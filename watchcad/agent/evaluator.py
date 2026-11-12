"""Outcome evaluation and bounded repair candidate selection.

The agent must not claim success because a tool returned ok.  This module turns
the deterministic check lists into an honest verdict, guards locked fields, and
picks repair candidates only from the verifier's own fix hints.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import EvaluationResult


def failures(checks: list[dict]) -> list[dict]:
    return [c for c in (checks or []) if c.get("passed") is False]


def count_failures(checks: list[dict]) -> int:
    return len(failures(checks))


def failure_ids(checks: list[dict]) -> list[str]:
    return [str(c.get("id")) for c in failures(checks)]


def failure_signature(checks: list[dict]) -> tuple:
    return tuple(sorted((c.get("id"), c.get("detail"))
                        for c in failures(checks)))


def is_improved(before_checks: list[dict], after_checks: list[dict]) -> bool:
    before = count_failures(before_checks)
    after = count_failures(after_checks)
    if after < before:
        return True
    if after > before:
        return False
    return failure_signature(before_checks) != failure_signature(after_checks)


def protected_intact(locks_before, locks_after, changes: list[dict]) -> bool:
    """No committed change may touch a field that was locked before the run."""
    protected = set(locks_before or [])
    touched = {row.get("path") for row in (changes or [])}
    if protected & touched:
        return False
    # Locks may be added or removed only by an explicit lock tool call; a normal
    # spec change must preserve the existing set.
    return True


@dataclass
class RepairCandidate:
    path: str
    value: object
    check_id: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "value": self.value,
                "check": self.check_id, "reason": self.reason}


def repair_candidates(checks: list[dict], locks, *, seen: set | None = None) -> list[RepairCandidate]:
    """Return generic candidates drawn from verification fix hints.

    The verifier owns the engineering suggestion.  The agent only decides
    whether to try it, and never hardcodes a per-watch repair.
    """
    locked = set(locks or [])
    seen = set(seen or set())
    out: list[RepairCandidate] = []
    for check in failures(checks):
        fix = check.get("fix") or {}
        path = fix.get("path")
        if not path or path in locked or path.startswith("derived."):
            continue
        if "value" not in fix:
            continue
        key = (path, repr(fix.get("value")))
        if key in seen:
            continue
        seen.add(key)
        out.append(RepairCandidate(
            path=path, value=fix.get("value"), check_id=str(check.get("id")),
            reason=str(check.get("title") or "verification fix hint")))
    return out


def evaluate(*, before_checks: list[dict], after_checks: list[dict],
             changes: list[dict], locks_before, locks_after,
             intent: str, committed: bool, staged: bool,
             verification_completed: bool, geometry_rebuilt: bool,
             questions: list[str] | None = None,
             notes: list[str] | None = None) -> EvaluationResult:
    before = count_failures(before_checks)
    after = count_failures(after_checks)
    result = EvaluationResult(
        intent_understood=bool(intent),
        plan_generated=True,
        changes_proposed=bool(changes),
        changes_applied=bool(committed and changes),
        geometry_rebuilt=bool(geometry_rebuilt),
        verification_completed=bool(verification_completed),
        objective_achieved=False,
        protected_intact=protected_intact(locks_before, locks_after, changes),
        failures_before=before,
        failures_after=after,
    )
    if questions:
        result.notes.append("needs input: " + "; ".join(questions[:3]))
    for note in notes or []:
        if note and note not in result.notes:
            result.notes.append(note)

    if not verification_completed:
        result.unresolved.append("verification did not complete")
        return result
    if not result.protected_intact:
        result.unresolved.append("a locked field was touched")
        return result
    if questions and not changes:
        return result

    if intent in ("explain", "critic", "inspect", "variations", "undo", "redo"):
        result.objective_achieved = True
        return result
    if intent in ("verify", "build"):
        result.objective_achieved = after == 0 or intent == "build"
        if after:
            result.unresolved.extend(failure_ids(after_checks))
        return result
    if staged:
        # The objective is met only once the user approves and the committed
        # design verifies; the proposal itself is complete.
        result.objective_achieved = changes and after <= before and not (
            after > before)
        if after:
            result.unresolved.extend(failure_ids(after_checks))
        return result
    if changes and after == 0:
        result.objective_achieved = True
    elif changes and after < before:
        result.notes.append(f"improved from {before} to {after} failing check(s), "
                            "but the design is not fully clean")
        result.unresolved.extend(failure_ids(after_checks))
    elif changes and after == before and after > 0:
        result.notes.append("the change did not resolve the failing checks")
        result.unresolved.extend(failure_ids(after_checks))
    elif not changes:
        result.unresolved.append("no validated change was produced")
    else:
        result.unresolved.extend(failure_ids(after_checks))
    return result


def successful(result: EvaluationResult) -> bool:
    return bool(result.objective_achieved and result.verification_completed
                and result.protected_intact and result.failures_after == 0)
