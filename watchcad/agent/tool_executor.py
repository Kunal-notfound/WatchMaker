"""Validated execution of agent tool calls.

Every model request passes through here.  Mutating tools run against an isolated
working copy, so the live design is only replaced when the orchestrator commits
after a successful solve and verification.  Arguments are checked against the
tool schemas before a handler is called, locked fields are refused, and the run
budget is enforced for every call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import threading
import time

from ..parts import build_scene
from ..schema import Spec
from ..solver import solve
from ..verify import summary as verify_summary
from ..verify import verify
from .models import TimelineEvent, ToolCall, ToolResult
from .tools import READ_ONLY, TOOL_SPECS, ToolContext, ToolDenied, ToolError, HANDLERS


class ShadowStudio:
    """A private, fully solved copy of a studio used for safe mutation."""

    def __init__(self, source):
        self.lock = threading.RLock()
        self.source = source
        self.spec = source.spec.copy()
        self.solved: Spec = self.spec
        self.report: list[dict] = []
        self.scene: dict = {}
        self.checks: list[dict] = []
        self.summary: dict = {}
        self.last_build = None
        self.rebuild()

    def rebuild(self) -> None:
        with self.lock:
            self.solved, self.report = solve(self.spec)
            self.scene = build_scene(self.solved)
            self.checks = verify(self.solved, self.scene)
            self.summary = verify_summary(self.checks)

    def set(self, path: str, value, force: bool = False) -> bool:
        with self.lock:
            ok = self.spec.set(path, value, tag="agent", note="agent change", force=force)
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
                    if self.spec.set(path, op.get("value"), tag="agent", note="agent patch"):
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


def _type_name(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _matches(schema_type, value) -> bool:
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    for kind in types:
        if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if kind == "boolean" and isinstance(value, bool):
            return True
        if kind == "string" and isinstance(value, str):
            return True
        if kind == "array" and isinstance(value, list):
            return True
        if kind == "object" and isinstance(value, dict):
            return True
        if kind == "null" and value is None:
            return True
    return False


def validate_arguments(spec, arguments: dict) -> tuple[dict, list[str]]:
    """Validate a tool call against its JSON schema; return clean arguments."""
    if not isinstance(arguments, dict):
        raise ToolError("tool arguments must be an object")
    schema = spec.parameters or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    clean = {}
    for key, value in arguments.items():
        if key in ("reason", "note") and key not in properties:
            continue
        if key not in properties:
            if schema.get("additionalProperties", True) is False:
                raise ToolError(f"unknown argument '{key}' for {spec.name}")
            clean[key] = value
            continue
        subschema = properties[key] or {}
        if "type" in subschema and not _matches(subschema["type"], value):
            raise ToolError(
                f"argument '{key}' for {spec.name} should be "
                f"{subschema['type']}, got {_type_name(value)}")
        if subschema.get("type") == "array" and subschema.get("items"):
            item_type = subschema["items"].get("type")
            if item_type:
                for item in value:
                    if not _matches(item_type, item):
                        raise ToolError(
                            f"every item in '{key}' must be {item_type}, "
                            f"got {_type_name(item)}")
        clean[key] = value
    missing = [key for key in required if key not in clean]
    if missing:
        raise ToolError(f"{spec.name} is missing argument(s): " + ", ".join(missing))
    return clean, []


@dataclass
class ToolExecutor:
    studio: object
    session: object = None
    emit: object = None
    control: object = None
    build_root: str | None = None
    approval_mode: str = "autonomous"
    max_calls: int = 80
    iteration: int = 0

    working: ShadowStudio | None = field(default=None, init=False)
    calls: list[ToolResult] = field(default_factory=list, init=False)
    read_count: int = field(default=0, init=False)
    write_count: int = field(default=0, init=False)

    # -- state ------------------------------------------------------------
    def _target(self):
        return self.working if self.working is not None else self.studio

    def _ensure_working(self) -> ShadowStudio:
        if self.working is None:
            self.working = ShadowStudio(self.studio)
        return self.working

    def reset(self) -> None:
        self.working = None
        self.calls = []
        self.read_count = 0
        self.write_count = 0

    def diff(self) -> list[dict]:
        if self.working is None:
            return []
        before = self.studio.spec
        after = self.working.spec
        changes = []
        for row in before.diff(after):
            path = row["path"]
            # Derived state and the version counter are engine metadata, not
            # user-visible design changes.
            if path.startswith("derived.") or path == "meta.version":
                continue
            changes.append({"path": path, "from": row.get("from"),
                            "to": row.get("to")})
        added = sorted(after.locks - before.locks)
        removed = sorted(before.locks - after.locks)
        if added:
            changes.append({"path": "locks", "from": sorted(before.locks),
                            "to": sorted(after.locks), "added": added})
        if removed and not added:
            changes.append({"path": "locks", "from": sorted(before.locks),
                            "to": sorted(after.locks), "removed": removed})
        return changes

    def has_changes(self) -> bool:
        return bool(self.diff())

    def working_checks(self) -> list[dict]:
        target = self._target()
        return list(getattr(target, "checks", []) or [])

    def working_summary(self) -> dict:
        target = self._target()
        return dict(getattr(target, "summary", {}) or {})

    def working_spec(self) -> Spec:
        return self._target().solved

    # -- execution --------------------------------------------------------
    def execute(self, call: ToolCall) -> ToolResult:
        started = time.time()
        name = str(call.name or "").strip()
        spec = TOOL_SPECS.get(name)
        if spec is None:
            return self._reject(call, f"unknown tool '{name}'", started)
        if self.control is not None:
            try:
                self.control.check()
            except Exception as exc:  # cancellation / timeout is terminal
                return self._reject(call, str(exc), started, cancelled=True)
        if len(self.calls) >= self.max_calls:
            return self._reject(call, f"tool budget of {self.max_calls} calls exhausted",
                                started)
        if self.approval_mode == "suggest" and spec.mutating and self.write_count >= 12:
            return self._reject(call, "proposal budget exhausted; ask the user to choose",
                                started)
        try:
            arguments, _ = validate_arguments(spec, call.arguments or {})
        except ToolError as exc:
            return self._reject(call, str(exc), started)

        if spec.mutating:
            self._ensure_working()
        target = self._target()
        self._event("tool", f"Called {name}", call.reason or "",
                    status="running", data={"tool": name, "arguments": arguments,
                                            "call_id": call.call_id})
        ctx = ToolContext(studio=target, session=self.session,
                          build_root=self.build_root, emit=self.emit,
                          control=self.control, max_calls=self.max_calls)
        try:
            data = HANDLERS[name](ctx, **arguments)
            result = ToolResult(name=name, ok=True, call_id=call.call_id,
                                data=data or {}, read_only=spec.read_only,
                                duration_ms=int((time.time() - started) * 1000),
                                message=str((data or {}).get("message", "")))
        except ToolDenied as exc:
            return self._reject(call, str(exc), started, denied=True)
        except ToolError as exc:
            return self._reject(call, str(exc), started)
        except Exception as exc:  # noqa: BLE001 - never crash the run
            return self._reject(call, f"{type(exc).__name__}: {exc}", started)

        if spec.mutating:
            self.write_count += 1
        else:
            self.read_count += 1
        self.calls.append(result)
        self._event("tool", f"{name} completed",
                    result.message or "returned data",
                    status="ok", data={"tool": name, "call_id": call.call_id,
                                       "duration_ms": result.duration_ms,
                                       "read_only": result.read_only})
        return result

    def _reject(self, call: ToolCall, message: str, started: float,
                *, denied: bool = False, cancelled: bool = False) -> ToolResult:
        result = ToolResult(name=call.name, ok=False, call_id=call.call_id,
                            error=message, message=message,
                            duration_ms=int((time.time() - started) * 1000),
                            read_only=clean_read_only(call.name))
        self.calls.append(result)
        kind = "cancelled" if cancelled else ("denied" if denied else "error")
        self._event(kind, f"{call.name or 'tool'} rejected", message,
                    status="error", data={"tool": call.name,
                                          "call_id": call.call_id,
                                          "error": message})
        return result

    def _event(self, kind: str, title: str, detail: str = "", *,
               status: str = "done", data: dict | None = None) -> None:
        if not callable(self.emit):
            return
        try:
            self.emit(TimelineEvent(kind=kind, title=title, detail=detail,
                                    status=status, iteration=self.iteration,
                                    data=data or {}))
        except Exception:  # noqa: BLE001 - a dead listener must not kill a run
            pass

    # -- commit / rollback -------------------------------------------------
    def commit(self, label: str = "Agent change") -> dict:
        if self.working is None:
            return {"applied": 0, "changes": [], "checksum": None}
        changes = self.diff()
        if not changes:
            return {"applied": 0, "changes": [], "checksum": None}
        shadow = self.working
        self.studio.spec = shadow.spec.copy()
        self.studio.last_build = None
        self.studio.rebuild()
        return {"applied": len([c for c in changes if c["path"] != "locks"]),
                "changes": changes,
                "checksum": self.studio.solved.get("derived.checksum"),
                "summary": dict(self.studio.summary or {})}

    def rollback(self) -> None:
        self.working = None


def clean_read_only(name: str) -> bool:
    spec = TOOL_SPECS.get(name)
    return bool(spec.read_only) if spec else True


def valid_tool_names() -> set[str]:
    return set(TOOL_SPECS)


def readonly_tools() -> set[str]:
    return set(READ_ONLY)
