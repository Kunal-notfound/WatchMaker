"""Autonomous design orchestrator.

The loop is deliberately boring and inspectable:

    understand -> read state -> request validated changes -> solve -> verify
    -> repair within a bounded budget -> evaluate -> commit / stage / roll back

The live design is only replaced after verification.  All actions pass through
the :class:`ToolExecutor`, and the agent never sees or creates geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import json
import threading

from .. import llm as llm_module
from ..llm import LLMConfig, LLMError, LLMReply
from ..schema import Spec
from .context import AgentCancelled, AgentTimeout, RunControl
from .evaluator import (
    count_failures, evaluate, failure_ids, failures, is_improved,
    repair_candidates,
)
from .models import (
    AgentRunResult, AgentState, PlanStep, SpecChange, TimelineEvent, ToolCall,
)
from .planner import (
    Intent, build_variants, detect_intent, initial_message, plan_local,
    patches_from_request,
)
from .prompts import SYSTEM_PROMPT, context_message
from .tools import openai_tools
from .tool_executor import ToolExecutor


VALID_MODES = {"suggest", "approval", "safe", "autonomous"}
_MODE_ALIASES = {"suggest-only": "suggest", "suggest_only": "suggest",
                 "require-approval": "approval", "require_approval": "approval",
                 "manual": "approval", "auto": "autonomous"}


def normalize_mode(value: str | None) -> str:
    text = str(value or "autonomous").strip().lower()
    text = _MODE_ALIASES.get(text, text)
    return text if text in VALID_MODES else "autonomous"


@dataclass
class RunOptions:
    mode: str = "autonomous"
    use_llm: bool | None = None
    max_iterations: int = 3
    max_tool_rounds: int = 8
    name: str | None = None
    build_root: str | None = None


class AgentOrchestrator:
    """Run one bounded agent request against a session."""

    def __init__(self, session, *, llm_client=None, llm_config: LLMConfig | None = None,
                 build_root: str | None = None):
        self.session = session
        self.studio = session.studio
        self.llm_client = llm_client
        self.llm_config = llm_config or LLMConfig.from_env()
        self.build_root = build_root
        self.state: AgentState | None = None
        self._callback = None
        self._executor: ToolExecutor | None = None

    # -- public -----------------------------------------------------------
    def run(self, request: str, *, mode: str = "autonomous",
            use_llm: bool | None = None, max_iterations: int = 3,
            max_tool_rounds: int = 8, name: str | None = None,
            emit=None, control: RunControl | None = None) -> AgentRunResult:
        options = RunOptions(mode=normalize_mode(mode), use_llm=use_llm,
                             max_iterations=max(0, min(8, int(max_iterations))),
                             max_tool_rounds=max(1, min(16, int(max_tool_rounds))),
                             name=name, build_root=self.build_root)
        return self._run(request, options, emit=emit, control=control)

    # -- setup ------------------------------------------------------------
    def _run(self, request: str, options: RunOptions, *, emit=None,
             control: RunControl | None = None) -> AgentRunResult:
        request = (request or "").strip()
        state = AgentState(session_id=self.session.session_id, objective=request,
                           mode=options.mode)
        self.state = state
        self._callback = emit
        control = control or RunControl()
        intent = detect_intent(request)
        state.intent = intent.name

        with self.session._lock:
            before_spec = self.studio.spec.copy()
            before_checks = list(self.studio.checks or [])
            locks_before = set(before_spec.locks)
            state.before_checksum = before_spec.get("derived.checksum") or ""
            self._emit(TimelineEvent(
                kind="run", title="Agent run started",
                detail=request[:220], status="running",
                data={"mode": options.mode, "intent": intent.name}))

            use_llm = (self.llm_config.available if options.use_llm is None
                       else bool(options.use_llm))
            engine = "rules+llm" if use_llm and self.llm_config.available else "rules"
            state.engine = engine
            self._emit(TimelineEvent(
                kind="plan", title="Understand the request",
                detail=f"{intent.name} intent, {options.mode} mode",
                data={"intent": intent.to_dict(), "engine": engine}))

            # Local plan always exists: it is the fallback and the budget bound.
            plan = plan_local(request, self.studio, self.session,
                              mode=options.mode, use_llm=False)
            state.plan = [step.to_dict() for step in plan.steps]
            if plan.notes:
                state.notes.extend(plan.notes)
            if plan.questions:
                state.questions.extend(plan.questions)
            state.iterations = 0

            self._executor = ToolExecutor(
                studio=self.studio, session=self.session, emit=self._emit,
                control=control, build_root=options.build_root or self.build_root,
                approval_mode=options.mode, max_calls=90)
            executor = self._executor

            final_text = ""
            try:
                if intent.name in ("undo", "redo"):
                    final_text = self._history_action(intent.name)
                elif engine == "rules+llm":
                    try:
                        final_text = self._llm_loop(request, state, executor, intent,
                                                    options, control)
                    except (LLMError, OSError, TimeoutError) as exc:
                        self._emit(TimelineEvent(
                            kind="fallback", title="Model unavailable, using the local planner",
                            detail=str(exc)[:240], status="warning"))
                        state.notes.append(f"{type(exc).__name__}: {exc}; "
                                           "fell back to the deterministic planner")
                        executor.rollback()
                        state.engine = "rules (llm fallback)"
                        final_text = self._execute_local(request, state, plan, executor,
                                                         intent, options, control)
                else:
                    final_text = self._execute_local(request, state, plan, executor,
                                                     intent, options, control)
            except AgentCancelled as exc:
                executor.rollback()
                state.status = "cancelled"
                state.error = str(exc)
                final_text = "Stopped. No changes were committed."
                self._emit(TimelineEvent(kind="cancel", title="Run stopped",
                                         detail=str(exc), status="cancelled"))
            except AgentTimeout as exc:
                executor.rollback()
                state.status = "timeout"
                state.error = str(exc)
                final_text = "The run exceeded its time budget. No changes were committed."
                self._emit(TimelineEvent(kind="timeout", title="Run timed out",
                                         detail=str(exc), status="error"))

            # Common finalisation for any path that left work in the executor.
            if state.status not in ("cancelled", "timeout"):
                final_text = self._finalize(request, state, executor, intent,
                                            before_checks, locks_before,
                                            options, final_text)

            state.after_checksum = self.studio.spec.get("derived.checksum") or ""
            state.finished_at = round(__import__("time").time(), 3)
            state.final_message = final_text
            if state.status in ("running", ""):
                state.status = "complete"
            self._emit(TimelineEvent(
                kind="done", title="Run finished",
                detail=state.status, status="done" if state.status == "complete" else "warning",
                data={"evaluation": state.evaluation.to_dict(),
                      "checksum": state.after_checksum}))

            self._remember(request, state)
        return AgentRunResult(state=state, response=final_text,
                              applied=len([c for c in state.changes
                                           if c.path != "locks"]))

    # -- local execution ---------------------------------------------------
    def _execute_local(self, request: str, state: AgentState, plan, executor: ToolExecutor,
                       intent: Intent, options: RunOptions,
                       control: RunControl) -> str:
        results = []
        for index, step in enumerate(plan.steps):
            control.check()
            if step.tool in ("undo", "redo"):
                continue
            call = ToolCall(name=step.tool, arguments=step.arguments,
                            reason=step.reason)
            result = executor.execute(call)
            results.append(result)
            step.status = "done" if result.ok else "error"
            plan.steps[index] = step
            state.plan = [s.to_dict() for s in plan.steps]
            if not result.ok:
                state.notes.append(f"{step.tool}: {result.error or result.message}")
                if step.tool in ("apply_patch", "update_spec"):
                    # A rejected change is a hard stop for modifications.
                    break

        # A repair request has no patch step: repair from the current failures.
        if intent.name == "repair":
            self._repair(executor, state, options.max_iterations)
        elif executor.has_changes() and options.mode != "suggest":
            # Bounded autonomous repair after a requested change.
            self._repair(executor, state, options.max_iterations)

        if intent.name in ("explain", "critic", "inspect"):
            return initial_message(intent, self.studio, results)
        if intent.name == "variations":
            state.variants = build_variants(self.studio)
            if not state.variants:
                state.notes.append("no valid alternative could be generated")
                return "I could not build a coherent alternative from the current design."
            labels = ", ".join(v["label"] for v in state.variants)
            return (f"I prepared {len(state.variants)} alternatives: {labels}. "
                    "None is applied yet; choose one to review its diff and checksum.")
        if intent.name == "verify":
            report = _last_data(results, "get_verification_report") or {}
            if report:
                summary = report.get("summary") or {}
                failing = report.get("failures") or []
                text = (f"Verification: {summary.get('fail', 0)} failing, "
                        f"{summary.get('calculated', 0)} calculated, "
                        f"{summary.get('estimated', 0)} estimated, "
                        f"{summary.get('needs_test', 0)} need bench testing.")
                if failing:
                    text += " Failing: " + ", ".join(str(c.get("id")) for c in failing[:6]) + "."
                return text
        if intent.name == "build":
            build_data = _last_data(results, "build_package") or {}
            if build_data.get("build"):
                build = build_data["build"]
                return (f"Package generated with {len(build.get('files', []))} files "
                        f"at {build_data.get('root')}.")
        return ""

    # -- LLM execution -----------------------------------------------------
    def _llm_loop(self, request: str, state: AgentState, executor: ToolExecutor,
                  intent: Intent, options: RunOptions, control: RunControl) -> str:
        memory = self.session.memory
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": context_message(
                self.studio.solved.summary(),
                mode=options.mode, engine="rules+llm",
                history=json.dumps(memory.to_dict(), default=str)[:1800],
                constraints=memory.constraints,
                locks=sorted(self.studio.spec.locks))},
        ]
        messages.extend(self.session.conversation.as_llm_messages(10))
        messages.append({"role": "user", "content": request})
        final = ""
        for round_index in range(options.max_tool_rounds):
            control.check()
            reply = self._call_llm(messages, openai_tools())
            if not reply.has_tool_calls:
                final = reply.content.strip()
                break
            messages.append(llm_module.assistant_tool_message(reply))
            for call in reply.tool_calls:
                control.check()
                tool_call = ToolCall(name=call.get("name", ""),
                                     arguments=call.get("arguments") or {},
                                     call_id=call.get("id") or "",
                                     reason="model-selected step")
                result = executor.execute(tool_call)
                state.plan.append(PlanStep(
                    title=f"{tool_call.name}", tool=tool_call.name,
                    arguments=tool_call.arguments, reason=tool_call.reason,
                    status="done" if result.ok else "error").to_dict())
                payload = {
                    "ok": result.ok,
                    "tool": result.name,
                    "message": result.message,
                    "error": result.error,
                    "data": _trim(result.data),
                }
                messages.append(llm_module.tool_result_message(
                    tool_call.call_id, tool_call.name, payload))
            if control.cancelled:
                raise AgentCancelled("run cancelled by the user")
        else:
            final = ""
            state.notes.append("the model reached the tool-round budget")

        # The model may stop without calling the deterministic steps.  The
        # orchestrator always closes the loop itself.
        if executor.has_changes():
            self._ensure_verified(executor, state)
            if count_failures(executor.working_checks()) and options.mode != "suggest":
                self._repair(executor, state, options.max_iterations)
        return final

    def _call_llm(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        client = self.llm_client or llm_module
        chat = getattr(client, "chat", None) or client
        try:
            reply = chat(messages, tools=tools, config=self.llm_config)
        except TypeError:
            reply = chat(messages, tools=tools)
        if isinstance(reply, LLMReply):
            return reply
        if isinstance(reply, dict):
            return LLMReply(
                content=str(reply.get("content") or ""),
                tool_calls=list(reply.get("tool_calls") or []),
                raw=reply,
                model=str(reply.get("model") or self.llm_config.model),
            )
        raise LLMError("the configured model client returned an unsupported reply")

    # -- verification and repair -------------------------------------------
    def _ensure_verified(self, executor: ToolExecutor, state: AgentState) -> None:
        solver = executor.execute(ToolCall(name="run_solver", reason="close the solve loop"))
        verify = executor.execute(ToolCall(name="run_verification",
                                           reason="close the verification loop"))
        if not solver.ok:
            state.notes.append("solver failed: " + (solver.error or solver.message))
        if not verify.ok:
            state.notes.append("verification failed to run: "
                               + (verify.error or verify.message))

    def _repair(self, executor: ToolExecutor, state: AgentState, budget: int) -> None:
        budget = max(0, min(8, int(budget or 0)))
        if budget == 0:
            return
        tried: set = set()
        for iteration in range(1, budget + 1):
            state.iterations = iteration
            executor.iteration = iteration
            checks = executor.working_checks()
            current = failures(checks)
            if not current:
                break
            locks = executor._target().spec.locks
            candidates = repair_candidates(checks, locks, seen=tried)
            if not candidates:
                state.notes.append("no verifier fix hint could be applied safely")
                self._emit(TimelineEvent(
                    kind="repair", title=f"Repair {iteration}: no safe candidate",
                    detail="; ".join(failure_ids(checks)),
                    status="warning", iteration=iteration))
                break
            improved = False
            for candidate in candidates[:6]:
                control = executor.control
                if control is not None:
                    control.check()
                target = executor._ensure_working()
                snapshot = target.spec.copy()
                before_count = count_failures(target.checks)
                result = executor.execute(ToolCall(
                    name="update_spec",
                    arguments={"path": candidate.path, "value": candidate.value,
                               "note": f"repair {candidate.check_id}"},
                    reason=candidate.reason))
                if not result.ok:
                    _restore_shadow(target, snapshot)
                    continue
                after = executor.working_checks()
                if count_failures(after) < before_count:
                    improved = True
                    state.repair_attempts.append(_attempt(
                        iteration, before_count, count_failures(after),
                        [{"path": candidate.path, "value": candidate.value,
                          "check": candidate.check_id}], True,
                        f"applied the {candidate.check_id} fix hint"))
                    self._emit(TimelineEvent(
                        kind="repair", title=f"Repair {iteration}: {candidate.check_id}",
                        detail=f"set {candidate.path} = {candidate.value} "
                               f"({before_count} -> {count_failures(after)} failing)",
                        status="ok", iteration=iteration,
                        data={"path": candidate.path, "value": candidate.value}))
                    break
                # No net improvement: revert, and try the next hint.
                _restore_shadow(target, snapshot)
                tried.add((candidate.path, repr(candidate.value)))
            if not improved:
                state.repair_attempts.append(_attempt(
                    iteration, count_failures(checks), count_failures(executor.working_checks()),
                    [], False, "no fix hint reduced the failure count"))
                self._emit(TimelineEvent(
                    kind="repair", title=f"Repair {iteration} made no progress",
                    detail="reverted the attempted changes",
                    status="warning", iteration=iteration))
                break
            self._ensure_verified(executor, state)
        state.iterations = max(state.iterations, 0)

    # -- finalisation ------------------------------------------------------
    def _finalize(self, request: str, state: AgentState, executor: ToolExecutor,
                  intent: Intent, before_checks: list[dict], locks_before: set,
                  options: RunOptions, final_text: str) -> str:
        changes = executor.diff()
        if changes:
            # Verification is a precondition for any evaluation or commit.
            self._ensure_verified(executor, state)
        after_checks = executor.working_checks()
        after_spec = executor.working_spec()
        state.changes = [
            SpecChange(path=c["path"], before=c.get("from"), after=c.get("to"),
                       note="agent change" if c["path"] != "locks" else "lock change")
            for c in changes
        ]
        state.verification_before = _summary(before_checks)
        state.verification_after = _summary(after_checks)
        state.after_checksum = (after_spec.get("derived.checksum") or "")

        committed = False
        staged = False
        if changes and options.mode == "suggest":
            state.status = "suggested"
        elif changes and options.mode == "approval":
            ops = [{"op": "replace", "path": c["path"], "value": c.get("to")}
                   for c in changes if c["path"] != "locks"]
            token = self.session.stage(
                request=request, mode=options.mode, spec=executor.working.spec,
                ops=ops, agent_state=state.to_dict(),
                notes=state.notes, checks=after_checks)
            state.pending_token = token
            state.staged_ops = ops
            state.status = "awaiting_approval"
            staged = True
        elif changes:
            safe_to_commit = (len(failures(after_checks)) <= len(failures(before_checks))
                              and not (locks_before & {c["path"] for c in changes}))
            if safe_to_commit or options.mode == "autonomous":
                result = executor.commit("Agent change")
                committed = bool(result.get("applied"))
                state.status = "complete"
                self.session.checkpoint("Agent: " + request[:70], "agent")
                self._emit(TimelineEvent(
                    kind="commit", title="Changes committed",
                    detail=f"{result.get('applied', 0)} field(s); checksum "
                           f"{str(result.get('checksum'))[:8]}",
                    status="ok", data={"changes": changes}))
            else:
                executor.rollback()
                state.status = "rolled_back"
                state.notes.append("the change increased the failure count, so it was "
                                   "rolled back rather than committed")
                self._emit(TimelineEvent(
                    kind="rollback", title="Change rolled back",
                    detail="verification did not improve", status="warning"))
        else:
            state.status = "complete" if intent.name != "repair" else "needs_attention"

        evaluate_kwargs = dict(
            before_checks=before_checks, after_checks=after_checks,
            changes=changes, locks_before=locks_before,
            locks_after=set((executor.working.spec.locks if executor.working
                             else self.studio.spec.locks)),
            intent=intent.name, committed=committed, staged=staged,
            verification_completed=bool(changes) or bool(before_checks),
            geometry_rebuilt=bool(changes), questions=state.questions,
            notes=state.notes)
        state.evaluation = evaluate(**evaluate_kwargs)
        if state.evaluation.unresolved:
            state.unresolved = list(dict.fromkeys(state.evaluation.unresolved))
        state.changes = [c for c in state.changes if c.path != "locks"]

        return self._compose_message(final_text, state, intent, changes, options)

    def _compose_message(self, final_text: str, state: AgentState, intent: Intent,
                         changes: list[dict], options: RunOptions) -> str:
        before = state.verification_before
        after = state.verification_after
        failing = after.get("fail", 0) if after else 0
        quiet_intents = {"explain", "critic", "inspect", "variations", "undo", "redo"}
        bits = []
        if final_text:
            bits.append(final_text.strip())

        if state.status == "awaiting_approval":
            bits.append(f"I prepared {len(changes)} validated change(s) and stopped for "
                        "your approval. Nothing has been written to the live design.")
        elif state.status == "suggested":
            bits.append(f"I prepared {len(changes)} proposed change(s) without applying "
                        "them. Switch to approval or autonomous mode to execute.")
        elif state.status == "rolled_back":
            bits.append("I did not commit the change because verification got worse.")
        elif changes and state.status == "complete":
            bits.append(f"Applied {len(changes)} change(s).")
        elif intent.name == "variations" and not final_text:
            bits.append(f"{len(state.variants)} alternatives prepared; none applied.")
        elif intent.name in quiet_intents:
            pass
        elif intent.name == "repair":
            bits.append("Repair finished." if not failing else
                        "Repair could not clear every failing check.")
        else:
            if not changes:
                bits.append("No validated specification change was produced.")

        if after and intent.name not in ("undo", "redo"):
            bits.append(f"Verification now: {after.get('calculated', 0)} calculated, "
                        f"{after.get('estimated', 0)} estimated, "
                        f"{after.get('needs_test', 0)} need bench testing, "
                        f"{after.get('fail', 0)} failing.")
            if before and before.get("fail") != after.get("fail"):
                bits.append(f"Failures moved from {before.get('fail', 0)} to "
                            f"{after.get('fail', 0)}.")
        if state.questions and intent.name not in quiet_intents:
            bits.append("I need one answer: " + state.questions[0])
        if state.unresolved and intent.name not in quiet_intents:
            bits.append("Still unresolved: " + ", ".join(state.unresolved[:6]) + ".")
        notes = [n for n in state.notes if n]
        if notes and intent.name not in ("undo", "redo"):
            bits.append("Notes: " + "; ".join(dict.fromkeys(notes[-4:])) + ".")
        if state.status == "complete" and after and after.get("fail", 0) == 0 and changes:
            bits.append("The deterministic checks pass; needs_test items still require "
                        "the bench.")
        return " ".join(bits).strip()

    # -- history -----------------------------------------------------------
    def _history_action(self, intent: str) -> str:
        if intent == "undo":
            target = self.session.undo()
            if target is None:
                return "There is no earlier version to restore."
            self._emit(TimelineEvent(kind="history", title="Restored an earlier version",
                                     detail=target.label, status="ok",
                                     data=target.to_dict()))
            return f"Restored '{target.label}'."
        target = self.session.redo()
        if target is None:
            return "There is no newer version to restore."
        self._emit(TimelineEvent(kind="history", title="Restored a newer version",
                                 detail=target.label, status="ok",
                                 data=target.to_dict()))
        return f"Restored '{target.label}'."

    # -- plumbing ----------------------------------------------------------
    def _emit(self, event: TimelineEvent) -> None:
        if self.state is None:
            return
        event.seq = len(self.state.timeline) + 1
        self.state.timeline.append(event)
        if callable(self._callback):
            try:
                self._callback(event.to_dict())
            except Exception:  # noqa: BLE001 - streaming must not break the run
                pass

    def _remember(self, request: str, state: AgentState) -> None:
        try:
            self.session.conversation.user(request)
            self.session.conversation.assistant(
                state.final_message, kind="agent",
                data={"run_id": state.run_id, "status": state.status,
                      "intent": state.intent, "changes": len(state.changes),
                      "checksum": state.after_checksum})
            self.session.remember_request(
                request, intent=state.intent,
                changes=[c.to_dict() for c in state.changes],
                verification=state.verification_after,
                constraints=state.notes if state.intent == "modify" else None)
            self.session.last_agent = state.to_dict()
        except Exception:  # noqa: BLE001 - memory must not invalidate a run
            pass


# ---------------------------------------------------------------------------
# Helpers and public entry points
# ---------------------------------------------------------------------------

def _summary(checks: list[dict]) -> dict:
    from ..verify import summary

    return summary(checks or [])


def _last_data(results: list, name: str) -> dict:
    for result in reversed(results):
        if getattr(result, "name", "") == name and getattr(result, "ok", False):
            return getattr(result, "data", {}) or {}
    return {}


def _trim(data, limit: int = 12000):
    if not data:
        return {}
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        return {}
    if len(text) <= limit:
        return data
    return {"truncated": True, "preview": text[:limit]}


def _restore_shadow(target, spec: Spec) -> None:
    target.spec = spec.copy()
    target.last_build = None
    target.rebuild()


def _attempt(iteration: int, before: int, after: int, actions: list[dict],
             improved: bool, note: str):
    from .models import RepairAttempt

    return RepairAttempt(iteration=iteration, failures_before=before,
                         failures_after=after, actions=actions,
                         improved=improved, note=note)


def run_agent(session, request: str, *, mode: str = "autonomous",
              use_llm: bool | None = None, max_iterations: int = 3,
              emit=None, control: RunControl | None = None,
              llm_client=None, llm_config: LLMConfig | None = None,
              build_root: str | None = None) -> AgentRunResult:
    orchestrator = AgentOrchestrator(session, llm_client=llm_client,
                                     llm_config=llm_config,
                                     build_root=build_root)
    return orchestrator.run(request, mode=mode, use_llm=use_llm,
                            max_iterations=max_iterations, emit=emit,
                            control=control)


def apply_variant(session, variant: dict, *, mode: str = "autonomous",
                  emit=None, control: RunControl | None = None,
                  llm_config: LLMConfig | None = None) -> AgentRunResult:
    """Apply one prepared alternative through the same validated pipeline."""
    mode = normalize_mode(mode)
    changes = variant.get("changes") or []
    ops = [{"op": "replace", "path": c.get("path"), "value": c.get("to")}
           for c in changes if c.get("path")]
    request = "Apply the '" + str(variant.get("label", "alternative")) + "' alternative"
    control = control or RunControl()
    state = AgentState(session_id=session.session_id, objective=request,
                       mode=mode, intent="modify", engine="local rules")
    with session._lock:
        before_spec = session.studio.spec.copy()
        before_checks = list(session.studio.checks or [])
        locks_before = set(before_spec.locks)
        state.before_checksum = before_spec.get("derived.checksum") or ""
        executor = ToolExecutor(studio=session.studio, session=session,
                                emit=emit, control=control, approval_mode=mode)
        result = executor.execute(ToolCall(
            name="apply_patch", arguments={"ops": ops,
                                           "note": "selected design alternative"},
            reason="the user selected this alternative"))
        if not result.ok:
            executor.rollback()
            state.status = "error"
            state.error = result.error or result.message
            state.final_message = "The alternative could not be applied: " + state.error
            return AgentRunResult(state=state, response=state.final_message, applied=0)
        executor.execute(ToolCall(name="run_solver", reason="variant selected"))
        executor.execute(ToolCall(name="run_verification", reason="variant selected"))
        if count_failures(executor.working_checks()) and mode != "suggest":
            orchestrator = AgentOrchestrator(session, llm_config=llm_config)
            orchestrator.state = state
            orchestrator._callback = emit
            orchestrator._executor = executor
            orchestrator._repair(executor, state, 3)
        changes_out = executor.diff()
        after_checks = executor.working_checks()
        committed = False
        staged = False
        if changes_out and mode == "suggest":
            state.status = "suggested"
        elif changes_out and mode == "approval":
            token = session.stage(
                request=request, mode=mode, spec=executor.working.spec, ops=ops,
                agent_state=state.to_dict(), notes=state.notes,
                checks=after_checks)
            state.pending_token = token
            state.staged_ops = ops
            state.status = "awaiting_approval"
            staged = True
        elif changes_out:
            executor.commit("Selected alternative")
            session.checkpoint(request, "agent")
            committed = True
            state.status = "complete"
        state.changes = [SpecChange(path=c["path"], before=c.get("from"),
                                    after=c.get("to"))
                         for c in changes_out if c["path"] != "locks"]
        state.verification_before = _summary(before_checks)
        state.verification_after = _summary(after_checks)
        state.after_checksum = executor.working_spec().get("derived.checksum") or ""
        state.evaluation = evaluate(
            before_checks=before_checks, after_checks=after_checks,
            changes=changes_out, locks_before=locks_before,
            locks_after=set((executor.working.spec.locks if executor.working
                             else self.studio.spec.locks)), intent="modify",
            committed=committed, staged=staged, verification_completed=True,
            geometry_rebuilt=True)
        state.unresolved = failure_ids(after_checks)
        if state.status == "complete":
            message = (f"Applied '{variant.get('label')}'. Verification: "
                       f"{state.verification_after.get('fail', 0)} failing.")
        elif state.status == "awaiting_approval":
            message = "The alternative is waiting for approval."
        else:
            message = "The alternative was prepared but not committed."
        state.final_message = message
        session.conversation.user(request)
        session.conversation.assistant(message, kind="agent",
                                       data={"variant": variant.get("label")})
        session.last_agent = state.to_dict()
        return AgentRunResult(state=state, response=message,
                              applied=len(state.changes))
