"""Intent detection and the deterministic local planner.

When no model is configured (and whenever the model fails) this module produces
a complete, bounded run: it reads the relevant state, derives validated patches
from the deterministic prompt parser, then solves and verifies.  The same intent
detection is used to label runs made by the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import re
from typing import Any

from ..nlp import parse
from ..schema import FIELDS, Spec
from ..solver import solve
from ..verify import summary as verify_summary
from ..verify import verify
from .models import PlanStep


@dataclass
class Intent:
    name: str = "modify"
    objective: str = ""
    confidence: float = 0.7
    notes: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "objective": self.objective,
                "confidence": self.confidence, "notes": self.notes,
                "params": self.params}


@dataclass
class LocalPlan:
    intent: Intent
    steps: list[PlanStep] = field(default_factory=list)
    patches: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    engine: str = "rules"


_REPAIR = re.compile(r"\b(fix|repair|resolve|correct|failing|failures?|"
                     r"issues?|errors?|problems?|fails?)\b", re.I)
_CRITIC = re.compile(r"\b(critique|critic|review|assess|weakness|weaknesses|"
                     r"what.s wrong|risks?|trade-?offs?)\b", re.I)
_EXPLAIN = re.compile(r"\b(explain|why|how does|how do|what does|what is|"
                      r"tell me about|walk me through|reason)\b", re.I)
_VARIATIONS = re.compile(r"\b(variations?|alternatives?|options?|versions?|"
                         r"different directions?|explore|what if)\b", re.I)
_BUILD = re.compile(r"\b(build|package|export|manufacture|production files?|"
                    r"download)\b", re.I)
_VERIFY = re.compile(r"\b(verify|verification|validate|check it|checks?|"
                     r"inspect the report)\b", re.I)
_INSPECT = re.compile(r"\b(inspect|show me|list|what components?|geometry|"
                      r"part count|mass|weight)\b", re.I)
_REFERENCE = re.compile(r"\b(reference|like a|similar to|homage|inspired by|"
                        r"based on)\b", re.I)
_UNDO = re.compile(r"\b(undo|revert|roll back|go back|previous version)\b", re.I)
_REDO = re.compile(r"\b(redo|restore|forward)\b", re.I)
_MODIFY = re.compile(r"\b(make|change|set|use|add|remove|swap|switch|"
                     r"should be|want|give it|design|build me)\b", re.I)


def detect_intent(request: str) -> Intent:
    text = (request or "").strip()
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    objective = text[:240]

    if _UNDO.search(low):
        return Intent("undo", objective, 0.95)
    if _REDO.search(low):
        return Intent("redo", objective, 0.9)
    if _VARIATIONS.search(low):
        return Intent("variations", objective, 0.9)
    if _CRITIC.search(low):
        return Intent("critic", objective, 0.9)
    if _REPAIR.search(low):
        return Intent("repair", objective, 0.85)
    if _VERIFY.search(low) and not _MODIFY.search(low):
        return Intent("verify", objective, 0.8)
    if _INSPECT.search(low) and not _MODIFY.search(low):
        return Intent("inspect", objective, 0.75)
    if _EXPLAIN.search(low):
        return Intent("explain", objective, 0.85)
    if _BUILD.search(low) and not _MODIFY.search(low):
        return Intent("build", objective, 0.85)
    if _REFERENCE.search(low):
        return Intent("reference", objective, 0.75)
    return Intent("modify", objective, 0.6)


# ---------------------------------------------------------------------------
# Patch derivation
# ---------------------------------------------------------------------------

def patches_from_request(request: str, studio, *, use_llm: bool = False,
                         name: str | None = None) -> dict:
    """Run the deterministic parser (or its optional LLM assist) on a spec."""
    result = parse(request, base=studio.spec, use_llm=use_llm, name=name)
    ops = []
    locks = set(studio.spec.locks)
    for patch in result.patches:
        path = patch.get("path")
        if not path or path in locks:
            continue
        if path.startswith("derived."):
            continue
        ops.append({"op": "replace", "path": path, "value": patch.get("value"),
                    "note": patch.get("note", "request interpretation")})
    return {"ops": ops, "patches": result.patches, "questions": result.questions,
            "notes": result.notes, "engine": result.engine,
            "skipped": result.skipped}


def _step(title: str, tool: str = "", arguments: dict | None = None,
          reason: str = "", iteration: int = 0) -> PlanStep:
    return PlanStep(title=title, tool=tool, arguments=arguments or {},
                    reason=reason, iteration=iteration)


def plan_local(request: str, studio, session=None, *, mode: str = "autonomous",
               use_llm: bool = False) -> LocalPlan:
    """A complete local plan for the current request."""
    intent = detect_intent(request)
    plan = LocalPlan(intent=intent, notes=list(intent.notes))
    locks = sorted(studio.spec.locks)

    if intent.name == "undo":
        plan.steps.append(_step("Restore the previous design version", "undo",
                                reason="the user asked to revert"))
        return plan
    if intent.name == "redo":
        plan.steps.append(_step("Restore the next design version", "redo",
                                reason="the user asked to go forward"))
        return plan

    if intent.name == "variations":
        plan.steps.append(_step("Read the current design", "get_current_spec",
                                {"full": False}, "a base is needed for alternatives"))
        plan.notes.append("alternatives are proposals; none is applied automatically")
        return plan

    if intent.name == "verify":
        plan.steps.append(_step("Read the current verification report",
                                "get_verification_report", {},
                                "the user asked for the checks"))
        plan.steps.append(_step("Re-run the deterministic verification suite",
                                "run_verification", {},
                                "verification must be fresh"))
        return plan

    if intent.name == "build":
        plan.steps.append(_step("Solve the current specification", "run_solver",
                                {}, "the package must be built from a solved spec"))
        plan.steps.append(_step("Verify before packaging", "run_verification", {},
                                "never package an unverified design"))
        plan.steps.append(_step("Generate the production package", "build_package",
                                {"per_part": True}, "the user asked for the package"))
        return plan

    if intent.name in ("explain", "critic", "inspect"):
        plan.steps.append(_step("Read the current specification", "get_current_spec",
                                {"full": False}, "understand the current design"))
        plan.steps.append(_step("Read the verification report",
                                "get_verification_report", {},
                                "separate calculated results from estimates and tests"))
        if intent.name in ("critic", "inspect"):
            plan.steps.append(_step("Inspect the generated geometry",
                                    "inspect_geometry", {"include_bounds": False},
                                    "judge proportions, mass and train layout"))
        if intent.name == "inspect":
            plan.steps.append(_step("Read design history and memory",
                                    "get_design_history", {"limit": 10},
                                    "show what changed recently"))
        return plan

    if intent.name == "repair":
        plan.steps.append(_step("Read the failing checks", "get_verification_report",
                                {"failures_only": True},
                                "repair must start from the actual failure"))
        plan.notes.append("repair is bounded and will not touch locked fields")
        return plan

    # modify or reference
    parsed = patches_from_request(request, studio, use_llm=use_llm)
    plan.engine = parsed["engine"]
    plan.notes.extend(parsed["notes"])
    plan.patches = parsed["patches"]
    # Questions are useful when the request produced no change.  In an existing
    # session the essentials are already answered, so do not re-ask them after
    # every successful edit.
    if not parsed["ops"]:
        plan.questions.extend(parsed["questions"])
    plan.steps.append(_step("Read the current specification", "get_current_spec",
                            {"full": False}, "changes are relative to the current design"))
    if intent.name == "reference":
        plan.steps.append(_step("Look up reference material", "lookup_reference",
                                {"query": request[:160]},
                                "cite reference data instead of inventing numbers"))
    if parsed["ops"]:
        plan.steps.append(_step(
            f"Apply {len(parsed['ops'])} validated change(s)", "apply_patch",
            {"ops": parsed["ops"], "note": "agent interpretation of the request"},
            "only explicit, writable fields are changed"))
        plan.steps.append(_step("Recompute derived dimensions", "run_solver", {},
                                "geometry is a pure function of the spec"))
        plan.steps.append(_step("Verify the changed design", "run_verification", {},
                                "no success is claimed before verification"))
    else:
        plan.questions.append("Which specification field should change, and to what value?")
        plan.notes.append("the request did not map to a writable specification field")
    return plan


# ---------------------------------------------------------------------------
# Alternatives
# ---------------------------------------------------------------------------

_DIAL_COLORS = ["black", "blue", "silver", "white", "green", "burgundy", "salmon"]
_FINISHES = ["brushed", "polished", "bead-blasted", "two-tone"]
_BEZELS = ["plain", "dive", "fluted", "coin-edge"]


def _variant(base: Spec, label: str, rationale: str, changes: list[dict],
             visual: dict | None = None) -> dict:
    spec = base.copy()
    applied = []
    for change in changes:
        path = change.get("path")
        if not path or path in spec.locks or path not in FIELDS:
            continue
        before = spec.get(path)
        if before == change.get("value"):
            continue
        try:
            spec.set(path, change.get("value"), tag="agent", note="variant proposal")
            applied.append({"path": path, "from": before, "to": change.get("value")})
        except Exception:  # noqa: BLE001 - a bad alternative is simply skipped
            continue
    if not applied:
        return {}
    try:
        solved, _ = solve(spec)
        checks = verify(solved)
    except Exception:  # noqa: BLE001
        return {}
    return {
        "label": label,
        "rationale": rationale,
        "changes": applied,
        "checksum": solved.get("derived.checksum"),
        "summary": solved.summary(),
        "verification": verify_summary(checks),
        "failures": [c["id"] for c in checks if c.get("passed") is False],
        "spec": solved.to_dict(),
        "visual": visual or {},
    }


def build_variants(studio, limit: int = 3) -> list[dict]:
    """Three coherent, deterministic alternatives from the current design."""
    base = studio.solved
    diameter = float(base.get("case.diameter_mm") or 40)
    thickness = float(base.get("case.thickness_mm") or 12)
    color = base.get("dial.color") or "black"
    finish = base.get("case.finish") or "brushed"
    variants: list[dict] = []

    # 1. proportion study: slightly smaller/larger case with a corrected stack
    delta = -1.0 if diameter > 40 else 1.0
    variants.append(_variant(
        base, "Proportion study",
        f"Move the case {delta:+.0f} mm while keeping the movement and dial readable.",
        [{"path": "case.diameter_mm", "value": round(diameter + delta, 1)},
         {"path": "case.thickness_mm", "value": round(max(7.5, thickness - 0.4), 1)}],
        {"kind": "case-proportion", "emphasis": "silhouette"}))

    # 2. dial direction: a contrast colour that is not the current one
    alt_color = next((c for c in _DIAL_COLORS if c != color), "blue")
    variants.append(_variant(
        base, "Dial contrast",
        f"Keep the case but move the dial to {alt_color} for a different character.",
        [{"path": "dial.color", "value": alt_color}],
        {"kind": "dial-colour", "palette": [_colour_hex(alt_color),
                                            _colour_hex(base.get("case.material") or "steel")]}))

    # 3. finish and bezel direction
    alt_finish = next((f for f in _FINISHES if f != finish), "polished")
    bezel = base.get("case.bezel") or "plain"
    alt_bezel = next((b for b in _BEZELS if b != bezel), "flat")
    changes = [{"path": "case.finish", "value": alt_finish}]
    if alt_bezel in ("plain", "dive", "fluted", "coin-edge"):
        changes.append({"path": "case.bezel", "value": alt_bezel})
    variants.append(_variant(
        base, "Surface and bezel",
        f"{alt_finish.title()} case with a {alt_bezel.replace('-', ' ')} bezel.",
        changes,
        {"kind": "finish-bezel", "palette": [_colour_hex(alt_finish)]}))

    return [v for v in variants if v][:max(1, min(4, int(limit or 3)))]


def _colour_hex(value: str) -> str:
    return {
        "black": "#14171c", "blue": "#1c3f6e", "silver": "#c6c8cb",
        "white": "#e9e6dd", "green": "#204436", "burgundy": "#5a1f2a",
        "salmon": "#c98d72", "steel": "#c9ced6", "titanium": "#b4bcc2",
        "brushed": "#b9bec5", "polished": "#d7dbe0", "bead-blasted": "#9aa1a8",
        "two-tone": "#c8a24a", "dive": "#1d232b", "fluted": "#c9ced6",
        "coin-edge": "#b8bec6", "plain": "#c9ced6",
    }.get(str(value).lower(), "#9aa1a8")


# ---------------------------------------------------------------------------
# Deterministic prose
# ---------------------------------------------------------------------------

def _data_for(results: list, name: str) -> dict:
    for result in results:
        if getattr(result, "name", "") == name and getattr(result, "ok", False):
            return getattr(result, "data", {}) or {}
    return {}


def explain(studio, results: list, intent: Intent) -> str:
    spec = _data_for(results, "get_current_spec").get("spec", {})
    report = _data_for(results, "get_verification_report")
    summary = report.get("summary", {}) or (studio.summary or {})
    derived = spec.get("derived", {}) if isinstance(spec, dict) else {}
    train = derived.get("train", {}) if isinstance(derived, dict) else {}
    stack = derived.get("stack", {}) if isinstance(derived, dict) else {}
    torque = derived.get("torque", {}) if isinstance(derived, dict) else {}
    bits = ["Here is how the current design works, from the deterministic engine."]
    bits.append("Design: " + (spec.get("meta", {}).get("notes") or studio.solved.summary()
                              if isinstance(spec, dict) else studio.solved.summary()) + ".")
    if train:
        stages = train.get("stages") or []
        bits.append(
            "The going train has " + str(len(stages)) + " meshes with exact tooth counts; "
            "the barrel-to-centre ratio is " + str(train.get("barrel_to_centre"))
            + " and the direct centre-to-fourth ratio is "
            + str(train.get("centre_to_fourth")) + ".")
    if stack:
        bits.append("The vertical stack needs "
                    + str(stack.get("required_case_thickness_mm"))
                    + " mm inside " + str(stack.get("available_case_thickness_mm"))
                    + " mm, leaving " + str(stack.get("margin_mm")) + " mm.")
    if torque:
        bits.append("The power budget is " + str(torque.get("available_power_uw"))
                    + " uW available against " + str(torque.get("required_power_uw"))
                    + " uW required (margin " + str(torque.get("power_margin")) + "x).")
    if summary:
        bits.append("Verification: " + str(summary.get("fail", 0)) + " failing, "
                    + str(summary.get("calculated", 0)) + " calculated, "
                    + str(summary.get("estimated", 0)) + " estimated and "
                    + str(summary.get("needs_test", 0))
                    + " checks that still need a bench test.")
    bits.append("Dimensions marked derived are reproducible from the user fields; "
                "estimated values carry documented assumptions, and needs_test items "
                "are not passes.")
    return " ".join(bits)


def critique(studio, results: list, intent: Intent) -> str:
    spec = _data_for(results, "get_current_spec").get("spec", {})
    report = _data_for(results, "get_verification_report")
    geometry = _data_for(results, "inspect_geometry")
    checks = report.get("checks", [])
    failures = [c for c in checks if c.get("passed") is False]
    estimated = [c for c in checks if c.get("verdict") == "estimated"]
    needs = [c for c in checks if c.get("verdict") == "needs_test"]
    derived = spec.get("derived", {}) if isinstance(spec, dict) else {}
    stack = derived.get("stack", {}) if isinstance(derived, dict) else {}
    torque = derived.get("torque", {}) if isinstance(derived, dict) else {}

    strengths, risks = [], []
    if not failures:
        strengths.append("every deterministic check currently passes")
    if stack and isinstance(stack.get("margin_mm"), (int, float)):
        if stack["margin_mm"] >= 0.6:
            strengths.append(f"a healthy {stack['margin_mm']:.2f} mm stack margin")
        elif stack["margin_mm"] < 0.3:
            risks.append(f"the stack margin is tight at {stack['margin_mm']:.2f} mm")
    if torque and torque.get("power_margin"):
        margin = float(torque["power_margin"])
        if margin >= 1.5:
            strengths.append(f"a {margin:.2f}x power margin")
        elif margin < 1.15:
            risks.append(f"the power margin is only {margin:.2f}x")
    if geometry:
        count = geometry.get("part_count")
        mass = geometry.get("mass_g")
        if count:
            strengths.append(f"{count} modelled parts")
        if mass and mass > 90:
            risks.append(f"the estimated mass is high at {mass:.0f} g")
        stages = geometry.get("stages") or []
        if stages:
            strengths.append(f"{len(stages)} meshing stages with exact centre distances")
    for check in failures[:6]:
        risks.append(f"{check.get('id')}: {check.get('detail', '')}")
    if estimated:
        risks.append(f"{len(estimated)} checks are estimates, not measurements")
    if needs:
        risks.append(f"{len(needs)} items still need bench testing (the timegrapher, "
                     "water, shock and magnetic tests)")
    bits = ["Design critique."]
    bits.append("Strengths: " + ("; ".join(strengths) if strengths else "none established yet") + ".")
    bits.append("Risks: " + ("; ".join(risks) if risks else "no material risk found in the deterministic checks") + ".")
    bits.append("Direction: keep the brief fixed, resolve any failing check first, "
                "then adjust proportion or finish; do not trade a verified constraint "
                "for a cosmetic preference.")
    return " ".join(bits)


def inspect_summary(studio, results: list, intent: Intent) -> str:
    spec = _data_for(results, "get_current_spec").get("spec", {})
    geometry = _data_for(results, "inspect_geometry")
    history = _data_for(results, "get_design_history")
    bits = ["Current design state."]
    if isinstance(spec, dict):
        bits.append(studio.solved.summary() + ".")
    if geometry:
        bits.append(f"{geometry.get('part_count', 0)} parts, "
                    f"{geometry.get('triangles', 0)} triangles, "
                    f"{geometry.get('mass_g', 0)} g.")
        stack = geometry.get("stack") or {}
        if stack:
            bits.append(f"Stack margin {stack.get('margin_mm')} mm.")
    if history.get("history"):
        last = history["history"][-1]
        bits.append(f"{len(history['history'])} versions; newest is "
                    f"'{last.get('label')}' ({last.get('kind')}).")
    if history.get("can_undo"):
        bits.append("Undo is available.")
    return " ".join(bits)


def initial_message(intent: Intent, studio, results: list) -> str:
    if intent.name == "explain":
        return explain(studio, results, intent)
    if intent.name == "critic":
        return critique(studio, results, intent)
    if intent.name == "inspect":
        return inspect_summary(studio, results, intent)
    return ""
