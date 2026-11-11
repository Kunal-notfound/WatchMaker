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
from .build import BuildResult, build, slugify
from .modules import MODULES, QUARTZ_MODULES
from .nlp import describe, ideas, parse, preset_prompt
from .parts import build_scene
from .schema import ESSENTIAL, SCHEMA, Spec, new_spec
from .solver import solve
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

    def _api_get(self, route: str, query: dict) -> None:
        if route == "/api/state":
            self._json(200, STUDIO.snapshot())
        elif route == "/api/schema":
            self._json(200, _schema_payload())
        elif route == "/api/scene":
            with STUDIO.lock:
                parts = [p.to_viewer() for p in STUDIO.scene.get("parts", [])]
                payload = {"parts": parts,
                           "tree": STUDIO.scene.get("tree", {}),
                           "mass_g": STUDIO.scene.get("mass_g", 0),
                           "bounds": STUDIO.scene.get("bounds", {}),
                           "triangles": STUDIO.scene.get("triangles", 0)}
            self._json(200, payload)
        elif route == "/api/builds":
            self._json(200, STUDIO.last_build.to_dict() if STUDIO.last_build else {"build": None})
        elif route == "/api/download/file":
            self._download_file(query)
        elif route == "/api/download/zip":
            self._download_zip()
        else:
            self._error(404, f"unknown endpoint {route}")

    def _api_post(self, route: str, body: dict) -> None:
        if route == "/api/prompt":
            prompt = str(body.get("prompt", ""))
            if not prompt.strip():
                self._error(400, "prompt is empty")
                return
            with STUDIO.lock:
                result = parse(prompt, base=STUDIO.solved,
                               use_llm=bool(body.get("use_llm", True)),
                               name=body.get("name") or None)
                STUDIO.spec = result.spec
                STUDIO.rebuild()
                payload = STUDIO.snapshot({
                    "patches": result.patches,
                    "questions": result.questions,
                    "notes": result.notes,
                    "engine": result.engine,
                    "applied": result.applied,
                    "skipped_locked": result.skipped,
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
            ok = STUDIO.set(path, body["value"], force=bool(body.get("force", False)))
            if not ok:
                self._error(409, f"{path} is locked")
                return
            self._json(200, STUDIO.snapshot())

        elif route == "/api/patch":
            ops = body.get("ops") or []
            if not isinstance(ops, list):
                self._error(400, "ops must be a list")
                return
            applied = STUDIO.patch(ops)
            self._json(200, STUDIO.snapshot({"applied": applied}))

        elif route == "/api/lock":
            path = str(body.get("path", ""))
            if not path:
                self._error(400, "path is required")
                return
            with STUDIO.lock:
                STUDIO.spec.lock(path, bool(body.get("locked", True)))
                STUDIO.rebuild()
                payload = STUDIO.snapshot()
            self._json(200, payload)

        elif route == "/api/spec":
            payload = body.get("spec", body)
            try:
                spec = Spec.from_dict(payload) if isinstance(payload, dict) else Spec.from_json(str(payload))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._error(400, f"invalid spec: {exc}")
                return
            with STUDIO.lock:
                STUDIO.spec = spec
                STUDIO.last_build = None
                STUDIO.rebuild()
                snapshot = STUDIO.snapshot()
            self._json(200, snapshot)

        elif route == "/api/reset":
            preset = body.get("preset")
            name = body.get("name") or "Untitled"
            if preset:
                prompt = preset_prompt(str(preset)) or f"{preset} watch"
                spec = parse(prompt, name=name).spec
            else:
                spec = new_spec(name)
            with STUDIO.lock:
                STUDIO.spec = spec
                STUDIO.last_build = None
                STUDIO.rebuild()
                payload = STUDIO.snapshot()
            self._json(200, payload)

        elif route == "/api/build":
            self._build(bool(body.get("per_part", True)), bool(body.get("force", False)))

        else:
            self._error(404, f"unknown endpoint {route}")

    # -- build and download ----------------------------------------------
    def _build(self, per_part: bool, force: bool) -> None:
        with STUDIO.lock:
            spec = STUDIO.solved
            slug = slugify(spec.get("meta.name") or "watch")
            checksum = (spec.get("derived.checksum") or "")[:8]
            out = BUILD_ROOT / f"{slug}-{checksum}"
            result = build(spec, out_dir=str(out), per_part=per_part, force=force,
                           scene=STUDIO.scene, checks=STUDIO.checks)
            result.files = [f for f in result.files if os.path.isfile(f)]
            STUDIO.last_build = result
            # fresh archive for the download button
            archive = out / "package.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in result.files:
                    if os.path.abspath(path) == os.path.abspath(archive):
                        continue
                    zf.write(path, os.path.relpath(path, out))
            payload = STUDIO.snapshot()
        self._json(200, payload)

    def _build_root(self) -> Path | None:
        with STUDIO.lock:
            if STUDIO.last_build is None:
                return None
            return Path(STUDIO.last_build.root).resolve()

    def _download_file(self, query: dict) -> None:
        root = self._build_root()
        if root is None:
            self._error(409, "build the package first")
            return
        rel = (query.get("path") or [""])[0]
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            self._error(403, "path escapes the build directory")
            return
        self._serve_file(target, target.name)

    def _download_zip(self) -> None:
        root = self._build_root()
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

def _schema_payload() -> dict:
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
        "starter": STUDIO.snapshot()["spec"],
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
