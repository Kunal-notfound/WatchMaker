"""Deterministic engineering optimisation over the shared watch specification.

There is no model in this module.  Each candidate is a real set of spec
changes; the existing solver, mesh builder and verifier evaluate it.  Numbers
carry an explicit provenance label:

* ``calculated`` — closed-form geometry, mass, stack margin, part counts,
* ``estimated``  — material/process cost and weighted complexity indices,
* ``needs_test`` — the verifier's physical checks that only a bench can settle.

Costs are planning estimates from the tables below, not quotations.  The
optimiser never claims a candidate was simulated or physically verified.
"""

from __future__ import annotations

from typing import Any

from .parts import build_scene
from .schema import FIELDS, Spec
from .solver import solve
from .verify import summary, verify

# ---------------------------------------------------------------------------
# Cost assumptions (USD).  Deliberately transparent and editable.
# ---------------------------------------------------------------------------
MATERIAL_USD_PER_KG = {
    "steel": 14.0,
    "titanium": 58.0,
    "brass": 11.0,
    "gold": 2400.0,
    "jewel": 1200.0,
    "crystal": 420.0,
    "dial": 55.0,
    "spring": 26.0,
    "rubber": 18.0,
    "copper": 15.0,
    "pcb": 90.0,
}
PROCESS_USD_PER_PART = {
    "case": 9.0,
    "dial": 4.0,
    "hand": 3.0,
    "movement": 2.5,
    "complication": 7.0,
    "crystal": 2.0,
    "gasket": 0.6,
    "part": 2.0,
}
OBJECTIVES = ("weight", "cost", "manufacturability", "tolerance", "assembly")


def _clamp(path: str, value: Any) -> Any:
    field = FIELDS.get(path) or {}
    if field.get("kind") in ("num", "int"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return field.get("default")
        low, high = field.get("min"), field.get("max")
        if low is not None:
            number = max(float(low), number)
        if high is not None:
            number = min(float(high), number)
        return int(round(number)) if field.get("kind") == "int" else round(number, 3)
    return value


def _delta(spec: Spec, path: str, delta: float) -> Any:
    base = spec.get(path)
    try:
        return _clamp(path, float(base) + delta)
    except (TypeError, ValueError):
        return base


def _blueprints(spec: Spec, objective: str) -> list[dict]:
    """Ordered, deterministic source of candidate change sets."""
    items: list[dict] = []

    def add(key: str, label: str, rationale: str,
            changes: list[dict]) -> None:
        if key == "baseline":
            items.append({"id": key, "label": label, "rationale": rationale,
                          "changes": []})
            return
        cleaned = []
        for change in changes:
            path = change.get("path")
            if path not in FIELDS or path in spec.locks:
                continue
            value = _clamp(path, change.get("value"))
            if spec.get(path) == value:
                continue
            cleaned.append({"path": path, "value": value,
                            "label": change.get("label") or path})
        signature = tuple(sorted((item["path"], repr(item["value"]))
                                 for item in cleaned))
        if cleaned and signature not in seen:
            seen.add(signature)
            items.append({"id": key, "label": label, "rationale": rationale,
                          "changes": cleaned})

    add("baseline", "Current design", "No changes; the reference for every comparison.", [])
    seen: set[tuple] = set()

    # Proportion probes are useful for every objective.
    add("diameter-down", "Case −1 mm",
        "A smaller case removes material and often improves wearability.",
        [{"path": "case.diameter_mm", "value": _delta(spec, "case.diameter_mm", -1)}])
    add("diameter-up", "Case +1 mm",
        "A larger case tests whether the movement and dial still fit cleanly.",
        [{"path": "case.diameter_mm", "value": _delta(spec, "case.diameter_mm", 1)}])
    add("thinner", "Case −0.4 mm",
        "Reduce case height; the stack margin decides whether this is viable.",
        [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", -0.4)}])
    add("thicker", "Case +0.4 mm",
        "Add stack margin for tolerances or complications.",
        [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", 0.4)}])

    if objective == "weight":
        add("titanium", "Titanium case",
            "Titanium is roughly 45% less dense than steel at similar stiffness.",
            [{"path": "case.material", "value": "titanium"}])
        add("carbon", "Carbon case",
            "A carbon case cuts metal mass but changes the manufacturing route.",
            [{"path": "case.material", "value": "carbon"}])
        add("ceramic", "Ceramic case",
            "Ceramic is light and scratch resistant, with different process risk.",
            [{"path": "case.material", "value": "ceramic"}])
        add("quartz", "Quartz movement",
            "A quartz module removes the mechanical train and most of its mass.",
            [{"path": "movement.kind", "value": "quartz"},
             {"path": "quartz.module", "value": "generic 3-hand"}])
        add("thin-dial", "Thinner dial",
            "A 0.1 mm thinner dial saves a small amount of material.",
            [{"path": "dial.thickness_mm", "value": _delta(spec, "dial.thickness_mm", -0.1)}])

    elif objective == "cost":
        add("steel", "Steel case",
            "316L steel is the lowest-cost case material in the library.",
            [{"path": "case.material", "value": "316L stainless steel"}])
        add("bead-blasted", "Bead-blasted finish",
            "Bead blasting avoids manual polishing time.",
            [{"path": "case.finish", "value": "bead-blasted"}])
        add("thin-crystal", "Thinner crystal",
            "A 0.2 mm thinner crystal removes material and cost.",
            [{"path": "case.crystal_thickness_mm",
              "value": _delta(spec, "case.crystal_thickness_mm", -0.2)}])
        add("quartz-cost", "Quartz movement",
            "A bought quartz module removes the in-house movement parts.",
            [{"path": "movement.kind", "value": "quartz"},
             {"path": "quartz.module", "value": "generic 3-hand"}])
        add("smaller-dial", "Dial −0.5 mm",
            "A smaller dial removes material inside the same case.",
            [{"path": "dial.diameter_mm",
              "value": _delta(spec, "dial.diameter_mm", -0.5)}])

    elif objective == "manufacturability":
        add("sealed", "Screw-down crown and back",
            "Screw-down closures make the water-resistance claim easier to build.",
            [{"path": "case.crown", "value": "screw-down"},
             {"path": "case.caseback", "value": "screw-down"}])
        add("standard-lugs", "Standard 20 mm lugs",
            "Standard lug spacing avoids a bespoke strap interface.",
            [{"path": "case.lug_width_mm", "value": 20}])
        add("bead-blasted-dfm", "Bead-blasted finish",
            "A uniform blasted finish is easier to inspect than a mirror polish.",
            [{"path": "case.finish", "value": "bead-blasted"}])
        add("no-modules-dfm", "Remove complications",
            "Fewer stacked modules mean fewer parts, openings and assembly risks.",
            [{"path": "movement.complications", "value": {}}])
        add("plain-bezel", "Plain bezel",
            "A plain bezel has the simplest machining route.",
            [{"path": "case.bezel", "value": "plain"}])
        add("flat-crystal", "Flat crystal",
            "A flat crystal is easier to grind and seat than a domed one.",
            [{"path": "case.crystal_type", "value": "flat"}])

    elif objective == "tolerance":
        add("margin-04", "+0.4 mm stack margin",
            "More case height gives the worst-case stack-up room to close.",
            [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", 0.4)}])
        add("margin-08", "+0.8 mm stack margin",
            "A larger margin buys tolerance headroom at the cost of thickness.",
            [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", 0.8)}])
        add("flat-tolerance", "Flat crystal",
            "A flat crystal removes curvature from the stack calculation.",
            [{"path": "case.crystal_type", "value": "flat"}])
        add("simple-complications", "Remove complications",
            "Fewer stacked modules reduce the number of tolerance interfaces.",
            [{"path": "movement.complications", "value": {}}])
        add("thicker-dial", "Thicker dial",
            "A slightly thicker dial is less sensitive to flatness variation.",
            [{"path": "dial.thickness_mm", "value": _delta(spec, "dial.thickness_mm", 0.1)}])

    elif objective == "assembly":
        add("no-complications", "Remove complications",
            "Every removed module is one less sub-assembly and adjustment.",
            [{"path": "movement.complications", "value": {}}])
        add("quartz-assembly", "Quartz movement",
            "A bought quartz module replaces the mechanical train and its assembly.",
            [{"path": "movement.kind", "value": "quartz"},
             {"path": "quartz.module", "value": "generic 3-hand"}])
        add("plain-assembly", "Plain bezel",
            "A plain bezel simplifies case assembly and inspection.",
            [{"path": "case.bezel", "value": "plain"}])
        add("flat-assembly", "Flat crystal",
            "A flat crystal seats with a simpler gasket interface.",
            [{"path": "case.crystal_type", "value": "flat"}])
        add("wide-lugs", "Wider lugs",
            "A wider lug stance can simplify strap fitting, at the cost of mass.",
            [{"path": "case.lug_width_mm",
              "value": _delta(spec, "case.lug_width_mm", 1)}])

    return items


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _material_cost(scene: dict) -> tuple[float, float]:
    material = 0.0
    process = 0.0
    masses = scene.get("part_masses_g") or {}
    for part in scene.get("parts") or []:
        mass_g = float(masses.get(part.name, 0.0) or 0.0)
        material += mass_g / 1000.0 * MATERIAL_USD_PER_KG.get(part.material, 30.0)
        process += PROCESS_USD_PER_PART.get(part.role, 2.0)
    return round(material, 2), round(process, 2)


def _metrics(spec: Spec, scene: dict, checks: list[dict]) -> dict:
    check_summary = summary(checks)
    stack = spec.get("derived.stack") or {}
    margin = float(stack.get("margin_mm") or 0.0)
    worst_case = 0.28  # 7 interfaces x 0.04 mm, matching verify.tolerance.stackup
    dfm = [c for c in checks if c.get("layer") == "dfm"]
    dfm_fail = sum(1 for c in dfm if c.get("passed") is False)
    tolerance_fail = sum(1 for c in checks
                         if c.get("layer") == "tolerance" and c.get("passed") is False)
    complication_count = len((spec.get("movement.complications") or {}))
    parts = len(scene.get("parts") or [])
    material_cost, process_cost = _material_cost(scene)

    return {
        "mass_g": round(float(scene.get("mass_g") or 0.0), 2),
        "material_cost_usd": material_cost,
        "process_cost_usd": process_cost,
        "estimated_unit_cost_usd": round(material_cost + process_cost, 2),
        "part_count": parts,
        "complication_count": complication_count,
        "dfm_failures": dfm_fail,
        "tolerance_failures": tolerance_fail,
        "stack_margin_mm": round(margin, 3),
        "tolerance_risk": round(max(0.0, 1.0 - margin / worst_case), 3),
        "failing_checks": int(check_summary.get("fail") or 0),
        "needs_test": int(check_summary.get("needs_test") or 0),
        "triangles": int(scene.get("triangles") or 0),
        "provenance": {
            "mass_g": "calculated",
            "material_cost_usd": "estimated",
            "process_cost_usd": "estimated",
            "estimated_unit_cost_usd": "estimated",
            "part_count": "calculated",
            "complication_count": "calculated",
            "dfm_failures": "calculated",
            "tolerance_failures": "calculated",
            "stack_margin_mm": "calculated",
            "tolerance_risk": "calculated",
            "failing_checks": "calculated",
            "needs_test": "calculated from the verifier's physical test list",
            "triangles": "calculated",
        },
    }


def _score(objective: str, metrics: dict) -> float:
    if objective == "weight":
        return metrics["mass_g"]
    if objective == "cost":
        return metrics["estimated_unit_cost_usd"]
    if objective == "manufacturability":
        return metrics["dfm_failures"] * 1000.0 + metrics["part_count"]
    if objective == "tolerance":
        return -metrics["stack_margin_mm"] * 100.0 + metrics["tolerance_risk"]
    return metrics["part_count"] + metrics["complication_count"] * 8.0


def evaluate(spec: Spec, blueprint: dict, objective: str) -> dict:
    """Solve, mesh and verify one candidate.  Never mutates ``spec``."""
    candidate = spec.copy()
    applied = []
    skipped = []
    for change in blueprint.get("changes") or []:
        path = change.get("path")
        if path not in FIELDS:
            skipped.append({"path": path, "reason": "unknown field"})
            continue
        before = candidate.get(path)
        candidate.set(path, change.get("value"), tag="optimise",
                      note=f"{objective} candidate: {blueprint.get('label')}")
        after = candidate.get(path)
        if before == after:
            skipped.append({"path": path, "reason": "unchanged"})
        else:
            applied.append({"path": path, "from": before, "to": after})
    try:
        if applied:
            solved, _ = solve(candidate)
        else:
            # The baseline (or a fully skipped blueprint) is already solved.
            # Re-solving would bump meta.version and make its checksum differ
            # from the design it represents.
            solved = candidate
        scene = build_scene(solved)
        checks = verify(solved, scene)
        metrics = _metrics(solved, scene, checks)
        status = "ok"
        error = ""
    except Exception as exc:  # noqa: BLE001 - an invalid candidate is data
        solved = candidate
        metrics = {"failing_checks": 1, "provenance": {"failing_checks": "calculated"}}
        checks = []
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "id": blueprint.get("id"),
        "label": blueprint.get("label"),
        "rationale": blueprint.get("rationale"),
        "changes": blueprint.get("changes") or [],
        "applied": applied,
        "skipped": skipped,
        "metrics": metrics,
        "status": status,
        "error": error,
        "checksum": str(solved.get("derived.checksum") or ""),
        "score": round(_score(objective, metrics), 3),
        "verification": summary(checks),
    }


def run(spec: Spec, objective: str = "weight", budget: int = 9) -> dict:
    """Evaluate a deterministic candidate field and rank it.

    ``spec`` is the current solved specification.  No candidate is applied;
    the caller must send the chosen change set through the validated variant
    pipeline.
    """
    objective = objective if objective in OBJECTIVES else "weight"
    try:
        budget = max(2, min(12, int(budget or 9)))
    except (TypeError, ValueError):
        budget = 9

    blueprints = _blueprints(spec, objective)[:budget]
    baseline = evaluate(spec, blueprints[0], objective) if blueprints else None
    candidates = [evaluate(spec, item, objective) for item in blueprints]
    baseline_checksum = str(spec.get("derived.checksum") or "")
    measured = ("mass_g", "material_cost_usd", "process_cost_usd",
                "part_count", "complication_count", "dfm_failures",
                "tolerance_failures", "stack_margin_mm", "tolerance_risk",
                "failing_checks")
    base_metrics = baseline.get("metrics", {}) if baseline else {}
    for item in candidates:
        changed = [key for key in measured
                   if item["metrics"].get(key) != base_metrics.get(key)]
        item["changed_metrics"] = changed
        item["measurable"] = bool(changed)

    # Passing candidates always rank above failing ones.
    ranked = sorted(
        candidates,
        key=lambda item: (item["status"] != "ok",
                          int(item["metrics"].get("failing_checks") or 0) > 0,
                          item["score"],
                          item["metrics"].get("mass_g", 0.0),
                          item["id"]),
    )
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    winner = ranked[0] if ranked else None
    return {
        "objective": objective,
        "budget": budget,
        "baseline_checksum": baseline_checksum,
        "baseline": baseline,
        "candidates": candidates,
        "ranked": [item["id"] for item in ranked],
        "winner": winner["id"] if winner else None,
        "assumptions": {
            "material_usd_per_kg": MATERIAL_USD_PER_KG,
            "process_usd_per_part": PROCESS_USD_PER_PART,
            "tolerance_worst_case_mm": 0.28,
            "note": "Costs are planning estimates; mass, margin and counts are calculated from geometry.",
        },
        "provenance": {
            "engine": "deterministic solver + mesh kernel + verifier",
            "model_used": False,
            "simulated": False,
            "physically_verified": False,
        },
    }
