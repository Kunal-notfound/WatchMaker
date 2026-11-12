"""Conversation, design and session context.

The browser used to talk to one process-wide ``Studio``.  This module keeps the
same default studio for backward compatibility while allowing independent
sessions to hold their own spec, conversation, design memory, version history
and pending approvals.

Only plain Python objects live here.  A session never owns geometry: it points at
a studio-like object whose deterministic solver builds the scene.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import hashlib
import json
import re
import threading
import time
from typing import Any, Callable

from .models import AgentMessage, DesignMemory, new_id


def now() -> float:
    return round(time.time(), 3)


class AgentCancelled(RuntimeError):
    """Raised when the user presses stop."""


class AgentTimeout(RuntimeError):
    """Raised when a bounded agent run exceeds its deadline."""


@dataclass
class RunControl:
    """Cooperative cancellation and a hard wall-clock budget for one run."""

    run_id: str = field(default_factory=lambda: new_id("run"))
    timeout_s: float = 150.0
    started: float = field(default_factory=now)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise AgentCancelled("run cancelled by the user")
        if self.timeout_s and time.time() - self.started > self.timeout_s:
            raise AgentTimeout(f"run exceeded its {self.timeout_s:.0f} s budget")


@dataclass
class VersionSnapshot:
    version_id: str
    index: int
    label: str
    kind: str
    spec: dict
    checksum: str
    summary: dict = field(default_factory=dict)
    at: float = field(default_factory=now)

    def to_dict(self, include_spec: bool = False) -> dict:
        out = {
            "id": self.version_id,
            "index": self.index,
            "label": self.label,
            "kind": self.kind,
            "checksum": self.checksum,
            "summary": self.summary,
            "at": self.at,
        }
        if include_spec:
            out["spec"] = self.spec
        return out


def _clean_spec(spec) -> dict:
    """A spec payload without derived state, so the solver owns it again."""
    if hasattr(spec, "to_dict"):
        payload = copy.deepcopy(spec.to_dict())
    elif isinstance(spec, dict):
        payload = copy.deepcopy(spec)
    else:
        raise TypeError("expected a Spec or a spec dictionary")
    data = payload.get("data", payload)
    if isinstance(data, dict):
        data.pop("derived", None)
    payload["data"] = data
    prov = payload.get("provenance")
    if isinstance(prov, dict):
        payload["provenance"] = {k: v for k, v in prov.items()
                                 if not k.startswith("derived.")}
    return payload


def _checksum(studio) -> str:
    try:
        return str(studio.solved.get("derived.checksum")
                   or studio.spec.get("derived.checksum") or "")
    except Exception:
        return ""


def _summary(studio) -> dict:
    try:
        return dict(studio.summary or {})
    except Exception:
        return {}


class ConversationContext:
    """The transcript, kept separate from the design document."""

    def __init__(self, limit: int = 120):
        self.messages: list[AgentMessage] = []
        self.limit = limit
        self.summary = ""

    def add(self, role: str, content: str, kind: str = "text",
            data: dict | None = None) -> AgentMessage:
        message = AgentMessage(role=role, content=content, kind=kind, data=data or {})
        self.messages.append(message)
        if len(self.messages) > self.limit:
            self.messages = self.messages[-self.limit:]
        return message

    def user(self, content: str, data: dict | None = None) -> AgentMessage:
        return self.add("user", content, "text", data)

    def assistant(self, content: str, kind: str = "text",
                  data: dict | None = None) -> AgentMessage:
        return self.add("assistant", content, kind, data)

    def recent(self, count: int = 12) -> list[AgentMessage]:
        return self.messages[-count:]

    def as_llm_messages(self, count: int = 14) -> list[dict]:
        out = []
        if self.summary:
            out.append({"role": "system", "content": "Conversation summary: " + self.summary})
        for message in self.recent(count):
            if message.role not in ("user", "assistant", "system"):
                continue
            out.append({"role": message.role, "content": message.content})
        return out

    def to_dict(self) -> dict:
        return {"messages": [m.to_dict() for m in self.messages[-40:]],
                "summary": self.summary}


class Session:
    """One isolated design session: spec + transcript + memory + history."""

    def __init__(self, session_id: str, studio, *, title: str = ""):
        self.session_id = session_id
        self._studio = studio
        self.title = title or session_id
        self.conversation = ConversationContext()
        self.memory = DesignMemory()
        self.versions: list[VersionSnapshot] = []
        self.redo_stack: list[VersionSnapshot] = []
        self.pending: dict | None = None
        self.runs: dict[str, RunControl] = {}
        self.last_agent: dict | None = None
        self.created_at = now()
        self._version_seq = 0
        self._lock = threading.RLock()
        self.checkpoint("Session opened", "initial")

    # -- studio binding ---------------------------------------------------
    @property
    def studio(self):
        return self._studio

    def bind(self, studio) -> None:
        """Bind the default session to the current module-level studio."""
        with self._lock:
            if studio is self._studio:
                return
            self._studio = studio
            # A new studio object means a fresh document; do not carry stale
            # versions across the rebind (the API tests replace STUDIO).
            self.versions.clear()
            self.redo_stack.clear()
            self.pending = None
            self.take_checkpoint("Session rebound", "initial", force=True)

    # -- version history --------------------------------------------------
    def _signature(self) -> str:
        locks = sorted(self.studio.spec.locks)
        blob = json.dumps({"checksum": _checksum(self.studio), "locks": locks},
                          sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def checkpoint(self, label: str, kind: str = "change", *,
                   force: bool = False) -> VersionSnapshot | None:
        """Append the current document if it differs from the newest version."""
        with self._lock:
            if self.versions and not force:
                if self.versions[-1].checksum == _checksum(self.studio) \
                        and self.versions[-1].spec.get("locks") == sorted(self.studio.spec.locks):
                    return None
            return self.take_checkpoint(label, kind, force=force)

    def take_checkpoint(self, label: str, kind: str = "change", *,
                        force: bool = False) -> VersionSnapshot:
        with self._lock:
            if self.versions and not force:
                if self.versions[-1].checksum == _checksum(self.studio) \
                        and self.versions[-1].spec.get("locks") == sorted(self.studio.spec.locks):
                    return self.versions[-1]
            self._version_seq += 1
            snapshot = VersionSnapshot(
                version_id=f"v{self._version_seq}",
                index=len(self.versions) + 1,
                label=label,
                kind=kind,
                spec=_clean_spec(self.studio.spec),
                checksum=_checksum(self.studio),
                summary=_summary(self.studio),
            )
            self.versions.append(snapshot)
            self.redo_stack.clear()
            self.memory.versions = [v.to_dict() for v in self.versions[-20:]]
            return snapshot

    def restore(self, version_id_or_index: str | int) -> VersionSnapshot:
        with self._lock:
            target = None
            for snapshot in self.versions:
                if str(snapshot.version_id) == str(version_id_or_index) \
                        or str(snapshot.index) == str(version_id_or_index):
                    target = snapshot
                    break
            if target is None:
                raise KeyError(f"unknown version {version_id_or_index}")
            self._restore_spec(target.spec)
            # Keep history linear at the restored point, but allow redo.
            index = self.versions.index(target)
            if index != len(self.versions) - 1:
                self.redo_stack = list(reversed(self.versions[index + 1:])) + self.redo_stack
                self.versions = self.versions[:index + 1]
            return target

    def undo(self) -> VersionSnapshot | None:
        with self._lock:
            if len(self.versions) < 2:
                return None
            current = self.versions.pop()
            self.redo_stack.append(current)
            target = self.versions[-1]
            self._restore_spec(target.spec)
            return target

    def redo(self) -> VersionSnapshot | None:
        with self._lock:
            if not self.redo_stack:
                return None
            target = self.redo_stack.pop()
            self.versions.append(target)
            self._restore_spec(target.spec)
            return target

    def _restore_spec(self, payload: dict) -> None:
        from ..schema import Spec

        spec = Spec.from_dict(copy.deepcopy(payload))
        self.studio.spec = spec
        self.studio.last_build = None
        self.studio.rebuild()

    def history(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [v.to_dict() for v in self.versions[-limit:]]

    def can_undo(self) -> bool:
        return len(self.versions) >= 2

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    # -- pending approvals ------------------------------------------------
    def stage(self, *, request: str, mode: str, spec, ops: list[dict],
              agent_state: dict, notes: list[str] | None = None,
              checks: list[dict] | None = None) -> str:
        with self._lock:
            token = new_id("approve")
            self.pending = {
                "token": token,
                "request": request,
                "mode": mode,
                "spec": _clean_spec(spec),
                "ops": copy.deepcopy(ops),
                "agent": copy.deepcopy(agent_state),
                "notes": list(notes or []),
                "checks": copy.deepcopy(checks or []),
                "at": now(),
            }
            return token

    def approve(self, token: str) -> dict:
        with self._lock:
            if not self.pending:
                raise KeyError("no changes are waiting for approval")
            if self.pending.get("token") != token:
                raise ValueError("approval token does not match the pending change")
            pending = self.pending
            self.pending = None
            from ..schema import Spec

            self.studio.spec = Spec.from_dict(copy.deepcopy(pending["spec"]))
            self.studio.last_build = None
            self.studio.rebuild()
            self.take_checkpoint("Approved: " + pending["request"][:80], "agent")
            self.memory.applied_changes.append({
                "request": pending["request"], "ops": pending["ops"], "at": now(),
            })
            return pending

    def reject(self, token: str | None = None) -> dict:
        with self._lock:
            if not self.pending:
                raise KeyError("no changes are waiting for approval")
            if token and self.pending.get("token") != token:
                raise ValueError("approval token does not match the pending change")
            pending = self.pending
            self.pending = None
            return pending

    def clear_pending(self) -> None:
        with self._lock:
            self.pending = None

    # -- design memory ----------------------------------------------------
    def remember_request(self, request: str, *, intent: str = "",
                         changes: list[dict] | None = None,
                         verification: dict | None = None,
                         constraints: list[str] | None = None) -> None:
        with self._lock:
            if intent:
                self.memory.intent = intent
            for item in constraints or []:
                if item and item not in self.memory.constraints:
                    self.memory.constraints.append(item)
            if changes:
                self.memory.applied_changes.extend(changes)
                self.memory.applied_changes = self.memory.applied_changes[-80:]
            if verification:
                self.memory.verification_history.append(
                    {"summary": verification, "at": now(), "request": request[:120]})
                self.memory.verification_history = self.memory.verification_history[-40:]
            self.memory.locked_fields = sorted(self.studio.spec.locks)
            if changes:
                for change in changes:
                    path = change.get("path", "")
                    if path in ("case.material", "dial.color", "case.finish",
                                "dial.finish", "hands.style", "case.bezel"):
                        self.memory.preferences[path] = change.get("to")
            self.memory.versions = [v.to_dict() for v in self.versions[-20:]]

    def conversation_payload(self) -> dict:
        return {
            "session_id": self.session_id,
            "conversation": self.conversation.to_dict(),
            "memory": self.memory.to_dict(),
            "versions": self.history(12),
            "can_undo": self.can_undo(),
            "can_redo": self.can_redo(),
            "pending": bool(self.pending),
        }

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "conversation": self.conversation.to_dict(),
            "memory": self.memory.to_dict(),
            "history": self.history(20),
            "can_undo": self.can_undo(),
            "can_redo": self.can_redo(),
            "pending": self.pending is not None,
            "last_agent": self.last_agent,
        }


class SessionManager:
    """Resolve a request header to an isolated session.

    ``default_provider`` is called on every default-session lookup so the API
    tests can replace ``api.STUDIO`` without breaking the manager.
    """

    _VALID = re.compile(r"[^A-Za-z0-9_.-]+")

    def __init__(self, default_provider: Callable[[], Any],
                 studio_factory: Callable[[], Any]):
        self._default_provider = default_provider
        self._studio_factory = studio_factory
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    @classmethod
    def normalize_id(cls, value: str | None) -> str:
        text = (value or "default").strip()
        text = cls._VALID.sub("-", text)[:64].strip("-.")
        return text or "default"

    def has(self, session_id: str | None = "default") -> bool:
        """True when a session for this id is already live in memory."""
        sid = self.normalize_id(session_id)
        with self._lock:
            return sid in self._sessions

    def controls(self, session_id: str | None = "default") -> list[RunControl]:
        sid = self.normalize_id(session_id)
        with self._lock:
            session = self._sessions.get(sid)
            return list(session.runs.values()) if session else []

    def get(self, session_id: str | None = "default") -> Session:
        sid = self.normalize_id(session_id)
        with self._lock:
            if sid == "default":
                studio = self._default_provider()
                session = self._sessions.get("default")
                if session is None:
                    session = Session("default", studio, title="Default design")
                    self._sessions["default"] = session
                else:
                    session.bind(studio)
                return session
            session = self._sessions.get(sid)
            if session is None:
                session = Session(sid, self._studio_factory(), title=sid)
                self._sessions[sid] = session
            return session

    def create(self, title: str = "") -> Session:
        sid = new_id("ses")
        with self._lock:
            session = Session(sid, self._studio_factory(), title=title or sid)
            self._sessions[sid] = session
            return session

    def drop(self, session_id: str) -> bool:
        sid = self.normalize_id(session_id)
        if sid == "default":
            return False
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def list(self) -> list[dict]:
        with self._lock:
            return [{"session_id": s.session_id, "title": s.title,
                     "created_at": s.created_at, "versions": len(s.versions),
                     "pending": s.pending is not None}
                    for s in self._sessions.values()]

    def update_default_title(self, title: str) -> None:
        with self._lock:
            session = self._sessions.get("default")
            if session:
                session.title = title
