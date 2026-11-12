"""Typed models shared by the WatchMaker agent.

Everything here is plain dataclasses so it can be serialised to JSON without a
third-party runtime.  The agent never stores meshes or executable Python in a
model: only text, spec paths, diffs and tool results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any


def _now() -> float:
    return round(time.time(), 3)


def new_id(prefix: str = "run") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: new_id("call"))
    reason: str = ""

    def to_dict(self) -> dict:
        return {"id": self.call_id, "name": self.name,
                "arguments": self.arguments, "reason": self.reason}


@dataclass
class ToolResult:
    name: str
    ok: bool
    call_id: str = ""
    data: dict = field(default_factory=dict)
    message: str = ""
    error: str | None = None
    duration_ms: int = 0
    read_only: bool = True

    def to_dict(self) -> dict:
        out = {
            "name": self.name, "ok": self.ok, "call_id": self.call_id,
            "message": self.message, "duration_ms": self.duration_ms,
            "read_only": self.read_only,
        }
        if self.error:
            out["error"] = self.error
        if self.data:
            out["data"] = self.data
        return out


@dataclass
class TimelineEvent:
    kind: str
    title: str
    detail: str = ""
    status: str = "done"
    iteration: int = 0
    data: dict = field(default_factory=dict)
    seq: int = 0
    at: float = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "detail": self.detail,
                "status": self.status, "iteration": self.iteration,
                "data": self.data, "seq": self.seq, "at": self.at}


@dataclass
class SpecChange:
    path: str
    before: Any = None
    after: Any = None
    note: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "from": self.before, "to": self.after,
                "note": self.note}


@dataclass
class AgentMessage:
    role: str
    content: str
    kind: str = "text"
    data: dict = field(default_factory=dict)
    at: float = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content, "kind": self.kind,
                "data": self.data, "at": self.at}


@dataclass
class PlanStep:
    title: str
    tool: str = ""
    arguments: dict = field(default_factory=dict)
    reason: str = ""
    status: str = "pending"
    iteration: int = 0
    step_id: str = field(default_factory=lambda: new_id("step"))

    def to_dict(self) -> dict:
        return {"id": self.step_id, "title": self.title, "tool": self.tool,
                "arguments": self.arguments, "reason": self.reason,
                "status": self.status, "iteration": self.iteration}


@dataclass
class DesignMemory:
    intent: str = ""
    constraints: list[str] = field(default_factory=list)
    preferences: dict = field(default_factory=dict)
    locked_fields: list[str] = field(default_factory=list)
    versions: list[dict] = field(default_factory=list)
    verification_history: list[dict] = field(default_factory=list)
    applied_changes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "constraints": list(self.constraints),
            "preferences": dict(self.preferences),
            "locked_fields": list(self.locked_fields),
            "versions": list(self.versions[-20:]),
            "verification_history": list(self.verification_history[-20:]),
            "applied_changes": list(self.applied_changes[-40:]),
        }


@dataclass
class RepairAttempt:
    iteration: int
    failures_before: int
    failures_after: int
    actions: list[dict] = field(default_factory=list)
    improved: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {"iteration": self.iteration, "failures_before": self.failures_before,
                "failures_after": self.failures_after, "actions": self.actions,
                "improved": self.improved, "note": self.note}


@dataclass
class EvaluationResult:
    intent_understood: bool = False
    plan_generated: bool = False
    changes_proposed: bool = False
    changes_applied: bool = False
    geometry_rebuilt: bool = False
    verification_completed: bool = False
    objective_achieved: bool = False
    protected_intact: bool = True
    failures_before: int = 0
    failures_after: int = 0
    notes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class AgentState:
    session_id: str = "default"
    run_id: str = field(default_factory=lambda: new_id("run"))
    status: str = "idle"
    objective: str = ""
    mode: str = "autonomous"
    engine: str = "local rules"
    intent: str = ""
    plan: list[dict] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    changes: list[SpecChange] = field(default_factory=list)
    before_checksum: str = ""
    after_checksum: str = ""
    verification_before: dict = field(default_factory=dict)
    verification_after: dict = field(default_factory=dict)
    iterations: int = 0
    repair_attempts: list[RepairAttempt] = field(default_factory=list)
    evaluation: EvaluationResult = field(default_factory=EvaluationResult)
    pending_token: str = ""
    staged_ops: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)
    final_message: str = ""
    error: str = ""
    started_at: float = field(default_factory=_now)
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "status": self.status,
            "objective": self.objective,
            "mode": self.mode,
            "engine": self.engine,
            "intent": self.intent,
            "plan": self.plan,
            "timeline": [e.to_dict() for e in self.timeline],
            "changes": [c.to_dict() for c in self.changes],
            "before_checksum": self.before_checksum,
            "after_checksum": self.after_checksum,
            "verification_before": self.verification_before,
            "verification_after": self.verification_after,
            "iterations": self.iterations,
            "repair_attempts": [a.to_dict() for a in self.repair_attempts],
            "evaluation": self.evaluation.to_dict(),
            "pending_token": self.pending_token,
            "staged_ops": self.staged_ops,
            "questions": self.questions,
            "notes": self.notes,
            "unresolved": self.unresolved,
            "variants": self.variants,
            "final_message": self.final_message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class AgentRunResult:
    state: AgentState
    response: str = ""
    applied: int = 0

    def to_dict(self) -> dict:
        data = self.state.to_dict()
        data["response"] = self.response
        data["applied"] = self.applied
        return data
