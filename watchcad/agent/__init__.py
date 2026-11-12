"""WatchMaker's autonomous, tool-using design agent.

The package is intentionally tiny and stdlib-only.  It never generates geometry:
the deterministic solver and verifier remain the only source of truth.

Typical use::

    from watchcad.api import STUDIO, SESSIONS
    from watchcad.agent import run_agent

    session = SESSIONS.get("default")
    result = run_agent(session, "make the dial blue and verify it")
    print(result.response)
"""

from __future__ import annotations

from .context import (
    AgentCancelled, AgentTimeout, ConversationContext, RunControl,
    Session, SessionManager,
)
from .evaluator import count_failures, evaluate, failures
from .models import (
    AgentMessage, AgentRunResult, AgentState, DesignMemory, EvaluationResult,
    PlanStep, RepairAttempt, SpecChange, TimelineEvent, ToolCall, ToolResult,
)
from .orchestrator import AgentOrchestrator, apply_variant, run_agent
from .planner import Intent, build_variants, detect_intent, plan_local
from .tool_executor import ToolExecutor, validate_arguments
from .tools import TOOL_SPECS, openai_tools, tool_catalog

__all__ = [
    "AgentCancelled", "AgentMessage", "AgentOrchestrator", "AgentRunResult",
    "AgentState", "AgentTimeout", "ConversationContext", "DesignMemory",
    "EvaluationResult", "Intent", "PlanStep", "RepairAttempt", "RunControl",
    "Session", "SessionManager", "SpecChange", "TOOL_SPECS", "TimelineEvent",
    "ToolCall", "ToolExecutor", "ToolResult", "apply_variant", "build_variants",
    "count_failures", "detect_intent", "evaluate", "failures", "openai_tools",
    "plan_local", "run_agent", "tool_catalog", "validate_arguments",
]
