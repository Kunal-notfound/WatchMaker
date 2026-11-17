"""Deterministic, multi-objective optimisation over the shared watch spec.

There is no model in this module.  Every candidate is a real specification
change set; the existing solver, mesh builder and verifier evaluate it.  Hard
constraints (schema validity, locked fields, solver success and failing checks)
are reported separately from objectives.  Feasible candidates are compared with
Pareto dominance across mixed minimisation/maximisation objectives.

Numbers carry an explicit provenance label:

* ``calculated`` — closed-form geometry, mass, stack margin, part counts,
* ``estimated``  — material/process cost and weighted complexity indices,
* ``needs_test`` — verifier checks that only a bench can settle.

Costs are planning estimates from the tables below, not quotations.  The engine
does not simulate wear, tolerance stack-ups in a CAD tolerancer, or physical
behaviour, and it never claims a global optimum.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .materials import CASE_MATERIAL_KEY, MATERIALS
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

# A positive sign means "smaller is better"; a negative sign flips a maximised
# metric into the same minimisation space used by the dominance comparison.
OBJECTIVE_META = {
    "weight": {"label": "Minimum mass", "metric": "mass_g", "unit": "g",
               "direction": "min", "sign": 1,
               "provenance": "calculated"},
    "cost": {"label": "Minimum estimated unit cost", "metric": "estimated_unit_cost_usd",
             "unit": "USD", "direction": "min", "sign": 1,
             "provenance": "estimated"},
    "manufacturability": {"label": "Manufacturability", "metric": "manufacturability_index",
                          "unit": "index", "direction": "min", "sign": 1,
                          "provenance": "estimated"},
    "tolerance": {"label": "Maximum stack margin", "metric": "stack_margin_mm",
                  "unit": "mm", "direction": "max", "sign": -1,
                  "provenance": "calculated"},
    "assembly": {"label": "Assembly simplicity", "metric": "assembly_index",
                 "unit": "index", "direction": "min", "sign": 1,
                 "provenance": "estimated"},
}

GENERATOR_VERSION = 2
_EPS = 1e-6


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------
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


def _blueprints(spec: Spec, objective: str | None = None) -> list[dict]:
    """Build the deterministic candidate field, ordered for ``objective``.

    The full field is shared by all objectives so a Pareto comparison never
    depends on a different search space for each objective.  The requested
    objective only changes presentation order, not which candidates exist.
    """
    items: list[dict] = []
    seen: set[tuple] = set()

    def add(key: str, label: str, rationale: str, changes: list[dict] | None = None,
            objectives: tuple[str, ...] = ()) -> None:
        if key == "baseline":
            items.append({"id": key, "label": label, "rationale": rationale,
                          "changes": [], "objectives": [], "group": "baseline"})
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
        if not cleaned or signature in seen:
            return
        seen.add(signature)
        items.append({"id": key, "label": label, "rationale": rationale,
                      "changes": cleaned, "objectives": list(objectives),
                      "group": "probe"})

    add("baseline", "Current design", "No changes; the reference for every comparison.")
    # Probe library.  Every probe carries its owning objective(s), but all
    # probes are evaluated on every run.
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

    add("titanium", "Titanium case",
        "Titanium is roughly 45% less dense than steel at similar stiffness.",
        [{"path": "case.material", "value": "titanium"}], ("weight",))
    add("carbon", "Carbon case",
        "A carbon case cuts metal mass but changes the manufacturing route.",
        [{"path": "case.material", "value": "carbon"}], ("weight",))
    add("ceramic", "Ceramic case",
        "Ceramic is light and scratch resistant, with different process risk.",
        [{"path": "case.material", "value": "ceramic"}], ("weight",))
    add("quartz", "Quartz movement",
        "A quartz module removes the mechanical train and most of its mass.",
        [{"path": "movement.kind", "value": "quartz"},
         {"path": "quartz.module", "value": "generic 3-hand"}], ("weight",))
    add("thin-dial", "Thinner dial",
        "A 0.1 mm thinner dial saves a small amount of material.",
        [{"path": "dial.thickness_mm", "value": _delta(spec, "dial.thickness_mm", -0.1)}],
        ("weight",))

    add("steel", "Steel case",
        "316L steel is the lowest-cost case material in the library.",
        [{"path": "case.material", "value": "316L stainless steel"}], ("cost",))
    add("bead-blasted", "Bead-blasted finish",
        "Bead blasting avoids manual polishing time.",
        [{"path": "case.finish", "value": "bead-blasted"}], ("cost",))
    add("thin-crystal", "Thinner crystal",
        "A 0.2 mm thinner crystal removes material and cost.",
        [{"path": "case.crystal_thickness_mm",
          "value": _delta(spec, "case.crystal_thickness_mm", -0.2)}], ("cost",))
    add("quartz-cost", "Quartz movement",
        "A bought quartz module removes the in-house movement parts.",
        [{"path": "movement.kind", "value": "quartz"},
         {"path": "quartz.module", "value": "generic 3-hand"}], ("cost",))
    add("smaller-dial", "Dial −0.5 mm",
        "A smaller dial removes material inside the same case.",
        [{"path": "dial.diameter_mm",
          "value": _delta(spec, "dial.diameter_mm", -0.5)}], ("cost",))

    add("sealed", "Screw-down crown and back",
        "Screw-down closures make the water-resistance claim easier to build.",
        [{"path": "case.crown", "value": "screw-down"},
         {"path": "case.caseback", "value": "screw-down"}], ("manufacturability",))
    add("standard-lugs", "Standard 20 mm lugs",
        "Standard lug spacing avoids a bespoke strap interface.",
        [{"path": "case.lug_width_mm", "value": 20}], ("manufacturability",))
    add("bead-blasted-dfm", "Bead-blasted finish",
        "A uniform blasted finish is easier to inspect than a mirror polish.",
        [{"path": "case.finish", "value": "bead-blasted"}], ("manufacturability",))
    add("no-modules-dfm", "Remove complications",
        "Fewer stacked modules mean fewer parts, openings and assembly risks.",
        [{"path": "movement.complications", "value": {}}], ("manufacturability",))
    add("plain-bezel", "Plain bezel",
        "A plain bezel has the simplest machining route.",
        [{"path": "case.bezel", "value": "plain"}], ("manufacturability",))
    add("flat-crystal", "Flat crystal",
        "A flat crystal is easier to grind and seat than a domed one.",
        [{"path": "case.crystal_type", "value": "flat"}], ("manufacturability",))

    add("margin-04", "+0.4 mm stack margin",
        "More case height gives the worst-case stack-up room to close.",
        [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", 0.4)}],
        ("tolerance",))
    add("margin-08", "+0.8 mm stack margin",
        "A larger margin buys tolerance headroom at the cost of thickness.",
        [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", 0.8)}],
        ("tolerance",))
    add("flat-tolerance", "Flat crystal",
        "A flat crystal removes curvature from the stack calculation.",
        [{"path": "case.crystal_type", "value": "flat"}], ("tolerance",))
    add("simple-complications", "Remove complications",
        "Fewer stacked modules reduce the number of tolerance interfaces.",
        [{"path": "movement.complications", "value": {}}], ("tolerance",))
    add("thicker-dial", "Thicker dial",
        "A slightly thicker dial is less sensitive to flatness variation.",
        [{"path": "dial.thickness_mm", "value": _delta(spec, "dial.thickness_mm", 0.1)}],
        ("tolerance",))

    add("no-complications", "Remove complications",
        "Every removed module is one less sub-assembly and adjustment.",
        [{"path": "movement.complications", "value": {}}], ("assembly",))
    add("quartz-assembly", "Quartz movement",
        "A bought quartz module replaces the mechanical train and its assembly.",
        [{"path": "movement.kind", "value": "quartz"},
         {"path": "quartz.module", "value": "generic 3-hand"}], ("assembly",))
    add("plain-assembly", "Plain bezel",
        "A plain bezel simplifies case assembly and inspection.",
        [{"path": "case.bezel", "value": "plain"}], ("assembly",))
    add("flat-assembly", "Flat crystal",
        "A flat crystal seats with a simpler gasket interface.",
        [{"path": "case.crystal_type", "value": "flat"}], ("assembly",))
    add("wide-lugs", "Wider lugs",
        "A wider lug stance can simplify strap fitting, at the cost of mass.",
        [{"path": "case.lug_width_mm",
          "value": _delta(spec, "case.lug_width_mm", 1)}], ("assembly",))

    # Deliberate two-field combinations expose trade-offs that single probes
    # cannot: lighter material plus thinner case, or a cheaper module plus a
    # simpler machining route.
    add("titanium-thin", "Titanium, thinner case",
        "Combine the lightest practical case material with a lower profile.",
        [{"path": "case.material", "value": "titanium"},
         {"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", -0.4)}],
        ("weight",))
    add("carbon-flat", "Carbon, flat crystal",
        "A light case with a flat crystal trades scratch resistance for mass.",
        [{"path": "case.material", "value": "carbon"},
         {"path": "case.crystal_type", "value": "flat"}], ("weight",))
    add("steel-sealed", "Steel, screw-down",
        "Keep the economical material while simplifying the sealed build.",
        [{"path": "case.material", "value": "316L stainless steel"},
         {"path": "case.crown", "value": "screw-down"},
         {"path": "case.caseback", "value": "screw-down"}], ("cost", "manufacturability"))
    add("margin-thin-dial", "Margin, thinner dial",
        "Buy stack headroom without increasing the outer case height.",
        [{"path": "case.thickness_mm", "value": _delta(spec, "case.thickness_mm", 0.4)},
         {"path": "dial.thickness_mm", "value": _delta(spec, "dial.thickness_mm", -0.1)}],
        ("tolerance",))
    add("quartz-plain", "Quartz, plain bezel",
        "Minimise both assembly steps and machining operations.",
        [{"path": "movement.kind", "value": "quartz"},
         {"path": "quartz.module", "value": "generic 3-hand"},
         {"path": "case.bezel", "value": "plain"}], ("assembly", "cost"))

    baseline = next((item for item in items if item["id"] == "baseline"), None)
    rest = [item for item in items if item["id"] != "baseline"]
    if objective and objective in OBJECTIVES:
        primary = [item for item in rest if objective in item["objectives"]]
        secondary = [item for item in rest if objective not in item["objectives"]]
        ordered = ([baseline] if baseline else []) + primary + secondary
    else:
        ordered = ([baseline] if baseline else []) + rest
    return ordered


def build_candidate(spec: Spec, changes: list[dict] | None,
                    *, label: str = "candidate") -> dict:
    """Solve a change set against a copy of ``spec`` without mutating it.

    Returns the solved spec, the applied/skipped diff and an error string.  A
    failed build is returned as data so the optimiser can display rejection
    rather than aborting the whole run.
    """
    candidate = spec.copy()
    applied: list[dict] = []
    skipped: list[dict] = []
    for change in changes or []:
        path = change.get("path")
        if path not in FIELDS:
            skipped.append({"path": path, "reason": "unknown field"})
            continue
        if path in spec.locks:
            skipped.append({"path": path, "reason": "locked field"})
            continue
        before = candidate.get(path)
        candidate.set(path, change.get("value"), tag="optimise",
                      note=f"optimisation candidate: {label}")
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
            # Re-solving bumps meta.version and would change its checksum.
            solved = candidate
        scene = build_scene(solved)
        checks = verify(solved, scene)
        return {"spec": solved, "scene": scene, "checks": checks,
                "applied": applied, "skipped": skipped, "error": ""}
    except Exception as exc:  # noqa: BLE001 - an invalid candidate is data
        return {"spec": candidate, "scene": None, "checks": [],
                "applied": applied, "skipped": skipped,
                "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Metrics, constraints and Pareto ranking
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
    part_count = int(parts)

    return {
        "mass_g": round(float(scene.get("mass_g") or 0.0), 2),
        "material_cost_usd": material_cost,
        "process_cost_usd": process_cost,
        "estimated_unit_cost_usd": round(material_cost + process_cost, 2),
        "part_count": part_count,
        "complication_count": complication_count,
        "dfm_failures": dfm_fail,
        "tolerance_failures": tolerance_fail,
        "stack_margin_mm": round(margin, 3),
        "tolerance_risk": round(max(0.0, 1.0 - margin / worst_case), 3),
        "manufacturability_index": dfm_fail * 1000.0 + part_count,
        "assembly_index": part_count + complication_count * 8.0,
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
            "manufacturability_index": "estimated",
            "assembly_index": "estimated",
            "failing_checks": "calculated",
            "needs_test": "calculated from the verifier's physical test list",
            "triangles": "calculated",
        },
    }


def _constraint(cid: str, status: str, detail: str, *, blocking: bool,
                provenance: str) -> dict:
    return {"id": cid, "status": status, "detail": detail,
            "blocking": blocking, "provenance": provenance}


def _constraints(base: Spec, candidate: Spec, blueprint: dict, build: dict,
                 metrics: dict) -> list[dict]:
    """Hard constraints and non-blocking caveats, separate from objectives."""
    out: list[dict] = []
    issues = [row for row in candidate.validate() if row.get("level") == "error"]
    out.append(_constraint(
        "schema", "PASS" if not issues else "FAIL",
        "valid against the canonical spec" if not issues else
        "; ".join(str(row.get("message")) for row in issues[:3]),
        blocking=True, provenance="calculated"))
    touched = [row["path"] for row in build["applied"] if row["path"] in base.locks]
    out.append(_constraint(
        "locks", "PASS" if not touched else "FAIL",
        "no locked field changed" if not touched else "touched " + ", ".join(touched),
        blocking=True, provenance="calculated"))
    out.append(_constraint(
        "solver", "PASS" if not build["error"] else "FAIL",
        "solver, mesh and verifier completed" if not build["error"] else build["error"],
        blocking=True, provenance="calculated"))
    fail = int(metrics.get("failing_checks") or 0)
    out.append(_constraint(
        "verification", "PASS" if fail == 0 else "FAIL",
        f"{fail} failing check(s)" if fail else "no failing checks",
        blocking=True, provenance="calculated"))
    material = str(candidate.get("case.material") or "")
    key = CASE_MATERIAL_KEY.get(material)
    out.append(_constraint(
        "material_density", "PASS" if key in MATERIALS else "UNKNOWN",
        f"nominal density available for {material}" if key in MATERIALS else
        f"no nominal density for {material!r}; mass is not comparable",
        blocking=True, provenance="estimated"))
    needs_test = int(metrics.get("needs_test") or 0)
    out.append(_constraint(
        "bench_tests", "WARNING" if needs_test else "PASS",
        f"{needs_test} check(s) need physical testing" if needs_test else
        "no verifier items require physical testing",
        blocking=False, provenance="needs_test"))
    skipped = [row for row in build["skipped"] if row.get("reason") != "unchanged"]
    if skipped:
        out.append(_constraint(
            "change_set", "WARNING",
            "some requested changes were skipped: " + ", ".join(
                f"{row.get('path')} ({row.get('reason')})" for row in skipped[:3]),
            blocking=False, provenance="calculated"))
    return out


def _score(objective: str, metrics: dict) -> float:
    """Single-objective recommendation score, kept for compatibility."""
    objective = objective if objective in OBJECTIVE_META else "weight"
    meta = OBJECTIVE_META[objective]
    value = float(metrics.get(meta["metric"]) or 0.0)
    return round(value * meta["sign"], 3)


def objective_vector(item: dict, axes: list[str]) -> tuple[float, ...]:
    """Mix min/max objectives into a single minimisation tuple."""
    values = []
    for name in axes:
        meta = OBJECTIVE_META[name]
        raw = float(item.get("metrics", {}).get(meta["metric"]) or 0.0)
        values.append(round(raw * meta["sign"], 6))
    return tuple(values)


def dominates(left: dict, right: dict, axes: list[str]) -> bool:
    """True when ``left`` dominates ``right`` on all axes and better on one."""
    a, b = objective_vector(left, axes), objective_vector(right, axes)
    return all(x <= y + _EPS for x, y in zip(a, b)) and any(
        x < y - _EPS for x, y in zip(a, b))


def non_dominated_sort(items: list[dict], axes: list[str]) -> list[list[dict]]:
    """Standard fast non-dominated sorting; returns ordered fronts."""
    fronts: list[list[dict]] = []
    remaining = list(items)
    while remaining:
        front = [item for item in remaining
                 if not any(dominates(other, item, axes)
                            for other in remaining if other is not item)]
        if not front:  # defensive: numerical ties should be handled by _EPS
            front = remaining[:1]
        fronts.append(front)
        ids = {id(item) for item in front}
        remaining = [item for item in remaining if id(item) not in ids]
    return fronts


def crowding_distance(front: list[dict], axes: list[str]) -> dict[str, float]:
    """NSGA-II crowding distance keyed by candidate id."""
    out = {str(item.get("id")): 0.0 for item in front}
    # Boundary members get a large finite value: JSON output must stay strict
    # and a plain sentinel ranks them ahead of interior points.
    boundary = 1e9
    if len(front) <= 2:
        for item in front:
            out[str(item.get("id"))] = boundary
        return out
    for name in axes:
        meta = OBJECTIVE_META[name]
        ordered = sorted(front, key=lambda item: float(
            item.get("metrics", {}).get(meta["metric"]) or 0.0))
        low_id = str(ordered[0].get("id"))
        high_id = str(ordered[-1].get("id"))
        out[low_id] = boundary
        out[high_id] = boundary
        span = (float(ordered[-1].get("metrics", {}).get(meta["metric"]) or 0.0)
                - float(ordered[0].get("metrics", {}).get(meta["metric"]) or 0.0))
        if abs(span) <= _EPS:
            continue
        for index in range(1, len(ordered) - 1):
            cid = str(ordered[index].get("id"))
            if out[cid] >= boundary:
                continue
            before = float(ordered[index - 1].get("metrics", {}).get(meta["metric"]) or 0.0)
            after = float(ordered[index + 1].get("metrics", {}).get(meta["metric"]) or 0.0)
            out[cid] += (after - before) / span
    return out


def evaluate(spec: Spec, blueprint: dict, objective: str,
             *, axes: tuple[str, ...] | list[str] | None = None) -> dict:
    """Solve, mesh, verify and classify one candidate.  Never mutates ``spec``."""
    build = build_candidate(spec, blueprint.get("changes") or [],
                            label=str(blueprint.get("label") or "candidate"))
    solved = build["spec"]
    checks = build["checks"]
    if build["scene"] is None:
        metrics = {"failing_checks": 1, "needs_test": 0,
                   "provenance": {"failing_checks": "calculated"}}
        verification = {"fail": 1}
    else:
        metrics = _metrics(solved, build["scene"], checks)
        verification = summary(checks)
    constraints = _constraints(spec, solved, blueprint, build, metrics)
    feasible = (not build["error"]
                and all(row["status"] in ("PASS", "WARNING")
                        for row in constraints if row["blocking"]))
    result = {
        "id": blueprint.get("id"),
        "label": blueprint.get("label"),
        "rationale": blueprint.get("rationale"),
        "changes": blueprint.get("changes") or [],
        "objectives": list(blueprint.get("objectives") or []),
        "applied": build["applied"],
        "skipped": build["skipped"],
        "metrics": metrics,
        "status": "ok" if not build["error"] else "failed",
        "error": build["error"],
        "checksum": str(solved.get("derived.checksum") or ""),
        "score": _score(objective, metrics),
        "objective_values": {
            name: round(float(metrics.get(meta["metric"]) or 0.0), 6)
            for name, meta in OBJECTIVE_META.items()
            if metrics.get(meta["metric"]) is not None
        },
        "constraints": constraints,
        "feasible": feasible,
        "verification": verification,
        "pareto_rank": None,
        "crowding_distance": None,
        "dominates": [],
        "dominated_by": [],
    }
    if axes:
        result["axes_vector"] = list(objective_vector(result, list(axes)))
    return result


def run(spec: Spec, objective: str = "weight", budget: int = 9,
        axes: list[str] | tuple[str, ...] | None = None) -> dict:
    """Evaluate a deterministic candidate field and return a Pareto report.

    ``spec`` is the current solved specification.  No candidate is applied;
    callers must send the chosen change set through the validated variant
    pipeline.  ``winner`` is a single-objective recommendation among feasible,
    changed candidates; the Pareto front is reported separately and neither is
    a claim of global optimality.
    """
    objective = objective if objective in OBJECTIVE_META else "weight"
    try:
        budget = max(2, min(12, int(budget or 9)))
    except (TypeError, ValueError):
        budget = 9

    selected_axes = []
    for name in (axes or (objective,)):
        if name in OBJECTIVE_META and name not in selected_axes:
            selected_axes.append(str(name))
    if not selected_axes:
        selected_axes = [objective]

    blueprints = _blueprints(spec, objective)[:budget]
    candidates = [evaluate(spec, item, objective, axes=selected_axes)
                  for item in blueprints]
    baseline_checksum = str(spec.get("derived.checksum") or "")
    measured = ("mass_g", "material_cost_usd", "process_cost_usd",
                "part_count", "complication_count", "dfm_failures",
                "tolerance_failures", "stack_margin_mm", "tolerance_risk",
                "failing_checks")
    base_metrics = next((item["metrics"] for item in candidates
                         if item["id"] == "baseline"), {})
    for item in candidates:
        changed = [key for key in measured
                   if item["metrics"].get(key) != base_metrics.get(key)]
        item["changed_metrics"] = changed
        item["measurable"] = bool(changed)

    feasible = [item for item in candidates if item["feasible"]]
    fronts = non_dominated_sort(feasible, selected_axes)
    for rank, front in enumerate(fronts, start=1):
        distances = crowding_distance(front, selected_axes)
        for item in front:
            item["pareto_rank"] = rank
            item["crowding_distance"] = distances.get(str(item["id"]), 0.0)
    for item in candidates:
        item["dominates"] = [other["id"] for other in candidates
                             if item["feasible"] and other["feasible"]
                             and dominates(item, other, selected_axes)]
        item["dominated_by"] = [other["id"] for other in candidates
                                if item["feasible"] and other["feasible"]
                                and dominates(other, item, selected_axes)]

    def sort_key(item: dict):
        crowd = item["crowding_distance"]
        crowd_key = -1.0 if crowd is None or math.isinf(crowd) else -float(crowd)
        return (not item["feasible"], item["pareto_rank"] or 99,
                crowd_key, item["score"], float(item["metrics"].get("mass_g") or 0.0),
                str(item["id"]))

    ranked = sorted(candidates, key=sort_key)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    actionable = [item for item in ranked
                  if item["feasible"] and item["changes"] and item["status"] == "ok"]
    winner = actionable[0] if actionable else next(
        (item for item in ranked if item["id"] == "baseline"), ranked[0] if ranked else None)

    seed_source = json.dumps(
        {"baseline": baseline_checksum, "objective": objective,
         "axes": selected_axes, "budget": budget, "generator": GENERATOR_VERSION},
        sort_keys=True, separators=(",", ":"))
    seed_digest = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()
    blueprint_signature = hashlib.sha256(json.dumps(
        blueprints, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]

    return {
        "objective": objective,
        "budget": budget,
        "axes": selected_axes,
        "objectives": {name: dict(meta) for name, meta in OBJECTIVE_META.items()},
        "baseline_checksum": baseline_checksum,
        "baseline": next((item for item in candidates if item["id"] == "baseline"), None),
        "candidates": candidates,
        "ranked": [item["id"] for item in ranked],
        "winner": winner["id"] if winner else None,
        "frontier": [item["id"] for item in fronts[0]] if fronts else [],
        "frontiers": [
            {"rank": rank, "ids": [item["id"] for item in front],
             "labels": [item["label"] for item in front]}
            for rank, front in enumerate(fronts, start=1)
        ],
        "feasible_count": len(feasible),
        "assumptions": {
            "material_usd_per_kg": MATERIAL_USD_PER_KG,
            "process_usd_per_part": PROCESS_USD_PER_PART,
            "tolerance_worst_case_mm": 0.28,
            "note": "Costs are planning estimates; mass, margin and counts are calculated from geometry.",
        },
        "reproducibility": {
            "engine": "watchcad.optimise",
            "generator_version": GENERATOR_VERSION,
            "seed": seed_digest[:16],
            "seed_source": "sha256(baseline checksum + objective + axes + budget + generator version)",
            "random_sampling": False,
            "deterministic": True,
            "blueprint_signature": blueprint_signature,
            "candidate_field": "shared across objectives; objective only orders presentation",
            "note": "Re-running with the same baseline, objective, axes and budget reproduces this report exactly.",
        },
        "provenance": {
            "engine": "deterministic solver + mesh kernel + verifier",
            "model_used": False,
            "simulated": False,
            "physically_verified": False,
            "ranking": "Pareto dominance with NSGA-II non-dominated sorting and crowding distance",
            "claim": "No global-optimality claim; the field is a finite, deterministic probe set.",
        },
    }


def preview_scene(spec: Spec, changes: list[dict] | None) -> dict:
    """Build a candidate scene for 3-D preview without touching ``spec``."""
    build = build_candidate(spec, changes or [], label="preview")
    if build["scene"] is None:
        raise ValueError(build["error"] or "candidate could not be solved")
    return build["scene"]
