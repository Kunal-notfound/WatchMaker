"""Local HTTP API and web app.

The server is the same engine the CLI uses: every request mutates the shared
spec, then the deterministic solver, mesh builder and verifier run again.  The
browser never computes geometry and the LLM never computes geometry; the
canvas viewer only projects the triangles the server produced.

Run:

    python3 -m watchcad serve --port 8765

Endpoints (JSON unless noted):

    GET  /api/state           spec, checks, summary, derivation report
    GET  /api/schema          field registry, presets, complications, ideas
    GET  /api/scene           triangles for the software renderer
    GET  /api/builds          last build manifest
    POST /api/prompt          {prompt, use_llm} -> patches, questions, state
    POST /api/set             {path, value, force}
    POST /api/patch           {ops:[...]}
    POST /api/lock            {path, locked}
    POST /api/reset           {preset?, name?}
    POST /api/build           {per_part?} -> package files
    GET  /api/download/file   ?path=<relative path inside the build>
    GET  /api/download/zip    package as one archive

Conversation endpoints:

    GET  /api/conversations              ?q=<search>
    POST /api/conversations              {title?, design_session?}
    GET  /api/conversations/<id>         transcript, design snapshot, tool events
    POST /api/conversations/<id>/rename  {title}
    POST /api/conversations/<id>/delete
    POST /api/chat                       {conversation_id?, message, images?, stream?}
    POST /api/chat/stop                  {conversation_id?, run_id?}
    POST /api/chat/approve|reject        {conversation_id, token?}
    GET  /api/settings                   non-secret model/research settings
    POST /api/settings                   update non-secret settings
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import shutil
import tempfile
import threading
import time
import traceback
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import exporters
from . import mesh as mesh_kernel
from . import optimise as optimiser
from . import providers as provider_vault
from . import settings as app_settings
from .agent.chat import ConversationRuntime
from .agent.context import (
    AgentCancelled, AgentTimeout, RunControl, SessionManager,
    _clean_spec as _clean_spec_payload,
)
from .agent.evaluator import count_failures
from .agent.knowledge import categories as knowledge_categories
from .agent.knowledge import lookup as knowledge_lookup
from .agent.knowledge import summary as knowledge_summary
from .agent.orchestrator import AgentOrchestrator, apply_variant, normalize_mode
from .agent.planner import detect_intent, build_variants
from .agent.tools import tool_catalog
from .build import BuildResult, build, slugify
from .components import UnknownComponent, component_context
from .critic import critique
from .llm import LLMConfig
from .modules import MODULES, QUARTZ_MODULES
from . import movements as movement_db
from .mesh import validate_meshes
from .nlp import describe, ideas, parse, preset_prompt
from .parts import build_scene
from .revision import RenderGenerationError, RevisionConflict
from .schema import ESSENTIAL, SCHEMA, Spec, new_spec
from .solver import solve
from .store import ConversationStore, default_db_path, new_id as new_store_id
from .verify import summary, verify

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
BUILD_ROOT = Path(os.environ.get("WATCHCAD_BUILD_DIR",
                                 Path(__file__).resolve().parent.parent / "out"))
MAX_BODY = 4 * 1024 * 1024

# The field groups the form editor shows.  `derived` is read-only.
FORM_GROUPS = [g for g in ("case", "dial", "hands", "movement", "quartz", "meta")]

RENDER_LOG = logging.getLogger("watchcad.render")
RENDER_LOG.addHandler(logging.NullHandler())

RENDER_STAGES = (
    "modification_pending", "validating", "solving", "generating_geometry",
    "exporting_model", "updating_viewport", "rendered", "failed",
)


def _render_log(event: str, **fields) -> None:
    """Emit one structured, secret-free render pipeline event."""
    payload = {"event": event}
    payload.update(fields)
    RENDER_LOG.info("%s", json.dumps(payload, sort_keys=True, default=str))


class Studio:
    """One canonical spec plus its solved scene, guarded by a re-entrant lock.

    ``revision`` is the ordering token for the render pipeline.  It is not the
    spec's ``meta.version`` and not ``derived.checksum``: both of those travel
    with a spec document and can regress when history is restored.  Every
    successful ``commit()`` increments it exactly once, after validation,
    solving, geometry generation and verification have all succeeded.
    """

    def __init__(self, spec: Spec | None = None):
        self.lock = threading.RLock()
        self.spec = spec or _starter_spec()
        self.solved: Spec = self.spec
        self.report: list[dict] = []
        self.scene: dict = {}
        self.checks: list[dict] = []
        self.summary: dict = {}
        self.mesh_issues: list[dict] = []
        self.last_build: BuildResult | None = None
        self._scenes: dict[str, dict] = {}
        self._models: dict[tuple[int, str], bytes] = {}
        self.revision = 1
        self.generation = 0
        self.modification_id = "initial"
        self.last_component = ""
        self.render_status: dict = {
            "status": "rendered",
            "stage": "rendered",
            "revision": self.revision,
            "modification_id": self.modification_id,
            "component": "",
            "label": "Initial design",
            "error": "",
        }
        self.on_commit = None
        self.rebuild()

    # -- status / observability -------------------------------------------
    def _status(self, stage: str, *, revision: int | None = None,
                modification_id: str | None = None, component: str | None = None,
                label: str | None = None, error: str = "") -> None:
        previous = self.render_status or {}
        self.render_status = {
            "status": "failed" if stage == "failed" else stage,
            "stage": stage,
            "revision": self.revision if revision is None else revision,
            "modification_id": modification_id or previous.get("modification_id", ""),
            "component": component or previous.get("component", ""),
            "label": label or previous.get("label", ""),
            "error": error,
        }
        _render_log("stage", stage=stage, revision=self.render_status["revision"],
                    modification_id=self.render_status["modification_id"],
                    component=self.render_status["component"],
                    error=error)

    # -- derivation -------------------------------------------------------
    def _derive(self, spec: Spec, *, modification_id: str | None = None,
                component: str = "", label: str = "") -> dict:
        """Build every derived artifact locally; never mutate live state."""
        context = dict(modification_id=modification_id, component=component,
                       label=label)
        self._status("validating", **context)
        issues = [issue for issue in spec.validate()
                  if issue.get("level") == "error"]
        if issues:
            raise RenderGenerationError(
                "spec validation failed: " + "; ".join(
                    f"{row.get('path')}: {row.get('message')}" for row in issues[:3]))
        self._status("solving", **context)
        solved, report = solve(spec)
        self._status("generating_geometry", **context)
        scene = build_scene(solved, "high")
        self._status("exporting_model", **context)
        mesh_issues = validate_meshes(list(scene.get("parts", [])))
        if mesh_issues:
            from .mesh import MeshValidationError
            raise MeshValidationError("; ".join(
                f"{row['mesh']}: {row['kind']}" for row in mesh_issues[:4]))
        checks = verify(solved, scene)
        summary_data = summary(checks)
        return {"solved": solved, "report": report, "scene": scene,
                "checks": checks, "summary": summary_data,
                "mesh_issues": mesh_issues}

    def _install(self, candidate: Spec, built: dict) -> None:
        """Atomically publish derived artifacts produced from ``candidate``."""
        self.spec = candidate
        self.solved = built["solved"]
        self.report = built["report"]
        self._scenes = {}
        self._models = {}
        self.scene = built["scene"]
        self.checks = built["checks"]
        self.summary = built["summary"]
        self.mesh_issues = built["mesh_issues"]

    def rebuild(self) -> None:
        """Re-derive the current spec without changing its revision.

        Used for initialisation and compatibility.  Design changes always go
        through :meth:`commit` so they get a new revision and a modification id.
        """
        with self.lock:
            built = self._derive(self.spec)
            self._install(self.spec, built)
            self._status("rendered")

    def commit(self, candidate: Spec, *, expected_revision=None, label: str = "",
               component: str = "", modification_id: str | None = None) -> dict:
        """Validate, solve, build, verify and atomically install a new design.

        ``expected_revision`` is the fence for deferred work.  A stale agent run,
        approval or proposal never touches the live studio.  On any generation
        failure the previous valid spec, scene, checks and revision stay in
        place and the failure is recorded in ``render_status``.
        """
        if not isinstance(candidate, Spec):
            raise TypeError("commit expects a Spec")
        with self.lock:
            if expected_revision is not None:
                try:
                    expected = int(expected_revision)
                except (TypeError, ValueError) as exc:
                    raise RevisionConflict(expected_revision, self.revision) from exc
                if expected != self.revision:
                    self._status("failed", error="stale revision")
                    _render_log("revision_conflict", expected=expected,
                                current=self.revision, label=label)
                    raise RevisionConflict(expected, self.revision)

            mid = modification_id or secrets.token_hex(6)
            self._status("modification_pending", modification_id=mid,
                         component=component, label=label)
            _render_log("modification", revision=self.revision,
                        modification_id=mid, component=component, label=label)
            try:
                built = self._derive(candidate, modification_id=mid,
                                     component=component, label=label)
                self._status("updating_viewport", modification_id=mid,
                             component=component, label=label)
                self._install(candidate, built)
                # Only a successful generation may replace the live identity
                # carried by previously exported GLBs.
                self.modification_id = mid
                self.last_component = component or ""
            except Exception as exc:  # noqa: BLE001 - preserve last good model
                self._status("failed", modification_id=mid, component=component,
                             label=label, error=f"{type(exc).__name__}: {exc}")
                _render_log("generation_failed", revision=self.revision,
                            modification_id=mid, component=component,
                            error=f"{type(exc).__name__}: {exc}")
                if isinstance(exc, RenderGenerationError):
                    raise
                raise RenderGenerationError(str(exc)) from exc

            self.revision += 1
            self.last_build = None
            self._status("rendered", modification_id=mid, component=component,
                         label=label)
            checksum = str(self.solved.get("derived.checksum") or "")
            _render_log("rendered", revision=self.revision, checksum=checksum,
                        modification_id=mid, component=component, label=label)
            result = {
                "revision": self.revision,
                "checksum": checksum,
                "modification_id": mid,
                "component": component,
                "label": label,
                "committed": True,
                "persisted": False,
            }
            callback = self.on_commit
            if callable(callback):
                try:
                    callback(result)
                    result["persisted"] = True
                except Exception as exc:  # noqa: BLE001 - DB failure is not a render failure
                    result["persist_error"] = f"{type(exc).__name__}: {exc}"
                    _render_log("persist_failed", revision=self.revision,
                                modification_id=mid, error=result["persist_error"])
            return result

    def next_generation(self) -> int:
        """Return a monotonic token for one delivered model/scene payload."""
        with self.lock:
            self.generation += 1
            return self.generation

    def model_headers(self, generation: int, preview: bool = False, *,
                      session_id: str = "", checksum: str | None = None,
                      modification_id: str | None = None,
                      preview_id: str = "") -> dict:
        """Headers that bind an artifact to the design that produced it.

        A generation is only meaningful inside one design session, so the
        session id travels with every other identity field.  Previews carry
        their own checksum and token; they must never be mistaken for the live
        design at the same revision.
        """
        with self.lock:
            return {
                "X-Design-Revision": str(self.revision),
                "X-Design-Checksum": str(
                    checksum if checksum is not None
                    else self.solved.get("derived.checksum") or ""),
                "X-Model-Generation": str(generation),
                "X-Model-Preview": "true" if preview else "false",
                "X-Model-Preview-ID": str(preview_id or ""),
                "X-Modification-ID": str(modification_id or self.modification_id),
                "X-WatchCAD-Session": str(session_id or "default"),
            }

    def scene_for(self, quality: str = "high") -> dict:
        """Return the mesh scene for a render preset, cached by quality.

        Quality only changes triangle counts and texture detail.  Every
        dimension, mass and verification result is identical across presets, so
        the low preset is safe on an integrated GPU.
        """
        quality = str(quality or "high").lower()
        if quality not in ("low", "medium", "high"):
            quality = "high"
        if quality == "high" and self.scene:
            return self.scene
        if quality not in self._scenes:
            self._scenes[quality] = build_scene(self.solved, quality)
        return self._scenes[quality]

    def set(self, path: str, value, force: bool = False, *,
            expected_revision=None, component: str = "", label: str = "") -> bool:
        with self.lock:
            candidate = self.spec.copy()
            ok = candidate.set(path, value, tag="user", note="edited in the app",
                               force=force)
            if not ok:
                return False
            self.commit(candidate, expected_revision=expected_revision,
                        component=component, label=label or f"Set {path}")
            return True

    def patch(self, ops: list[dict], *, expected_revision=None,
              component: str = "", label: str = "Manual patch") -> int:
        with self.lock:
            candidate = self.spec.copy()
            applied = 0
            for op in ops:
                kind = op.get("op", "replace")
                path = op.get("path", "")
                if not path:
                    continue
                if kind in ("add", "replace"):
                    if candidate.set(path, op.get("value"), tag="user",
                                     note="patch from the app"):
                        applied += 1
                elif kind == "lock":
                    candidate.lock(path, True)
                    applied += 1
                elif kind == "unlock":
                    candidate.lock(path, False)
                    applied += 1
                elif kind == "remove":
                    parent, leaf = candidate._parent(path)
                    parent.pop(leaf, None)
                    candidate.provenance.pop(path, None)
                    applied += 1
            if applied:
                self.commit(candidate, expected_revision=expected_revision,
                            component=component, label=label)
            return applied

    def snapshot(self, extra: dict | None = None) -> dict:
        with self.lock:
            data = {
                "revision": self.revision,
                "generation": self.generation,
                "checksum": str(self.solved.get("derived.checksum") or ""),
                "modification_id": self.modification_id,
                "render": dict(self.render_status),
                "spec": self.solved.to_dict(),
                "checks": self.checks,
                "summary": self.summary,
                "validation": self.solved.validate(),
                "mesh_issues": list(self.mesh_issues),
                "report": self.report,
                "description": describe(self.solved),
                "scene": {
                    "parts": len(self.scene.get("parts", [])),
                    "triangles": self.scene.get("triangles", 0),
                    "mass_g": self.scene.get("mass_g", 0),
                    "bounds": self.scene.get("bounds", {}),
                    "tree": self.scene.get("tree", {}),
                },
            }
            if self.last_build is not None:
                data["build"] = self.last_build.to_dict()
            if extra:
                data.update(extra)
            return data


def _starter_spec() -> Spec:
    """A complete, passing example so the first page load is not empty."""
    result = parse("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
                   "with applied lume indices, baton hands, sapphire crystal, "
                   "screw-down caseback and crown, date.", name="Abyss 42")
    return result.spec


STUDIO = Studio()

# The default session binds to the module-level ``STUDIO`` on every lookup, so
# tests (and embedders) can replace it.  Other sessions get their own studio.
SESSIONS = SessionManager(lambda: STUDIO, lambda: Studio())

# Short-lived, session-scoped candidate previews.  A preview holds a solved
# Spec copy only; it is never applied to the live studio.  Entries expire so an
# abandoned preview cannot hold a large scene in memory indefinitely.
OPTIMISE_PREVIEWS: dict[tuple[str, str], dict] = {}
OPTIMISE_PREVIEW_TTL_S = 900
_OPTIMISE_PREVIEW_LOCK = threading.RLock()

# Persistent conversations and designs.  The store is created lazily so that
# importing the API cannot create files as a side effect.  Tests may replace
# ``STORE`` with an isolated in-memory/temporary database.
STORE: ConversationStore | None = None
_STORE_LOCK = threading.RLock()

# Test/integration hooks: set these to a scripted client and config to run the
# conversational runtime without any network access.
LLM_CLIENT = None
LLM_CONFIG: LLMConfig | None = None


def get_store() -> ConversationStore:
    global STORE
    with _STORE_LOCK:
        if STORE is None:
            STORE = ConversationStore(default_db_path())
        return STORE


def _provider_security() -> dict:
    return {
        "storage": "process memory, per browser session",
        "persisted": False,
        "sessions_with_keys": provider_vault.VAULT.count(),
    }


def _conversation_config(session_id: str | None = None) -> LLMConfig:
    if LLM_CONFIG is not None:
        return LLM_CONFIG
    config = LLMConfig.from_env()
    if session_id:
        session_key = provider_vault.VAULT.get(session_id)
        if session_key:
            # Session-only BYOK key; never persisted or returned.
            config.key = session_key
    if LLM_CLIENT is not None and not config.available:
        # Injected clients are usable even when no environment endpoint exists.
        return LLMConfig(url="mock://injected", model="scripted",
                         provider="injected")
    return config


def _restore_design(session, payload: dict, label: str = "Restored design") -> bool:
    """Install a persisted specification as a new committed revision."""
    try:
        spec = Spec.from_dict(payload)
    except (TypeError, ValueError, KeyError):
        return False
    with session._lock:
        session.studio.commit(spec, label=label, component="restore")
        session.versions.clear()
        session.redo_stack.clear()
        session.take_checkpoint(label, "restore", force=True)
    return True


def _session_for_conversation(conversation: dict):
    """Resolve (and if necessary restore) the design session of a conversation."""
    sid = SESSIONS.normalize_id(conversation.get("design_session") or "default")
    existed = SESSIONS.has(sid)
    session = SESSIONS.get(sid)
    _bind_commit(session)
    if not existed:
        stored = get_store().get_design(sid)
        spec = (stored or {}).get("spec") if isinstance(stored, dict) else None
        if isinstance(spec, dict) and spec.get("data"):
            _restore_design(session, spec, "Restored conversation design")
    return session


def _save_design(session, conversation_id: str = "", label: str = "",
                 revision=None, checksum: str | None = None) -> None:
    try:
        studio = session.studio
        get_store().save_design(
            session.session_id, _clean_spec_payload(studio.spec),
            revision=revision if revision is not None else studio.revision,
            checksum=(checksum if checksum is not None else
                      str(studio.solved.get("derived.checksum") or "")),
            label=label, conversation_id=conversation_id or None)
    except Exception:  # noqa: BLE001 - persistence must not break an API call
        pass


def _bind_commit(session) -> None:
    """Persist every successful studio commit for this session."""
    studio = session.studio

    def persist(result: dict) -> None:
        _save_design(session, label=str(result.get("label") or "design change"),
                     revision=result.get("revision"),
                     checksum=str(result.get("checksum") or ""))

    studio.on_commit = persist


def _create_conversation(source_session, body: dict) -> tuple[dict, object]:
    """Create a conversation with its own design session.

    A new conversation clones the design the user is currently looking at (so
    it starts from real work), then diverges independently.  Supplying an
    explicit ``design_session`` reuses that design instead.
    """
    requested = str(body.get("design_session") or "").strip()
    sid = SESSIONS.normalize_id(requested or new_store_id("dsn"))
    existed = SESSIONS.has(sid)
    session = SESSIONS.get(sid)
    _bind_commit(session)
    if not existed:
        stored = get_store().get_design(sid) if requested else None
        spec = (stored or {}).get("spec") if isinstance(stored, dict) else None
        if isinstance(spec, dict) and spec.get("data"):
            _restore_design(session, spec, "Restored conversation design")
        else:
            try:
                # A cloned conversation starts from the design the browser is
                # already displaying.  Carry its revision forward so the new
                # studio cannot look older than the model still in the viewport.
                with source_session._lock:
                    clone = source_session.studio.spec.copy()
                    source_revision = int(source_session.studio.revision)
                with session._lock:
                    session.studio.revision = max(
                        int(session.studio.revision), source_revision)
                    # Model generations must move forward too: the viewer
                    # rejects a lower generation as an older model.
                    with source_session._lock:
                        source_generation = int(source_session.studio.generation)
                    session.studio.generation = max(
                        int(session.studio.generation), source_generation)
                    session.studio.commit(clone, label="Conversation started",
                                          component="conversation")
                    session.versions.clear()
                    session.redo_stack.clear()
                    session.take_checkpoint("Conversation started", "initial",
                                            force=True)
            except Exception:  # noqa: BLE001 - fall back to the starter design
                pass
    title = str(body.get("title") or "").strip()
    conversation = get_store().create_conversation(
        title=title or "New conversation", design_session=sid)
    _save_design(session, conversation["id"], "conversation start")
    return conversation, session


def _conversation_payload(conversation: dict, session, *,
                          include_snapshot: bool = False) -> dict:
    store = get_store()
    cid = conversation["id"]
    payload = {
        "conversation": conversation,
        "messages": store.messages(cid),
        "tool_events": store.tool_events(cid, 40),
        "design_refs": store.design_refs(cid, 20),
        "pending": session.pending,
        "session": session.conversation_payload(),
        "design": {
            "session_id": session.session_id,
            "checksum": str(session.studio.solved.get("derived.checksum") or ""),
        },
    }
    if include_snapshot:
        payload["snapshot"] = session.studio.snapshot()
    return payload


def _conversation_route_ids(route: str) -> tuple[str, list[str]]:
    parts = [part for part in route.split("/") if part]
    # ["api", "conversations", "<id>", ...]
    if len(parts) < 3:
        return "", []
    return urllib.parse.unquote(parts[2]), parts[3:]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _jsonable(obj):
    return json.dumps(obj, default=str, allow_nan=False)


class Handler(BaseHTTPRequestHandler):
    server_version = "watchcad/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        if os.environ.get("WATCHCAD_HTTP_LOG"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str,
              headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, obj, headers: dict | None = None) -> None:
        try:
            body = _jsonable(obj).encode()
        except (ValueError, TypeError) as exc:
            status = 500
            body = _jsonable({"error": f"serialisation: {exc}"}).encode()
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON: {exc}")
        if not isinstance(obj, dict):
            raise ValueError("JSON body must be an object")
        return obj

    def _serve_file(self, path: Path, download_name: str | None = None) -> None:
        if not path.is_file():
            self._error(404, "file not found")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".gltf":
            ctype = "model/gltf+json"
        headers = {}
        if download_name:
            headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
        self._send(200, path.read_bytes(), ctype, headers)

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if route.startswith("/api/"):
                self._api_get(route, query)
            else:
                self._static(route)
        except RevisionConflict as exc:
            self._error(409, str(exc))
        except RenderGenerationError as exc:
            self._error(422, str(exc))
        except Exception as exc:  # noqa: BLE001 - report, do not kill the server
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        route = parsed.path
        try:
            if not route.startswith("/api/"):
                self._error(404, "not an API route")
                return
            body = self._body()
            self._api_post(route, body)
        except ValueError as exc:
            self._error(400, str(exc))
        except RevisionConflict as exc:
            self._error(409, str(exc))
        except RenderGenerationError as exc:
            self._error(422, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    # -- sessions ---------------------------------------------------------
    def _session_id(self, query: dict | None = None, body: dict | None = None) -> str:
        value = self.headers.get("X-WatchCAD-Session") or ""
        if not value and query:
            value = (query.get("session") or [""])[0]
        if not value and body:
            value = str(body.get("session_id") or "")
        return value or "default"

    def _session(self, query: dict | None = None, body: dict | None = None):
        session = SESSIONS.get(self._session_id(query, body))
        _bind_commit(session)
        return session

    def _store_preview(self, session_id: str, spec, *, base_revision: int) -> str:
        token = secrets.token_hex(12)
        with _OPTIMISE_PREVIEW_LOCK:
            now = time.time()
            for key in list(OPTIMISE_PREVIEWS):
                if now - OPTIMISE_PREVIEWS[key]["at"] > OPTIMISE_PREVIEW_TTL_S:
                    OPTIMISE_PREVIEWS.pop(key, None)
            OPTIMISE_PREVIEWS[(session_id, token)] = {
                "spec": spec.copy(), "at": now,
                "base_revision": int(base_revision),
            }
        return token

    def _preview_entry(self, query: dict | None = None, body: dict | None = None):
        token = ""
        if query:
            token = str((query.get("preview") or [""])[0] or "")
        if not token and body:
            token = str(body.get("preview") or "")
        if not token:
            return None
        key = (self._session_id(query, body), token)
        with _OPTIMISE_PREVIEW_LOCK:
            entry = OPTIMISE_PREVIEWS.get(key)
            if entry is None:
                return None
            if time.time() - entry["at"] > OPTIMISE_PREVIEW_TTL_S:
                OPTIMISE_PREVIEWS.pop(key, None)
                return None
            return entry

    def _preview_spec(self, query: dict | None = None, body: dict | None = None):
        entry = self._preview_entry(query, body)
        return entry["spec"] if entry else None

    def _expected_revision(self, body: dict, query: dict | None = None):
        raw = body.get("expected_revision")
        if raw in (None, ""):
            raw = None
        if raw is None and query:
            raw = (query.get("revision") or [""])[0]
        if raw in (None, ""):
            return None
        return raw

    def _api_get(self, route: str, query: dict) -> None:
        session = self._session(query)
        studio = session.studio
        if route == "/api/conversations":
            search = (query.get("q") or [""])[0]
            self._json(200, {"conversations": get_store().list_conversations(search),
                             "current": session.session_id})
            return
        if route.startswith("/api/conversations/"):
            self._conversation_get(route)
            return
        if route == "/api/settings":
            self._json(200, self._settings_payload(session.session_id))
            return
        if route == "/api/providers":
            config = _conversation_config(session.session_id)
            self._json(200, {
                "providers": provider_vault.catalog(),
                "settings": app_settings.public_settings(),
                "active": dict(config.public_dict(),
                               session_key=provider_vault.VAULT.has(
                                   session.session_id)),
                "capabilities": provider_vault.preset_capabilities(
                    config.provider, config.model),
                "security": _provider_security(),
            })
            return
        if route == "/api/state":
            payload = studio.snapshot({"session": session.conversation_payload(),
                                       "agent": session.last_agent})
            self._json(200, payload)
        elif route == "/api/render/status":
            with studio.lock:
                payload = dict(studio.render_status)
                payload.update({
                    "revision": studio.revision,
                    "generation": studio.generation,
                    "checksum": str(studio.solved.get("derived.checksum") or ""),
                    "modification_id": studio.modification_id,
                })
            self._json(200, payload)
        elif route == "/api/component/context":
            selection = (query.get("component") or query.get("part")
                         or query.get("name") or [""])[0]
            try:
                self._json(200, component_context(studio, selection))
            except UnknownComponent as exc:
                self._error(400, str(exc))
        elif route == "/api/critic":
            selection = (query.get("component") or query.get("part")
                         or query.get("name") or [""])[0]
            try:
                self._json(200, critique(studio, selection or None))
            except UnknownComponent as exc:
                self._error(400, str(exc))
        elif route == "/api/schema":
            self._json(200, _schema_payload(studio))
        elif route == "/api/movements":
            def number(name):
                raw = (query.get(name) or [""])[0]
                try:
                    return float(raw) if raw not in ("", None) else None
                except ValueError:
                    return None
            catalog = movement_db.catalog()
            catalog["movements"] = movement_db.search(
                (query.get("q") or [""])[0],
                maker=(query.get("maker") or [""])[0],
                type=(query.get("type") or [""])[0],
                min_diameter=number("min_diameter"),
                max_diameter=number("max_diameter"),
                min_thickness=number("min_thickness"),
                max_thickness=number("max_thickness"),
                include_unknown=bool((query.get("include_unknown") or [""])[0]))
            catalog["filters"] = {key: (query.get(key) or [""])[0]
                                  for key in ("q", "maker", "type", "min_diameter",
                                              "max_diameter", "min_thickness", "max_thickness")}
            self._json(200, catalog)
        elif route == "/api/movements/detail":
            movement_id = (query.get("id") or [""])[0]
            record = movement_db.get_record(movement_id)
            if record is None:
                self._error(404, f"unknown movement {movement_id!r}")
                return
            with studio.lock:
                solved = studio.solved
                self._json(200, {
                    "record": record,
                    "provenance": movement_db.field_provenance(record),
                    "missing": movement_db.missing_fields(record),
                    "compatibility": movement_db.compatibility(solved, record),
                    "envelope": movement_db.envelope(solved, record),
                    "current": {
                        "movement": solved.get("movement.base_calibre"),
                        "calibre_id": solved.get("movement.calibre_id"),
                        "case_diameter_mm": solved.get("case.diameter_mm"),
                        "case_thickness_mm": solved.get("case.thickness_mm"),
                        "checksum": solved.get("derived.checksum"),
                    },
                    "sources": record.get("sources_full", []),
                    "schema_version": movement_db.MOVEMENT_SCHEMA_VERSION,
                })
        elif route in ("/api/scene", "/api/model.glb"):
            quality = (query.get("quality") or ["high"])[0]
            requested_revision = (query.get("revision") or [""])[0]
            session_id = self._session_id(query)
            preview_token = str((query.get("preview") or [""])[0] or "")
            entry = self._preview_entry(query)
            preview = entry["spec"] if entry else None
            preview_id = preview_token if preview is not None else ""
            with studio.lock:
                if requested_revision not in ("", None):
                    try:
                        wanted = int(requested_revision)
                    except (TypeError, ValueError) as exc:
                        raise RevisionConflict(requested_revision, studio.revision) from exc
                    if wanted != studio.revision:
                        raise RevisionConflict(wanted, studio.revision)
                if entry and int(entry.get("base_revision") or 0) != studio.revision:
                    raise RevisionConflict(entry.get("base_revision"), studio.revision)
                if preview is not None:
                    scene = build_scene(preview, quality)
                    name = preview.get("meta.name") or "watch"
                    checksum = str(preview.get("derived.checksum") or "")
                else:
                    scene = studio.scene_for(quality)
                    name = studio.solved.get("meta.name") or "watch"
                    checksum = str(studio.solved.get("derived.checksum") or "")
                generation = studio.next_generation()
                headers = studio.model_headers(
                    generation, preview=preview is not None,
                    session_id=session_id, checksum=checksum,
                    preview_id=preview_id,
                )
                if route == "/api/scene":
                    parts = [p.to_viewer() for p in scene.get("parts", [])]
                    payload = {"parts": parts,
                               "tree": scene.get("tree", {}),
                               "mass_g": scene.get("mass_g", 0),
                               "bounds": scene.get("bounds", {}),
                               "triangles": scene.get("triangles", 0),
                               "quality": scene.get("quality", "high"),
                               "preview": preview is not None,
                               "revision": studio.revision,
                               "generation": generation,
                               "checksum": checksum,
                               "session_id": session_id,
                               "modification_id": studio.modification_id,
                               "preview_id": preview_id}
                    self._json(200, payload, headers)
                    return
                # Preview specs differ from the live spec at the same revision.
                # Including the token prevents a preview from reusing the
                # baseline GLB (or another preview's GLB) from the same cache.
                key = (studio.revision, quality, preview_token)
                data = studio._models.get(key)
                cached = data is not None
                if data is None:
                    model_extras = {"watchcad": {
                        "session_id": session_id,
                        "revision": studio.revision,
                        "checksum": checksum,
                        "modification_id": studio.modification_id,
                        "preview": preview is not None,
                        "preview_id": preview_id,
                    }}
                    data = mesh_kernel.to_glb(
                        list(scene.get("parts", [])), str(name), extras=model_extras)
                    studio._models[key] = data
            _render_log("model_exported", revision=studio.revision,
                        generation=generation, preview=preview is not None,
                        quality=quality, bytes=len(data), cached=cached)
            self._send(200, data, "model/gltf-binary", headers)
        elif route == "/api/builds":
            self._json(200, studio.last_build.to_dict() if studio.last_build else {"build": None})
        elif route == "/api/session":
            self._json(200, session.to_dict())
        elif route == "/api/sessions":
            self._json(200, {"sessions": SESSIONS.list(),
                             "current": session.session_id})
        elif route == "/api/agent/tools":
            self._json(200, {"tools": tool_catalog(),
                             "knowledge": knowledge_summary(),
                             "knowledge_categories": knowledge_categories()})
        elif route == "/api/agent/history":
            payload = session.conversation_payload()
            payload["memory"] = session.memory.to_dict()
            payload["history"] = session.history(40)
            payload["pending"] = session.pending
            self._json(200, payload)
        elif route == "/api/agent/nlp":
            self._json(200, detect_intent((query.get("q") or [""])[0]).to_dict())
        elif route == "/api/agent/knowledge":
            q = (query.get("q") or [""])[0]
            self._json(200, knowledge_lookup(q, category=(query.get("category") or [None])[0]))
        elif route == "/api/agent/version":
            key = (query.get("id") or [""])[0]
            target = next((v for v in session.versions
                           if str(v.version_id) == key or str(v.index) == key), None)
            if target is None:
                self._error(404, f"unknown version {key}")
                return
            self._json(200, target.to_dict(include_spec=True))
        elif route == "/api/agent/variants":
            self._json(200, {"variants": build_variants(studio)})
        elif route == "/api/download/file":
            self._download_file(query)
        elif route == "/api/download/zip":
            self._download_zip(query)
        else:
            self._error(404, f"unknown endpoint {route}")

    def _api_post(self, route: str, body: dict) -> None:
        session = self._session(body=body)
        studio = session.studio

        if route in ("/api/chat", "/api/agent/run"):
            conversation_id = str(body.get("conversation_id") or "").strip()
            if conversation_id:
                conversation = get_store().get_conversation(conversation_id)
                if conversation is not None:
                    session = _session_for_conversation(conversation)
                    studio = session.studio
            expected = self._expected_revision(body)
            if expected is not None:
                with studio.lock:
                    try:
                        wanted = int(expected)
                    except (TypeError, ValueError) as exc:
                        raise RevisionConflict(expected, studio.revision) from exc
                    if wanted != studio.revision:
                        raise RevisionConflict(wanted, studio.revision)

        if route == "/api/conversations":
            conversation, target = _create_conversation(session, body)
            payload = _conversation_payload(conversation, target,
                                            include_snapshot=True)
            self._json(200, payload)
            return
        if route.startswith("/api/conversations/"):
            self._conversation_post(route, body)
            return
        if route == "/api/chat":
            self._chat(body)
            return
        if route == "/api/chat/stop":
            self._chat_stop(body)
            return
        if route in ("/api/chat/approve", "/api/chat/reject"):
            self._chat_approval(body, route.endswith("approve"))
            return
        if route == "/api/settings":
            patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
            app_settings.save_settings(patch)
            self._json(200, self._settings_payload(session.session_id))
            return
        if route == "/api/providers/save":
            provider = str(body.get("provider") or "openai-compatible").strip()
            preset = provider_vault.PROVIDERS.get(provider) \
                or provider_vault.PROVIDERS["openai-compatible"]
            endpoint = str(body.get("endpoint") or preset["url"]).strip()
            model = str(body.get("model") or preset["model"]).strip()
            vision = str(body.get("vision_model") or "").strip()
            if not provider_vault.endpoint_scheme_ok(endpoint):
                self._error(400, "endpoint must be http:// or https://")
                return
            app_settings.save_settings({
                "provider": provider,
                "url": endpoint,
                "model": model,
                "vision_model": vision,
            })
            key = str(body.get("key") or "").strip()
            if key:
                provider_vault.VAULT.set(session.session_id, key)
            payload = self._settings_payload(session.session_id)
            payload["providers"] = provider_vault.catalog()
            payload["active"] = dict(
                _conversation_config(session.session_id).public_dict(),
                session_key=provider_vault.VAULT.has(session.session_id))
            payload["capabilities"] = provider_vault.preset_capabilities(
                provider, model)
            payload["security"] = _provider_security()
            self._json(200, payload)
            return
        if route == "/api/providers/remove":
            provider_vault.VAULT.clear(session.session_id)
            payload = self._settings_payload(session.session_id)
            payload["active"] = dict(
                _conversation_config(session.session_id).public_dict(),
                session_key=False)
            payload["capabilities"] = provider_vault.preset_capabilities(
                _conversation_config(session.session_id).provider,
                _conversation_config(session.session_id).model)
            payload["security"] = _provider_security()
            self._json(200, payload)
            return
        if route == "/api/providers/test":
            result = provider_vault.probe(body, session.session_id)
            self._json(200, result)
            return
        if route == "/api/movements/propose":
            movement_id = str(body.get("id") or "")
            record = movement_db.get_record(movement_id)
            if record is None:
                self._error(404, f"unknown movement {movement_id!r}")
                return
            with studio.lock:
                proposal = movement_db.propose(studio.solved, movement_id)
                proposal["baseline"] = {
                    "checksum": studio.solved.get("derived.checksum"),
                    "name": studio.solved.get("meta.name"),
                }
            self._json(200, proposal)
            return

        if route == "/api/optimise":
            try:
                budget = int(body.get("budget") or 9)
            except (TypeError, ValueError):
                budget = 9
            axes = body.get("axes")
            if not isinstance(axes, list):
                axes = None
            with studio.lock:
                result = optimiser.run(
                    studio.solved,
                    str(body.get("objective") or "weight"),
                    budget,
                    axes=axes)
            self._json(200, result)
            return

        if route == "/api/optimise/preview":
            changes = body.get("changes")
            if not isinstance(changes, list):
                self._error(400, "preview needs a change list")
                return
            baseline = str(body.get("baseline_checksum") or "")
            with studio.lock:
                current = str(studio.solved.get("derived.checksum") or "")
                if baseline and baseline != current:
                    self._error(409, "design changed; rerun optimisation before previewing")
                    return
                try:
                    preview = optimiser.build_candidate(
                        studio.solved, changes, label=str(body.get("label") or "preview"))
                    if preview["error"]:
                        raise ValueError(preview["error"])
                    token = self._store_preview(
                        session.session_id, preview["spec"],
                        base_revision=studio.revision)
                except (ValueError, TypeError) as exc:
                    self._error(400, f"candidate could not be previewed: {exc}")
                    return
            self._json(200, {
                "preview": token,
                "revision": studio.revision,
                "checksum": str(preview["spec"].get("derived.checksum") or ""),
                "mass_g": float((preview["spec"].get("derived") or {}).get("mass_g") or 0),
                "failing_checks": len([c for c in preview["checks"]
                                       if c.get("passed") is False]),
            })
            return

        if route == "/api/prompt":
            prompt = str(body.get("prompt", ""))
            if not prompt.strip():
                self._error(400, "prompt is empty")
                return
            with studio.lock:
                base = studio.solved
            result = parse(prompt, base=base,
                           use_llm=bool(body.get("use_llm", True)),
                           name=body.get("name") or None)
            studio.commit(result.spec, expected_revision=self._expected_revision(body),
                          label="Prompt: " + prompt[:60], component="prompt")
            session.checkpoint("Prompt: " + prompt[:60], "prompt")
            payload = studio.snapshot({
                    "patches": result.patches,
                    "questions": result.questions,
                    "notes": result.notes,
                    "engine": result.engine,
                    "applied": result.applied,
                    "skipped_locked": result.skipped,
                    "session": session.conversation_payload(),
                })
            self._json(200, payload)

        elif route == "/api/set":
            path = str(body.get("path", ""))
            if not path:
                self._error(400, "path is required")
                return
            if "value" not in body:
                self._error(400, "value is required")
                return
            expected = self._expected_revision(body)
            ok = studio.set(path, body["value"], force=bool(body.get("force", False)),
                            expected_revision=expected,
                            component=path.split(".")[0],
                            label=str(body.get("label") or f"Set {path}"))
            if not ok:
                self._error(409, f"{path} is locked")
                return
            session.checkpoint(f"Set {path}", "manual")
            self._json(200, studio.snapshot())

        elif route == "/api/patch":
            ops = body.get("ops") or []
            if not isinstance(ops, list):
                self._error(400, "ops must be a list")
                return
            applied = studio.patch(ops, expected_revision=self._expected_revision(body),
                                   component=str(body.get("component") or ""),
                                   label=str(body.get("label") or "Manual patch"))
            if applied:
                session.checkpoint("Manual patch", "manual")
            self._json(200, studio.snapshot({"applied": applied}))

        elif route == "/api/lock":
            path = str(body.get("path", ""))
            if not path:
                self._error(400, "path is required")
                return
            with studio.lock:
                candidate = studio.spec.copy()
            candidate.lock(path, bool(body.get("locked", True)))
            studio.commit(candidate, expected_revision=self._expected_revision(body),
                          label=f"Lock {path}", component=path.split(".")[0])
            session.checkpoint(f"Lock {path}", "lock")
            self._json(200, studio.snapshot())

        elif route == "/api/spec":
            payload = body.get("spec", body)
            try:
                spec = Spec.from_dict(payload) if isinstance(payload, dict) else Spec.from_json(str(payload))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._error(400, f"invalid spec: {exc}")
                return
            studio.commit(spec, expected_revision=self._expected_revision(body),
                          label="Loaded a specification", component="spec")
            session.checkpoint("Loaded a specification", "manual")
            self._json(200, studio.snapshot())

        elif route == "/api/reset":
            preset = body.get("preset")
            name = body.get("name") or "Untitled"
            if preset:
                prompt = preset_prompt(str(preset)) or f"{preset} watch"
                spec = parse(prompt, name=name).spec
            else:
                spec = new_spec(name)
            studio.commit(spec, expected_revision=self._expected_revision(body),
                          label="Reset the design", component="reset")
            session.checkpoint("Reset the design", "manual", force=True)
            self._json(200, studio.snapshot())

        elif route == "/api/build":
            self._build(session, bool(body.get("per_part", True)),
                        bool(body.get("force", False)))

        # -- agent --------------------------------------------------------
        elif route == "/api/agent/run":
            self._agent_run(session, body)
        elif route == "/api/agent/approve":
            self._agent_approve(session, body)
        elif route == "/api/agent/reject":
            self._agent_reject(session, body)
        elif route == "/api/agent/cancel":
            self._agent_cancel(session, body)
        elif route == "/api/agent/undo":
            self._agent_history(session, "undo")
        elif route == "/api/agent/redo":
            self._agent_history(session, "redo")
        elif route == "/api/agent/variant":
            self._agent_variant(session, body)
        elif route == "/api/session/new":
            created = SESSIONS.create(str(body.get("title") or ""))
            self._json(200, created.to_dict())
        elif route == "/api/session/drop":
            dropped = SESSIONS.drop(session.session_id)
            self._json(200, {"dropped": dropped,
                             "sessions": SESSIONS.list()})
        else:
            self._error(404, f"unknown endpoint {route}")

    # -- agent -----------------------------------------------------------
    def _agent_run(self, session, body: dict) -> None:
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            self._error(400, "prompt is empty")
            return
        mode = normalize_mode(body.get("mode") or body.get("autonomy"))
        control = RunControl(timeout_s=float(body.get("timeout_s") or 150))
        session.runs[control.run_id] = control
        stream = bool(body.get("stream", False))
        write = self._start_sse(control) if stream else None
        if write:
            write({"type": "start", "run_id": control.run_id,
                   "session_id": session.session_id, "mode": mode,
                   "intent": detect_intent(prompt).name})
        try:
            orchestrator = AgentOrchestrator(
                session, build_root=str(BUILD_ROOT),
                llm_config=_conversation_config(session.session_id))
            result = orchestrator.run(
                prompt, mode=mode,
                use_llm=body.get("use_llm") if "use_llm" in body else None,
                max_iterations=int(body.get("max_iterations") or 3),
                max_tool_rounds=int(body.get("max_tool_rounds") or 8),
                emit=(lambda event: write({"type": "timeline", "event": event}))
                if write else None,
                control=control,
                focus=body.get("component") or body.get("part") or "")
            payload = result.to_dict()
            payload["snapshot"] = session.studio.snapshot()
            payload["session"] = session.conversation_payload()
            payload["pending"] = session.pending
            if write:
                write({"type": "result", "result": payload})
            else:
                self._json(200, payload)
        except AgentCancelled as exc:
            payload = {"status": "cancelled", "error": str(exc),
                       "response": "Stopped. No changes were committed."}
            if write:
                write({"type": "error", "error": payload})
            else:
                self._error(409, str(exc))
        except AgentTimeout as exc:
            payload = {"status": "timeout", "error": str(exc),
                       "response": "The run exceeded its time budget."}
            if write:
                write({"type": "error", "error": payload})
            else:
                self._error(504, str(exc))
        finally:
            session.runs.pop(control.run_id, None)
            if write:
                self._end_sse()

    def _agent_approve(self, session, body: dict) -> None:
        token = str(body.get("token") or "")
        try:
            pending = session.approve(token)
        except KeyError as exc:
            self._error(409, str(exc))
            return
        except ValueError as exc:
            self._error(409, str(exc))
            return
        payload = session.studio.snapshot({
            "approved": True,
            "request": pending.get("request"),
            "changes": pending.get("ops", []),
            "agent": pending.get("agent"),
            "session": session.conversation_payload(),
        })
        self._json(200, payload)

    def _agent_reject(self, session, body: dict) -> None:
        token = str(body.get("token") or "")
        try:
            pending = session.reject(token or None)
        except KeyError as exc:
            self._error(409, str(exc))
            return
        except ValueError as exc:
            self._error(409, str(exc))
            return
        payload = session.studio.snapshot({
            "rejected": True,
            "request": pending.get("request"),
            "session": session.conversation_payload(),
        })
        self._json(200, payload)

    def _agent_cancel(self, session, body: dict) -> None:
        run_id = str(body.get("run_id") or "")
        cancelled = []
        for key, control in list(session.runs.items()):
            if run_id and key != run_id:
                continue
            control.cancel()
            cancelled.append(key)
        self._json(200, {"cancelled": cancelled,
                         "session_id": session.session_id})

    def _agent_history(self, session, action: str) -> None:
        target = session.undo() if action == "undo" else session.redo()
        if target is None:
            self._error(409, f"nothing to {action}")
            return
        payload = session.studio.snapshot({
            "version": target.to_dict(),
            "session": session.conversation_payload(),
        })
        self._json(200, payload)

    def _agent_variant(self, session, body: dict) -> None:
        variant = body.get("variant")
        if not isinstance(variant, dict):
            variant = {"label": body.get("label") or "Selected alternative",
                       "changes": body.get("changes") or []}
        if not variant.get("changes") and not variant.get("spec"):
            self._error(400, "variant needs changes or a spec")
            return
        mode = normalize_mode(body.get("mode") or "autonomous")
        result = apply_variant(session, variant, mode=mode,
                               emit=None)
        payload = result.to_dict()
        payload["snapshot"] = session.studio.snapshot()
        payload["session"] = session.conversation_payload()
        payload["pending"] = session.pending
        self._json(200, payload)

    # -- conversations ---------------------------------------------------
    def _settings_payload(self, session_id: str | None = None) -> dict:
        config = _conversation_config(session_id)
        return {
            "settings": app_settings.public_settings(),
            "llm": config.public_dict(),
            "session_key": provider_vault.VAULT.has(session_id) if session_id else False,
            "research": {"configured": bool(config.search_url),
                         "provider": config.search_provider},
            "database": str(get_store().path),
        }

    def _conversation_get(self, route: str) -> None:
        conversation_id, action = _conversation_route_ids(route)
        conversation = get_store().get_conversation(conversation_id)
        if conversation is None:
            self._error(404, f"unknown conversation {conversation_id}")
            return
        session = _session_for_conversation(conversation)
        if not action:
            self._json(200, _conversation_payload(conversation, session,
                                                  include_snapshot=True))
        elif action == ["messages"]:
            self._json(200, {"messages": get_store().messages(conversation_id)})
        elif action == ["tool-events"]:
            self._json(200, {"tool_events": get_store().tool_events(conversation_id)})
        else:
            self._error(404, f"unknown conversation endpoint {route}")

    def _conversation_post(self, route: str, body: dict) -> None:
        conversation_id, action = _conversation_route_ids(route)
        conversation = get_store().get_conversation(conversation_id)
        if conversation is None:
            self._error(404, f"unknown conversation {conversation_id}")
            return
        session = _session_for_conversation(conversation)

        if action == ["rename"]:
            title = str(body.get("title") or "").strip()
            if not title:
                self._error(400, "title is required")
                return
            conversation = get_store().rename_conversation(conversation_id, title) \
                or conversation
            self._json(200, {"conversation": conversation})
            return
        if action == ["delete"]:
            deleted = get_store().delete_conversation(conversation_id)
            self._json(200, {"deleted": deleted, "conversation_id": conversation_id})
            return
        if len(action) == 3 and action[0] == "messages" and action[2] == "delete":
            deleted = get_store().delete_message(action[1])
            self._json(200, {"deleted": deleted,
                             "messages": get_store().messages(conversation_id)})
            return
        self._error(404, f"unknown conversation endpoint {route}")

    def _chat(self, body: dict) -> None:
        conversation_id = str(body.get("conversation_id") or "").strip()
        source_session = self._session(body=body)
        if conversation_id:
            conversation = get_store().get_conversation(conversation_id)
            if conversation is None:
                self._error(404, f"unknown conversation {conversation_id}")
                return
            session = _session_for_conversation(conversation)
        else:
            conversation, session = _create_conversation(source_session, body)
            conversation_id = conversation["id"]

        message = str(body.get("message") or body.get("prompt") or "")
        images = body.get("images") or []
        if isinstance(images, str):
            images = [images]
        if not isinstance(images, list):
            images = []
        mode = normalize_mode(body.get("mode") or body.get("autonomy") or "safe")
        control = RunControl(timeout_s=float(body.get("timeout_s") or 180))
        session.runs[control.run_id] = control
        stream = bool(body.get("stream", False))
        write = self._start_sse(control) if stream else None
        try:
            runtime = ConversationRuntime(
                session, get_store(), conversation_id,
                llm_client=LLM_CLIENT,
                llm_config=_conversation_config(session.session_id),
                build_root=str(BUILD_ROOT))
            result = runtime.run(
                message, images=[str(item) for item in images],
                mode=mode,
                use_llm=(body.get("use_llm") if "use_llm" in body else None),
                max_tool_rounds=int(body.get("max_tool_rounds") or 12),
                emit=write, control=control,
                regenerate=bool(body.get("regenerate", False)),
                edit_message_id=str(body.get("edit_message_id") or ""),
                focus=body.get("component") or body.get("part") or "")
            conversation = get_store().get_conversation(conversation_id) or conversation
            payload = result.to_dict()
            # Keep the revisioned snapshot from the runtime.  Conversation
            # metadata is additive and must not replace the state payload the
            # browser uses to decide whether the design actually changed.
            snapshot = payload.get("snapshot") or session.studio.snapshot()
            payload.update({
                "snapshot": snapshot,
                "revision": snapshot.get("revision"),
                "checksum": snapshot.get("checksum"),
                "conversation": conversation,
            })
            payload["messages"] = get_store().messages(conversation_id)
            payload["tool_events"] = get_store().tool_events(conversation_id, 40)
            payload["session"] = session.conversation_payload()
            payload["design"] = {
                "session_id": session.session_id,
                "checksum": str(session.studio.solved.get("derived.checksum") or ""),
            }
            if write:
                write({"type": "result", "result": payload})
            else:
                self._json(200, payload)
        finally:
            session.runs.pop(control.run_id, None)
            if write:
                self._end_sse()

    def _chat_stop(self, body: dict) -> None:
        conversation_id = str(body.get("conversation_id") or "")
        run_id = str(body.get("run_id") or "")
        controls = []
        if conversation_id:
            conversation = get_store().get_conversation(conversation_id)
            if conversation:
                controls = SESSIONS.controls(conversation.get("design_session"))
        if not controls:
            controls = SESSIONS.controls(body.get("session_id")
                                         or self._session_id(body=body))
        cancelled = []
        for control in controls:
            if run_id and control.run_id != run_id:
                continue
            control.cancel()
            cancelled.append(control.run_id)
        self._json(200, {"cancelled": cancelled,
                         "conversation_id": conversation_id})

    def _chat_approval(self, body: dict, approve: bool) -> None:
        conversation_id = str(body.get("conversation_id") or "").strip()
        conversation = get_store().get_conversation(conversation_id)
        if conversation is None:
            self._error(404, f"unknown conversation {conversation_id}")
            return
        session = _session_for_conversation(conversation)
        token = str(body.get("token") or "")
        try:
            if approve:
                if not token and session.pending:
                    token = str(session.pending.get("token") or "")
                pending = session.approve(token)
                _save_design(session, conversation_id, "approved change")
                checks = session.studio.checks or []
                after = summary(checks)
                changes = pending.get("ops") or []
                text = (f"Applied {len(changes)} approved change(s) through the "
                        f"validated pipeline. Verification: "
                        f"{after.get('fail', 0)} failing check(s).")
                get_store().add_message(
                    conversation_id, "assistant", text, kind="assistant",
                    data={"engine": "local rules", "status": "complete",
                          "approved": True, "changes": changes,
                          "verification": after})
            else:
                pending = session.reject(token or None)
                get_store().add_message(
                    conversation_id, "assistant",
                    "I left the design unchanged; the staged change was discarded.",
                    kind="assistant",
                    data={"engine": "local rules", "status": "rejected"})
        except KeyError as exc:
            self._error(409, str(exc))
            return
        except ValueError as exc:
            self._error(409, str(exc))
            return
        conversation = get_store().get_conversation(conversation_id) or conversation
        payload = {
            "approved": approve,
            "request": pending.get("request"),
            "changes": pending.get("ops") or [],
            "snapshot": session.studio.snapshot(),
            "session": session.conversation_payload(),
            "conversation": conversation,
            "messages": get_store().messages(conversation_id),
        }
        self._json(200, payload)

    # -- streaming -------------------------------------------------------
    def _start_sse(self, control: RunControl):
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            control.cancel()
            return None

        alive = {"ok": True}

        def write(event: dict) -> None:
            if not alive["ok"]:
                return
            try:
                data = _jsonable(event)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                alive["ok"] = False
                control.cancel()

        return write

    def _end_sse(self) -> None:
        try:
            self.wfile.write(b"event: end\ndata: {\"type\":\"end\"}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # -- build and download ----------------------------------------------
    def _build(self, session, per_part: bool, force: bool) -> None:
        studio = session.studio
        with studio.lock:
            spec = studio.solved
            slug = slugify(spec.get("meta.name") or "watch")
            checksum = (spec.get("derived.checksum") or "")[:8]
            out = BUILD_ROOT / session.session_id / f"{slug}-{checksum}"
            result = build(spec, out_dir=str(out), per_part=per_part, force=force,
                           scene=studio.scene, checks=studio.checks)
            result.files = [f for f in result.files if os.path.isfile(f)]
            studio.last_build = result
            # fresh archive for the download button
            archive = out / "package.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in result.files:
                    if os.path.abspath(path) == os.path.abspath(archive):
                        continue
                    zf.write(path, os.path.relpath(path, out))
            payload = studio.snapshot()
        self._json(200, payload)

    def _build_root(self, query: dict | None = None) -> Path | None:
        session = self._session(query)
        with session.studio.lock:
            if session.studio.last_build is None:
                return None
            return Path(session.studio.last_build.root).resolve()

    def _download_file(self, query: dict) -> None:
        root = self._build_root(query)
        if root is None:
            self._error(409, "build the package first")
            return
        rel = (query.get("path") or [""])[0]
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            self._error(403, "path escapes the build directory")
            return
        self._serve_file(target, target.name)

    def _download_zip(self, query: dict) -> None:
        root = self._build_root(query)
        if root is None:
            self._error(409, "build the package first")
            return
        archive = root / "package.zip"
        if not archive.is_file():
            self._error(404, "package.zip missing; rebuild")
            return
        self._serve_file(archive, f"{root.name}.zip")

    # -- static -----------------------------------------------------------
    def _static(self, route: str) -> None:
        if route in ("/", ""):
            route = "/index.html"
        target = (WEB_ROOT / route.lstrip("/")).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self._error(403, "outside the web root")
            return
        if not target.is_file():
            self._error(404, f"{route} not found")
            return
        self._serve_file(target)


# ---------------------------------------------------------------------------
# Schema payload
# ---------------------------------------------------------------------------

def _schema_payload(studio=None) -> dict:
    studio = studio or STUDIO
    groups = {}
    for group in FORM_GROUPS:
        fields = SCHEMA.get(group, {})
        groups[group] = [
            {"path": f"{group}.{name}", "name": name, **meta}
            for name, meta in fields.items()
        ]
    return {
        "groups": groups,
        "essentials": ESSENTIAL,
        "presets": ideas(),
        "ideas": ideas(),
        "complications": [
            {"id": k, "name": v["name"], "height_mm": v["height_mm"],
             "torque_uW": v["torque_uW"], "envelope_r": v["envelope_r"],
             "description": v["description"], "conflicts": v.get("conflicts", [])}
            for k, v in MODULES.items()
        ],
        "quartz_modules": [
            {"id": k, "width_mm": v["width_mm"], "height_mm": v["height_mm"]}
            for k, v in QUARTZ_MODULES.items()
        ],
        "starter": studio.snapshot()["spec"],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"watchcad studio on {url}")
    print("  API:  /api/state  /api/schema  /api/scene")
    print("  stop: Ctrl+C")
    if open_browser:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="watchcad-api", description="local watchcad web studio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)
    run(args.host, args.port, args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
