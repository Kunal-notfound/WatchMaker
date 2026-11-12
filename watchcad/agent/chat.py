"""Conversational runtime for WatchMaker.

A user message is conversation first and a CAD task second.  The runtime:

1. restores the persistent transcript and design context,
2. lets the model answer directly or call any validated tool,
3. supports multiple tool rounds, streaming text and streamed tool status,
4. runs mutation tools against the existing shadow working copy,
5. solves, verifies, repairs within a bounded budget, and only then commits,
6. persists the visible answer, tool events and design snapshot.

When no model is configured the runtime can still use the deterministic local
engine for explicit CAD actions, but it says so plainly.  It never presents a
local rule result as a model answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import json
import re
import time
from typing import Any, Callable

from .. import llm as llm_module
from ..llm import LLMConfig, LLMError, LLMNotConfigured, LLMReply, user_content
from ..nlp import describe
from ..schema import FIELDS, Spec
from ..store import ConversationStore, transcript_text
from .context import AgentCancelled, AgentTimeout, RunControl
from .evaluator import count_failures, failures
from .knowledge import lookup as knowledge_lookup
from .models import AgentState, SpecChange, TimelineEvent, ToolCall
from .orchestrator import AgentOrchestrator, normalize_mode
from .planner import detect_intent, patches_from_request
from .prompts import CONVERSATION_SYSTEM_PROMPT, context_message
from .tools import openai_tools
from .tool_executor import ToolExecutor


TOOL_STATUS = {
    "get_current_spec": "Inspecting the current watch",
    "get_verification_report": "Checking verification results",
    "inspect_component": "Inspecting a component",
    "inspect_geometry": "Inspecting generated geometry",
    "get_design_history": "Reviewing design history",
    "compare_specs": "Comparing design versions",
    "get_field_dependencies": "Checking field dependencies",
    "update_spec": "Updating the design",
    "apply_patch": "Updating the design",
    "run_solver": "Running the geometry solver",
    "run_verification": "Verifying the design",
    "build_package": "Building the production package",
    "lookup_reference": "Looking up reference data",
    "research_topic": "Researching technical references",
    "fetch_source": "Reading a source",
    "undo_design": "Restoring the previous design",
    "redo_design": "Restoring the next design",
}

MUTATING_TOOLS = {"update_spec", "apply_patch"}
# Without a model only unambiguous CAD actions are executed locally.  Questions,
# critiques and reference lookups stay conversation and are answered honestly as
# "no model configured" rather than being turned into a speculative CAD run.
LOCAL_ACTION_INTENTS = {"modify", "repair", "build", "verify", "variations",
                        "undo", "redo"}


def _now() -> float:
    return round(time.time(), 3)


def _trim(data: Any, limit: int = 8000) -> Any:
    if not data:
        return {}
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        return {}
    if len(text) <= limit:
        return data
    return {"truncated": True, "preview": text[:limit]}


def _clean_spec(spec) -> dict:
    payload = (copy.deepcopy(spec.to_dict())
               if hasattr(spec, "to_dict") else copy.deepcopy(dict(spec)))
    data = payload.get("data", payload)
    if isinstance(data, dict):
        data.pop("derived", None)
    payload["data"] = data
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        payload["provenance"] = {k: v for k, v in provenance.items()
                                 if not k.startswith("derived.")}
    return payload


def _view(entry: dict) -> dict:
    return {"name": entry.get("name"), "category": entry.get("category"),
            "status": entry.get("status"),
            "status_label": entry.get("status_label"),
            "summary": entry.get("summary"), "data": entry.get("data", {}),
            "provenance": entry.get("provenance", {})}


@dataclass
class ChatTurnResult:
    conversation_id: str
    run_id: str
    text: str = ""
    engine: str = "local rules"
    model: str = ""
    status: str = "complete"
    intent: str = ""
    changes: list[dict] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    pending: dict | None = None
    user_message: dict = field(default_factory=dict)
    assistant_message: dict = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    error: str = ""
    snapshot: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "response": self.text,
            "text": self.text,
            "engine": self.engine,
            "model": self.model,
            "status": self.status,
            "intent": self.intent,
            "changes": self.changes,
            "verification_after": self.verification,
            "tool_calls": self.tool_calls,
            "timeline": self.timeline,
            "usage": self.usage,
            "pending": self.pending,
            "user_message": self.user_message,
            "assistant_message": self.assistant_message,
            "suggestions": self.suggestions,
            "error": self.error,
            "snapshot": self.snapshot,
            "session": self.session,
        }


class ConversationRuntime:
    """One conversational turn against one persistent conversation."""

    def __init__(self, session, store: ConversationStore, conversation_id: str, *,
                 llm_client=None, llm_config: LLMConfig | None = None,
                 build_root: str | None = None):
        self.session = session
        self.store = store
        self.conversation_id = conversation_id
        self.llm_client = llm_client
        self.llm_config = llm_config or LLMConfig.from_env()
        self.build_root = build_root
        self.studio = session.studio
        self._emit_callback: Callable[[dict], None] | None = None
        self._timeline: list[dict] = []
        self._state: AgentState | None = None

    # -- public -----------------------------------------------------------
    def run(self, message: str, *, images: list[str] | None = None,
            mode: str = "safe", use_llm: bool | None = None,
            max_tool_rounds: int = 12, emit=None,
            control: RunControl | None = None, regenerate: bool = False,
            edit_message_id: str = "") -> ChatTurnResult:
        conversation = self.store.get_conversation(self.conversation_id)
        if conversation is None:
            conversation = self.store.ensure_conversation(
                self.conversation_id, design_session=self.session.session_id)
        mode = normalize_mode(mode)
        control = control or RunControl(timeout_s=180.0)
        self.session.runs[control.run_id] = control
        self._emit_callback = emit
        self._timeline = []
        self._state = AgentState(session_id=self.session.session_id,
                                 objective=str(message or ""), mode=mode,
                                 engine="local rules")
        self._state.run_id = control.run_id
        started = self.studio.spec.copy()
        before_checks = list(self.studio.checks or [])
        before_fail = count_failures(before_checks)
        existing_user = None

        try:
            if edit_message_id:
                target = self.store.get_message(edit_message_id)
                if target is None:
                    raise ValueError("that message no longer exists")
                self.store.delete_after(self.conversation_id, target["position"],
                                        inclusive=True)
                message = str(message or target.get("content") or "")
                images = images or list(target.get("data", {}).get("images") or [])
            if regenerate:
                rows = [row for row in self.store.messages(self.conversation_id)
                        if row.get("role") == "user"]
                if not rows:
                    raise ValueError("there is no previous message to regenerate")
                previous = rows[-1]
                self.store.delete_after(self.conversation_id, previous["position"])
                message = previous.get("content") or ""
                images = images or list(previous.get("data", {}).get("images") or [])
                existing_user = previous
            message = str(message or "").strip()
            images = self._valid_images(images)
            if not message and not images:
                raise ValueError("the message is empty")

            if existing_user is not None:
                user_message = existing_user
                message = str(user_message.get("content") or message)
            else:
                user_message = self.store.add_message(
                    self.conversation_id, "user",
                    message or "Please analyse this image.",
                    data={"images": images} if images else {})
                message = str(user_message.get("content") or message)
                self.store.autotitle(self.conversation_id,
                                     message or "Image analysis")
            result = ChatTurnResult(
                conversation_id=self.conversation_id,
                run_id=control.run_id,
                user_message=self._visible(user_message),
            )
            self._emit({"type": "start", "run_id": control.run_id,
                        "conversation_id": self.conversation_id,
                        "mode": mode,
                        "intent": detect_intent(message).name})

            model_available = bool(self.llm_config.available)
            want_model = (model_available if use_llm is None else bool(use_llm))
            if want_model and not model_available:
                self._emit({"type": "notice",
                            "text": "No language model is configured; using the "
                                    "deterministic local engine."})
            if want_model and model_available:
                self._model_turn(result, message, images, mode, control,
                                 before_checks, before_fail, max_tool_rounds)
            else:
                self._local_turn(result, message, mode, control, before_checks)

            result.timeline = list(self._timeline)
            result.snapshot = self.studio.snapshot()
            result.session = self.session.conversation_payload()
            result.pending = self.session.pending
            self._persist_design(result)
            result.suggestions = result.suggestions or self._suggestions(result)
            assistant = self.store.add_message(
                self.conversation_id, "assistant", result.text,
                kind="assistant",
                data={"engine": result.engine, "model": result.model,
                      "status": result.status, "intent": result.intent,
                      "changes": result.changes,
                      "usage": result.usage, "suggestions": result.suggestions})
            result.assistant_message = self._visible(assistant)
            self._maybe_summarize()
            return result
        except AgentCancelled as exc:
            result = self._error_result(control, "cancelled", str(exc),
                                        "Stopped. No changes were committed.")
            self._persist_error(result)
            return result
        except AgentTimeout as exc:
            result = self._error_result(control, "timeout", str(exc),
                                        "The response exceeded its time budget. "
                                        "No changes were committed.")
            self._persist_error(result)
            return result
        except ValueError as exc:
            result = self._error_result(control, "error", str(exc), str(exc))
            self._persist_error(result)
            return result
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            result = self._error_result(control, "error",
                                        f"{type(exc).__name__}: {exc}",
                                        "Something went wrong before I could "
                                        "finish. No unverified change was committed.")
            self._persist_error(result)
            return result
        finally:
            self.session.runs.pop(control.run_id, None)

    # -- model path -------------------------------------------------------
    def _model_turn(self, result: ChatTurnResult, message: str,
                    images: list[str], mode: str, control: RunControl,
                    before_checks: list[dict], before_fail: int,
                    max_rounds: int) -> None:
        result.intent = detect_intent(message).name
        executor = ToolExecutor(studio=self.studio, session=self.session,
                                emit=self._tool_event, control=control,
                                build_root=self.build_root, approval_mode=mode,
                                max_calls=120)
        messages = self._messages(message, images)
        tools = openai_tools()
        usage: dict = {}
        final_text = ""
        model_failed: str = ""
        rounds = max(1, min(24, int(max_rounds or 12)))
        reply: LLMReply | None = None

        for round_index in range(rounds):
            control.check()
            self._emit({"type": "status", "text": "Thinking",
                        "round": round_index + 1})
            streamed: list[str] = []

            def on_text(chunk: str) -> None:
                streamed.append(chunk)
                self._emit({"type": "delta", "text": chunk})

            try:
                reply = self._call_model(messages, tools, on_text=on_text)
            except (LLMError, OSError, TimeoutError, TypeError) as exc:
                model_failed = f"{type(exc).__name__}: {exc}"
                break
            for key, value in (reply.usage or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = int(usage.get(key, 0)) + int(value)
            if not reply.has_tool_calls:
                final_text = (reply.content or "".join(streamed)).strip()
                break
            messages.append(llm_module.assistant_tool_message(reply))
            for call in reply.tool_calls:
                control.check()
                tool_result = self._execute_tool(executor, call, mode)
                state = self._state
                if state is not None:
                    state.plan.append({
                        "id": f"step_{len(state.plan) + 1}",
                        "title": call.get("name", "tool"),
                        "tool": call.get("name", ""),
                        "arguments": call.get("arguments") or {},
                        "reason": "model-selected step",
                        "status": "done" if tool_result.ok else "error",
                        "iteration": round_index + 1,
                    })
                payload = {
                    "ok": tool_result.ok,
                    "tool": tool_result.name,
                    "message": tool_result.message,
                    "error": tool_result.error,
                    "data": _trim(tool_result.data),
                }
                result.tool_calls.append({
                    "name": tool_result.name, "ok": tool_result.ok,
                    "arguments": call.get("arguments") or {},
                    "message": tool_result.message,
                    "error": tool_result.error,
                    "read_only": tool_result.read_only,
                })
                self.store.add_tool_event(
                    self.conversation_id, tool_result.name,
                    arguments=call.get("arguments") or {},
                    result=payload, ok=tool_result.ok,
                    run_id=control.run_id)
                messages.append(llm_module.tool_result_message(
                    call.get("id") or tool_result.call_id,
                    tool_result.name, payload))
                if control.cancelled:
                    raise AgentCancelled("run cancelled by the user")
        else:
            final_text = ""

        if model_failed:
            executor.rollback()
            result.status = "error"
            result.error = model_failed
            result.engine = f"llm ({self.llm_config.model}) unavailable"
            result.model = self.llm_config.model
            result.text = (
                "I couldn't get a usable response from the configured model.\n\n"
                f"`{model_failed}`\n\nNo design change was committed. Check "
                "`WATCHCAD_LLM_URL`, the model name and the provider key, or "
                "switch the assistant to the local engine.")
            self._emit({"type": "error", "error": {"message": model_failed}})
            return

        result.engine = f"llm ({self.llm_config.model})"
        result.model = self.llm_config.model
        result.usage = usage
        if not final_text:
            control.check()
            final_text = self._summarize_after_tools(messages, tools)
        if not final_text:
            final_text = ("The model called tools but did not write a final answer. "
                          "The verified design status is shown below.")
        result.text = final_text
        self._finish_cad(result, executor, mode, before_checks, before_fail,
                         message, control)
        self._emit({"type": "done", "status": result.status})

    def _summarize_after_tools(self, messages: list[dict], tools: list[dict]) -> str:
        """One final tool-free round so the model explains what happened."""
        try:
            follow = list(messages)
            follow.append({
                "role": "user",
                "content": ("Now answer the user in natural language. Say what you "
                            "actually changed or found, the real verification "
                            "result, and any remaining uncertainty. Do not call "
                            "tools and do not claim anything the results do not "
                            "support."),
            })
            reply = self._call_model(follow, None)
            return (reply.content or "").strip()
        except (LLMError, OSError, TimeoutError, TypeError):
            return ""

    def _call_model(self, messages: list[dict], tools: list[dict] | None,
                    on_text: Callable[[str], None] | None = None) -> LLMReply:
        client = self.llm_client or llm_module
        stream = getattr(client, "stream_chat", None) if on_text else None
        try:
            if callable(stream):
                try:
                    reply = stream(messages, tools=tools, config=self.llm_config,
                                   on_text=on_text)
                except TypeError:
                    reply = stream(messages, tools=tools, on_text=on_text)
            else:
                chat = getattr(client, "chat", None) or client
                try:
                    reply = chat(messages, tools=tools, config=self.llm_config)
                except TypeError:
                    reply = chat(messages, tools=tools)
        except LLMNotConfigured:
            raise
        if isinstance(reply, LLMReply):
            return reply
        if isinstance(reply, dict):
            return LLMReply(content=str(reply.get("content") or ""),
                            tool_calls=list(reply.get("tool_calls") or []),
                            raw=reply,
                            model=str(reply.get("model") or self.llm_config.model),
                            usage=dict(reply.get("usage") or {}))
        if isinstance(reply, str):
            return LLMReply(content=reply, model=self.llm_config.model)
        raise LLMError("the configured model client returned an unsupported reply")

    def _execute_tool(self, executor: ToolExecutor, call: dict, mode: str):
        name = str(call.get("name") or "").strip()
        arguments = call.get("arguments") or {}
        tool_call = ToolCall(name=name, arguments=arguments,
                             call_id=call.get("id") or "",
                             reason="model-selected conversational step")
        if name in ("undo_design", "redo_design"):
            # History tools operate on the live document, not a shadow copy.
            executor.rollback()
        return executor.execute(tool_call)

    # -- local path -------------------------------------------------------
    def _local_turn(self, result: ChatTurnResult, message: str, mode: str,
                    control: RunControl, before_checks: list[dict]) -> None:
        intent = detect_intent(message)
        result.intent = intent.name
        result.engine = "local rules (no model configured)"
        parsed = {}
        if intent.name in ("modify", "reference"):
            try:
                parsed = patches_from_request(message, self.studio, use_llm=False)
            except Exception:  # noqa: BLE001 - local parsing is best effort
                parsed = {}
        if intent.name in LOCAL_ACTION_INTENTS and (parsed.get("ops")
                                                    or intent.name != "modify"):
            orchestrator = AgentOrchestrator(
                self.session, llm_config=self.llm_config,
                build_root=self.build_root)
            run = orchestrator.run(message, mode=mode, use_llm=False,
                                   max_iterations=3, max_tool_rounds=8,
                                   emit=self._orchestrator_event, control=control)
            state = run.state
            result.intent = state.intent or intent.name
            result.text = run.response
            result.changes = [c.to_dict() for c in state.changes
                              if c.path != "locks"]
            result.verification = dict(state.verification_after or {})
            result.status = state.status
            result.pending = self.session.pending
            self._timeline.extend(e.to_dict() for e in state.timeline)
            result.tool_calls = [
                {"name": e.data.get("tool"), "message": e.detail,
                 "read_only": bool(e.data.get("read_only"))}
                for e in state.timeline
                if e.kind == "tool" and e.status == "ok" and e.data.get("tool")
            ]
            if not result.text:
                result.text = self._local_factual(state, intent.name)
            self._emit({"type": "done", "status": result.status})
            return

        # A pure conversation without a model: be explicit, then offer the
        # curated reference table if it has a real match.
        entry = knowledge_lookup(message)
        matches = entry.get("matches") or []
        lines = [
            "No language model is configured, so I can't hold a free-form "
            "conversation yet. Set `WATCHCAD_LLM_URL` (and optionally "
            "`WATCHCAD_LLM_KEY` / `WATCHCAD_LLM_MODEL`) to enable the "
            "conversational assistant.",
            "",
            "The deterministic CAD engine is still available: I can inspect the "
            "watch, explain field dependencies, run verification, repair checked "
            "geometry, compare versions, build packages and apply explicit "
            "specification changes.",
        ]
        if matches:
            lines.append("")
            lines.append("From the curated reference table:")
            for item in matches[:4]:
                lines.append(f"- **{item['name']}** — {item.get('status_label')}. "
                             f"{item.get('summary')}")
            lines.append("")
            lines.append("Curated entries are not live manufacturer datasheets.")
        result.text = "\n".join(lines)
        result.status = "complete"
        result.intent = intent.name
        result.verification = dict(self.studio.summary or {})
        self._emit({"type": "done", "status": result.status})

    def _local_factual(self, state: AgentState, intent: str) -> str:
        after = state.verification_after or {}
        if state.status == "complete" and state.changes:
            return (f"The local engine applied {len(state.changes)} validated change(s). "
                    f"Verification: {after.get('fail', 0)} failing check(s).")
        if state.status == "suggested":
            return ("The local engine prepared the change without applying it in "
                    "suggest mode.")
        if state.status == "awaiting_approval":
            return "The change is staged and waiting for your approval."
        if intent == "repair":
            return ("The local engine finished its bounded repair pass. "
                    f"{after.get('fail', 0)} check(s) still fail.")
        return ("The local engine could not map that request to a validated change. "
                "Ask for a specific specification value, or enable a model for "
                "open-ended conversation.")

    # -- finalisation -----------------------------------------------------
    def _finish_cad(self, result: ChatTurnResult, executor: ToolExecutor,
                    mode: str, before_checks: list[dict], before_fail: int,
                    request: str, control: RunControl) -> None:
        with self.session._lock:
            helper = AgentOrchestrator(self.session, llm_config=self.llm_config,
                                       build_root=self.build_root)
            helper.state = self._state
            helper._callback = self._tool_event
            helper._executor = executor
            state = self._state or AgentState(session_id=self.session.session_id)
            if executor.has_changes():
                helper._ensure_verified(executor, state)
                if count_failures(executor.working_checks()) and mode != "suggest":
                    helper._repair(executor, state, 3)
            # Recompute after repair so the reported diff includes any
            # verifier-guided fixes applied above.
            changes = executor.diff()
            after_checks = executor.working_checks()
            after_spec = executor.working_spec()
            result.verification = self._summary(after_checks)
            result.changes = [{"path": c["path"], "from": c.get("from"),
                               "to": c.get("to")}
                              for c in changes if c["path"] != "locks"]
            before_summary = self._summary(before_checks)
            committed = False
            staged = False

            if changes and mode == "suggest":
                result.status = "suggested"
            elif changes and mode == "approval":
                ops = [{"op": "replace", "path": c["path"], "value": c.get("to")}
                       for c in changes if c["path"] != "locks"]
                token = self.session.stage(
                    request=request, mode=mode, spec=executor.working.spec,
                    ops=ops, agent_state=state.to_dict(),
                    notes=state.notes, checks=after_checks)
                result.pending = self.session.pending
                result.status = "awaiting_approval"
                result.tool_calls.append({"name": "approval", "ok": True,
                                          "arguments": {"token": token}})
                staged = True
            elif changes:
                safe = (count_failures(after_checks) <= before_fail
                        and not ({c["path"] for c in changes if c["path"] != "locks"}
                                 & set(self.studio.spec.locks)))
                if safe or mode == "autonomous":
                    commit = executor.commit("Conversational design change")
                    committed = bool(commit.get("applied"))
                    result.status = "complete"
                    self.session.checkpoint("Chat: " + request[:70], "agent")
                    self._timeline_event("commit", "Changes committed",
                                         f"{commit.get('applied', 0)} field(s); "
                                         f"checksum {str(commit.get('checksum'))[:8]}",
                                         status="ok", data={"changes": changes})
                else:
                    executor.rollback()
                    result.status = "rolled_back"
                    self._timeline_event("rollback", "Change rolled back",
                                         "verification did not improve",
                                         status="warning")
            else:
                result.status = "complete"

            result.verification = self._summary(after_checks)
            if result.status == "complete" and not changes:
                result.verification = self._summary(after_checks) or result.verification
            cad_tools = {str(item.get("name") or "") for item in result.tool_calls}
            show_status = bool(changes) or bool(cad_tools & {
                "update_spec", "apply_patch", "run_solver", "run_verification",
                "build_package", "inspect_geometry", "get_verification_report",
                "undo_design", "redo_design",
            })
            result.text = self._compose(result.text, result, changes, before_summary,
                                        committed, staged, mode,
                                        include_verification=show_status)
            state.changes = [
                SpecChange(path=c["path"], before=c.get("from"), after=c.get("to"))
                for c in result.changes
            ]
            state.verification_before = before_summary
            state.verification_after = result.verification
            state.after_checksum = after_spec.get("derived.checksum") or ""
            state.final_message = result.text
            state.status = result.status
            state.finished_at = _now()
            self.session.last_agent = state.to_dict()
            try:
                self.session.remember_request(
                    request, intent=result.intent or "converse",
                    changes=result.changes,
                    verification=result.verification)
            except Exception:  # noqa: BLE001 - memory must not break a turn
                pass

    def _compose(self, text: str, result: ChatTurnResult, changes: list[dict],
                 before: dict, committed: bool, staged: bool, mode: str, *,
                 include_verification: bool = True) -> str:
        bits = [text.strip()] if (text or "").strip() else []
        after = result.verification or {}
        failing = int(after.get("fail") or 0)
        if result.status == "suggested":
            bits.append(f"I prepared {len(changes)} validated change(s) but did "
                        "not apply them (suggest mode). Nothing in the live "
                        "design changed.")
        elif result.status == "awaiting_approval" or staged:
            bits.append(f"I staged {len(changes)} validated change(s) for your "
                        "approval. The live design is unchanged until you approve.")
        elif result.status == "rolled_back":
            bits.append("I rolled the change back because it made verification "
                        "worse. The live design is unchanged.")
        elif committed:
            bits.append(f"Applied {len(changes)} change(s) through the validated "
                        "tool pipeline.")
        elif changes and result.status == "complete":
            bits.append(f"{len(changes)} validated change(s) are ready.")
        elif not bits:
            bits.append("The local engine did not produce a written answer.")
        if include_verification and after:
            bits.append(f"Verification: {after.get('calculated', 0)} calculated, "
                        f"{after.get('estimated', 0)} estimated, "
                        f"{after.get('needs_test', 0)} need bench testing, "
                        f"{failing} failing.")
            if before and before.get("fail") != after.get("fail"):
                bits.append(f"Failures moved from {before.get('fail', 0)} to {failing}.")
            if failing:
                ids = ", ".join(str(c.get("id")) for c in failures(self.studio.checks)[:6])
                if ids:
                    bits.append("Still failing: " + ids + ".")
        return "\n\n".join(bit for bit in bits if bit).strip()

    @staticmethod
    def _summary(checks: list[dict]) -> dict:
        from ..verify import summary

        return summary(checks or [])

    # -- context and persistence ------------------------------------------
    def _messages(self, current: str, images: list[str]) -> list[dict]:
        conversation = self.store.get_conversation(self.conversation_id) or {}
        memory = self.session.memory
        verification = dict(self.studio.summary or {})
        context = context_message(
            describe(self.studio.solved),
            mode="safe",
            engine=f"llm ({self.llm_config.model})" if self.llm_config.available
            else "local rules",
            history=json.dumps(memory.to_dict(), default=str)[:1400],
            constraints=memory.constraints,
            locks=sorted(self.studio.spec.locks),
            verification=verification,
            conversation_summary=conversation.get("summary") or "",
        )
        context += ("\nWritable field groups: " + ", ".join(
            sorted({path.split(".")[0] for path in FIELDS}))
            + ". Use get_current_spec for exact paths and current values.")
        messages = [
            {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT},
            {"role": "system", "content": context},
        ]
        rows = self.store.messages(self.conversation_id, visible_only=True)
        if conversation.get("summary_through"):
            rows = [row for row in rows
                    if int(row.get("position") or 0) > int(conversation["summary_through"])]
        # Keep the transcript bounded by characters, newest first, then restore
        # chronological order.  The summary covers everything older.
        budget = 52000
        selected: list[dict] = []
        for row in reversed(rows[-80:]):
            content = str(row.get("content") or "")
            if row.get("role") == "assistant" and not content.strip():
                continue
            cost = len(content) + 40
            if selected and cost > budget:
                break
            budget -= cost
            selected.append(row)
        selected.reverse()
        for row in selected:
            role = row.get("role")
            data = row.get("data") or {}
            if role == "user":
                attached = data.get("images") or []
                # Only the last two turns re-send image bytes; older images are
                # represented in the text and in the persisted data.
                if attached and row is selected[-1]:
                    messages.append(user_content(str(row.get("content") or ""),
                                                 attached))
                else:
                    suffix = ("\n[The user attached an image earlier in this "
                              "conversation.]" if attached else "")
                    messages.append({"role": "user",
                                     "content": str(row.get("content") or "") + suffix})
            elif role == "assistant":
                messages.append({"role": "assistant",
                                 "content": str(row.get("content") or "")})
        if not selected or selected[-1].get("role") != "user" \
                or selected[-1].get("content") != current:
            messages.append(user_content(current, images))
        elif images:
            messages[-1] = user_content(current, images)
        return messages

    def _valid_images(self, images: list[str] | None) -> list[str]:
        out = []
        for image in images or []:
            if not isinstance(image, str):
                continue
            if not re.match(r"^data:image/(png|jpe?g|webp|gif);base64,", image, re.I):
                continue
            if len(image) > 2_600_000:
                continue
            out.append(image)
            if len(out) >= 4:
                break
        return out

    def _persist_design(self, result: ChatTurnResult) -> None:
        try:
            self.store.save_design(
                self.session.session_id, _clean_spec(self.studio.spec),
                checksum=str(self.studio.solved.get("derived.checksum") or ""),
                label=result.intent or "conversation",
                conversation_id=self.conversation_id)
        except Exception:  # noqa: BLE001 - persistence must not fail a turn
            pass

    def _persist_error(self, result: ChatTurnResult) -> None:
        try:
            assistant = self.store.add_message(
                self.conversation_id, "assistant", result.text,
                kind="assistant",
                data={"engine": result.engine, "status": result.status,
                      "error": result.error, "suggestions": []})
            result.assistant_message = self._visible(assistant)
            result.snapshot = self.studio.snapshot()
            result.session = self.session.conversation_payload()
            result.pending = self.session.pending
        except Exception:  # noqa: BLE001
            pass

    def _error_result(self, control: RunControl, status: str, error: str,
                      text: str) -> ChatTurnResult:
        return ChatTurnResult(
            conversation_id=self.conversation_id, run_id=control.run_id,
            text=text, engine="local rules", status=status, error=error,
            timeline=list(self._timeline), suggestions=["Try again"])

    def _visible(self, message: dict) -> dict:
        return {key: message.get(key) for key in
                ("id", "role", "content", "kind", "data", "created_at", "position")}

    def _suggestions(self, result: ChatTurnResult) -> list[str]:
        after = result.verification or {}
        if result.status == "awaiting_approval":
            return ["Approve those changes", "Show me the diff", "Explain the trade-offs"]
        if int(after.get("fail") or 0):
            return ["Why is that check failing?", "Fix the failing checks",
                    "What are my options?"]
        if result.changes:
            return ["Undo that", "Explain those changes", "Show me alternatives"]
        return ["Critique this design", "Show me alternatives",
                "What would titanium change?", "Build the production package"]

    def _maybe_summarize(self) -> None:
        rows = self.store.messages(self.conversation_id, visible_only=True)
        if len(rows) <= 34:
            return
        keep = 12
        old = rows[:-keep]
        if not old:
            return
        conversation = self.store.get_conversation(self.conversation_id) or {}
        previous = str(conversation.get("summary") or "")
        summary = self._summarize_rows(previous, old)
        self.store.update_summary(self.conversation_id, summary,
                                  int(old[-1].get("position") or 0))

    def _summarize_rows(self, previous: str, rows: list[dict]) -> str:
        user_goals = []
        decisions = []
        for row in rows:
            content = " ".join(str(row.get("content") or "").split())
            if not content:
                continue
            if row.get("role") == "user":
                user_goals.append(content[:180])
            else:
                data = row.get("data") or {}
                if data.get("changes"):
                    for change in data["changes"][:6]:
                        decisions.append(f"{change.get('path')} -> {change.get('to')}")
                elif any(word in content.lower() for word in
                         ("recommend", "suggest", "trade-off", "decided", "instead")):
                    decisions.append(content[:180])
        memory = self.session.memory
        bits = []
        if previous:
            bits.append("Earlier summary: " + previous[:1600].strip())
        if user_goals:
            bits.append("User goals and questions: " + "; ".join(user_goals[-10:]))
        if decisions:
            bits.append("Decisions and recommendations: "
                        + "; ".join(dict.fromkeys(decisions[-10:])))
        applied = [f"{c.get('path')} -> {c.get('to')}"
                   for c in (memory.applied_changes or [])[-6:] if isinstance(c, dict)]
        if applied:
            bits.append("Applied design changes: " + "; ".join(applied))
        if memory.constraints:
            bits.append("Constraints: " + "; ".join(memory.constraints[-8:]))
        bits.append("Current design: " + describe(self.studio.solved))
        checksum = self.studio.solved.get("derived.checksum") or ""
        if checksum:
            bits.append("Design checksum: " + str(checksum))
        return "\n".join(bits)[:6000]

    # -- events -----------------------------------------------------------
    def _emit(self, event: dict) -> None:
        if callable(self._emit_callback):
            try:
                self._emit_callback(event)
            except Exception:  # noqa: BLE001 - streaming must not break the run
                pass

    def _timeline_event(self, kind: str, title: str, detail: str = "", *,
                        status: str = "done", data: dict | None = None) -> None:
        event = TimelineEvent(kind=kind, title=title, detail=detail,
                              status=status, data=data or {})
        event.seq = len(self._timeline) + 1
        payload = event.to_dict()
        self._timeline.append(payload)
        self._emit({"type": "timeline", "event": payload})

    def _tool_event(self, event) -> None:
        if not isinstance(event, TimelineEvent):
            return
        event.seq = len(self._timeline) + 1
        payload = event.to_dict()
        self._timeline.append(payload)
        self._emit({"type": "timeline", "event": payload})
        if event.kind != "tool":
            return
        name = str((event.data or {}).get("tool") or "")
        label = TOOL_STATUS.get(name, "Using a tool")
        if event.status == "running":
            self._emit({"type": "status", "text": label, "tool": name,
                        "status": "running"})
        elif event.status in ("ok", "error"):
            self._emit({"type": "status",
                        "text": label + (" — done" if event.status == "ok"
                                         else " — failed"),
                        "tool": name, "status": event.status,
                        "detail": event.detail})

    def _orchestrator_event(self, event) -> None:
        self._tool_event(event)
