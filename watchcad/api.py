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
import mimetypes
import os
import shutil
import tempfile
import threading
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
from .llm import LLMConfig
from .modules import MODULES, QUARTZ_MODULES
from .nlp import describe, ideas, parse, preset_prompt
from .parts import build_scene
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


class Studio:
    """One spec plus its solved scene, guarded by a re-entrant lock."""

    def __init__(self, spec: Spec | None = None):
        self.lock = threading.RLock()
        self.spec = spec or _starter_spec()
        self.solved: Spec = self.spec
        self.report: list[dict] = []
        self.scene: dict = {}
        self.checks: list[dict] = []
        self.summary: dict = {}
        self.last_build: BuildResult | None = None
        self.rebuild()

    def rebuild(self) -> None:
        with self.lock:
            self.solved, self.report = solve(self.spec)
            self.scene = build_scene(self.solved)
            self.checks = verify(self.solved, self.scene)
            self.summary = summary(self.checks)

    def set(self, path: str, value, force: bool = False) -> bool:
        with self.lock:
            ok = self.spec.set(path, value, tag="user", note="edited in the app", force=force)
            if ok:
                self.rebuild()
            return ok

    def patch(self, ops: list[dict]) -> int:
        with self.lock:
            applied = 0
            for op in ops:
                kind = op.get("op", "replace")
                path = op.get("path", "")
                if not path:
                    continue
                if kind in ("add", "replace"):
                    if self.spec.set(path, op.get("value"), tag="user",
                                     note="patch from the app"):
                        applied += 1
                elif kind == "lock":
                    self.spec.lock(path, True)
                    applied += 1
                elif kind == "unlock":
                    self.spec.lock(path, False)
                    applied += 1
                elif kind == "remove":
                    parent, leaf = self.spec._parent(path)
                    parent.pop(leaf, None)
                    self.spec.provenance.pop(path, None)
                    applied += 1
            if applied:
                self.rebuild()
            return applied

    def snapshot(self, extra: dict | None = None) -> dict:
        with self.lock:
            data = {
                "spec": self.solved.to_dict(),
                "checks": self.checks,
                "summary": self.summary,
                "validation": self.solved.validate(),
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
    """Replace a fresh session's studio with a persisted specification."""
    try:
        spec = Spec.from_dict(payload)
    except (TypeError, ValueError, KeyError):
        return False
    with session.studio.lock:
        session.studio.spec = spec
        session.studio.last_build = None
        session.studio.rebuild()
    with session._lock:
        session.versions.clear()
        session.redo_stack.clear()
        session.take_checkpoint(label, "restore", force=True)
    return True


def _session_for_conversation(conversation: dict):
    """Resolve (and if necessary restore) the design session of a conversation."""
    sid = SESSIONS.normalize_id(conversation.get("design_session") or "default")
    existed = SESSIONS.has(sid)
    session = SESSIONS.get(sid)
    if not existed:
        stored = get_store().get_design(sid)
        spec = (stored or {}).get("spec") if isinstance(stored, dict) else None
        if isinstance(spec, dict) and spec.get("data"):
            _restore_design(session, spec, "Restored conversation design")
    return session


def _save_design(session, conversation_id: str = "", label: str = "") -> None:
    try:
        studio = session.studio
        get_store().save_design(
            session.session_id, _clean_spec_payload(studio.spec),
            checksum=str(studio.solved.get("derived.checksum") or ""),
            label=label, conversation_id=conversation_id or None)
    except Exception:  # noqa: BLE001 - persistence must not break an API call
        pass


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
    if not existed:
        stored = get_store().get_design(sid) if requested else None
        spec = (stored or {}).get("spec") if isinstance(stored, dict) else None
        if isinstance(spec, dict) and spec.get("data"):
            _restore_design(session, spec, "Restored conversation design")
        else:
            try:
                clone = source_session.studio.spec.copy()
                with session.studio.lock:
                    session.studio.spec = clone
                    session.studio.last_build = None
                    session.studio.rebuild()
                with session._lock:
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

    def _json(self, status: int, obj) -> None:
        try:
            body = _jsonable(obj).encode()
        except (ValueError, TypeError) as exc:
            status = 500
            body = _jsonable({"error": f"serialisation: {exc}"}).encode()
        self._send(status, body, "application/json; charset=utf-8")

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
        return SESSIONS.get(self._session_id(query, body))

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
        elif route == "/api/schema":
            self._json(200, _schema_payload(studio))
        elif route == "/api/scene":
            with studio.lock:
                parts = [p.to_viewer() for p in studio.scene.get("parts", [])]
                payload = {"parts": parts,
                           "tree": studio.scene.get("tree", {}),
                           "mass_g": studio.scene.get("mass_g", 0),
                           "bounds": studio.scene.get("bounds", {}),
                           "triangles": studio.scene.get("triangles", 0)}
            self._json(200, payload)
        elif route == "/api/model.glb":
            with studio.lock:
                parts = studio.scene.get("parts", [])
                name = studio.solved.get("meta.name") or "watch"
                data = mesh_kernel.to_glb(list(parts), str(name))
            self._send(200, data, "model/gltf-binary")
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
        if route == "/api/optimise":
            try:
                budget = int(body.get("budget") or 9)
            except (TypeError, ValueError):
                budget = 9
            with studio.lock:
                result = optimiser.run(
                    studio.solved,
                    str(body.get("objective") or "weight"),
                    budget)
            self._json(200, result)
            return

        if route == "/api/prompt":
            prompt = str(body.get("prompt", ""))
            if not prompt.strip():
                self._error(400, "prompt is empty")
                return
            with studio.lock:
                result = parse(prompt, base=studio.solved,
                               use_llm=bool(body.get("use_llm", True)),
                               name=body.get("name") or None)
                studio.spec = result.spec
                studio.rebuild()
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
            ok = studio.set(path, body["value"], force=bool(body.get("force", False)))
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
            applied = studio.patch(ops)
            if applied:
                session.checkpoint("Manual patch", "manual")
            self._json(200, studio.snapshot({"applied": applied}))

        elif route == "/api/lock":
            path = str(body.get("path", ""))
            if not path:
                self._error(400, "path is required")
                return
            with studio.lock:
                studio.spec.lock(path, bool(body.get("locked", True)))
                studio.rebuild()
                session.checkpoint(f"Lock {path}", "lock")
                payload = studio.snapshot()
            self._json(200, payload)

        elif route == "/api/spec":
            payload = body.get("spec", body)
            try:
                spec = Spec.from_dict(payload) if isinstance(payload, dict) else Spec.from_json(str(payload))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._error(400, f"invalid spec: {exc}")
                return
            with studio.lock:
                studio.spec = spec
                studio.last_build = None
                studio.rebuild()
                session.checkpoint("Loaded a specification", "manual")
                snapshot = studio.snapshot()
            self._json(200, snapshot)

        elif route == "/api/reset":
            preset = body.get("preset")
            name = body.get("name") or "Untitled"
            if preset:
                prompt = preset_prompt(str(preset)) or f"{preset} watch"
                spec = parse(prompt, name=name).spec
            else:
                spec = new_spec(name)
            with studio.lock:
                studio.spec = spec
                studio.last_build = None
                studio.rebuild()
                session.checkpoint("Reset the design", "manual", force=True)
                payload = studio.snapshot()
            self._json(200, payload)

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
                control=control)
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
                edit_message_id=str(body.get("edit_message_id") or ""))
            conversation = get_store().get_conversation(conversation_id) or conversation
            payload = result.to_dict()
            payload["conversation"] = conversation
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
