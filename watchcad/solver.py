"""Derivation solver: fills every redundant internal dimension.

Everything written here is reproducible from user-supplied essentials, and is
tagged `derived`.  The solver never invents user-facing aesthetics; it sizes the
train, the barrel, the stack and the energy budget.

Formulas are documented inline.  Numbers that are empirical carry a factor name
so the verification report can say *why* a value is an estimate.
"""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction

from . import gears
from .modules import BASE_DRAW_UW, QUARTZ_MODULES, UNLOCK_TORQUE_NMM, compose
from .schema import Spec

# --- documented empirical constants ---------------------------------------
CASE_WEIGHT_MM = 1.15          # case wall at the movement seat
MOVEMENT_CLEARANCE_MM = 0.9    # radial play between movement and case
PLATE_THICKNESS_MM = 1.20
BRIDGE_THICKNESS_MM = 0.70
BARREL_HEIGHT_MM = 1.20
HAND_STACK_MM = 0.90
CRYSTAL_GAP_MM = 0.25
CASEBACK_THICKNESS_MM = 1.20
DIAL_CLEARANCE_MM = 0.30
MAINSPRING_E_MPA = 200_000.0   # Young's modulus of Nivaflex-type spring steel
MAINSPRING_PRELOAD = 1.8       # empirical: winding preload / stress distribution
TRAIN_EFFICIENCY = 0.93        # per mesh, estimated
GEAR_MODULE_MM = 0.12
BPM_PER_HOUR = 60.0


def solve(spec: Spec) -> tuple[Spec, list[dict]]:
    """Return a solved copy plus a human-readable derivation report."""
    out = spec.copy()
    report: list[dict] = []
    out.data["meta"]["version"] = int(out.data["meta"].get("version", 1)) + 1

    _derive_case(out, report)
    if out.get("movement.kind") == "mechanical":
        _derive_mechanical(out, report)
    else:
        _derive_quartz(out, report)
    _derive_stack(out, report)
    _derive_hands(out, report)
    out.set("derived.mass_g", round(_estimate_mass(out), 2), tag="derived",
            note="sum of part volumes x material density")
    out.set("derived.checksum", _checksum(out), tag="derived")
    return out, report


# ---------------------------------------------------------------------------
def _write(spec: Spec, path: str, value, note: str, report: list[dict]):
    """Write a derived value unless the user owns or locked it."""
    if path in spec.locks:
        return False
    if spec.tag(path) == "user" and spec.exists(path):
        return False
    spec.set(path, value, tag="derived", note=note)
    report.append({"field": path, "value": value, "why": note})
    return True


def _derive_case(spec: Spec, report: list[dict]) -> None:
    diameter = spec.get("case.diameter_mm")
    movement_w = min(spec.get("movement.width_mm") or 28.0,
                     diameter - 2 * (CASE_WEIGHT_MM + MOVEMENT_CLEARANCE_MM))
    _write(spec, "movement.width_mm", round(movement_w, 2),
           "case bore minus wall and clearance", report)
    bore = diameter - 2 * (CASE_WEIGHT_MM + 0.45)
    _write(spec, "dial.diameter_mm", round(bore - 1.0, 2),
           "case bore minus rehaut clearance", report)
    _write(spec, "case.lug_to_lug_mm", round(diameter + 8.0, 1),
           "case diameter + 8 mm lug extension (rule of thumb)", report)
    _write(spec, "case.lug_width_mm", spec.get("case.lug_width_mm") or 20.0,
           "kept (industry default for this case size)", report)


def _derive_mechanical(spec: Spec, report: list[dict]) -> None:
    bph = float(spec.get("movement.beat_rate_bph") or 28800)
    reserve = float(spec.get("movement.power_reserve_h") or 42)
    turns = float(spec.get("movement.usable_barrel_turns") or 6.5)
    escape_teeth = int(spec.get("movement.escape_wheel_teeth") or 15)
    width = float(spec.get("movement.width_mm"))
    radius = width / 2
    module = GEAR_MODULE_MM

    # --- barrel ---------------------------------------------------------
    barrel_d = round(width * 0.34, 2)
    arbor_d = round(barrel_d / 3.0, 2)
    thickness = round(barrel_d / 78.0, 3)          # 0.11 mm at 8.6 mm bore
    length = math.pi * (barrel_d ** 2 - arbor_d ** 2) / (4 * thickness)
    theta = 2 * math.pi * turns
    torque = (MAINSPRING_E_MPA * thickness ** 3 * BARREL_HEIGHT_MM
              / (12 * length) * theta * MAINSPRING_PRELOAD)
    energy = torque * 1e-3 * theta * 0.5
    spec.set("derived.barrel", {
        "inner_diameter_mm": barrel_d,
        "arbor_diameter_mm": arbor_d,
        "height_mm": BARREL_HEIGHT_MM,
        "spring_thickness_mm": thickness,
        "spring_length_mm": round(length, 1),
        "usable_turns": turns,
        "torque_full_nmm": round(torque, 3),
        "energy_j": round(energy, 4),
        "formula": "M=E*s^3*h/(12L)*theta*preload; L=pi(D^2-d^2)/(4s)",
    }, tag="derived")
    report.append({"field": "derived.barrel", "value": f"{barrel_d} mm bore, "
                   f"{thickness} mm spring, {length:.0f} mm long", "why": "spring cavity fills 46% of movement width"})

    # --- going train ----------------------------------------------------
    # centre wheel = 1 rev/hour.  barrel -> centre sets the power reserve.
    target_bc = reserve / turns
    sol_bc = gears.best_ratio(target_bc, 1, module, radius, pinions=range(7, 17),
                              wheels=range(48, 121))
    if sol_bc is None:
        w, p, err = gears.approximate(target_bc, module, radius)
        sol_bc = gears.TrainSolution([gears.Stage("barrel", "centre", w, p, module,
                                                  module * (w + p) / 2, w / p)], target_bc, w / p, err * 1e4)
    bc = sol_bc.stages[0]
    bc.driver, bc.driven = "barrel", "centre"

    # centre -> fourth = 60 (1 rev/h -> 1 rev/min), split over two meshes
    sol_cf = gears.best_ratio(60, 2, module, radius)
    sol_cf.stages[0].driver, sol_cf.stages[0].driven = "centre", "third"
    sol_cf.stages[1].driver, sol_cf.stages[1].driven = "third", "fourth"

    # fourth -> escape: escape wheel makes bph/(2*teeth) revs per hour
    esc_ratio = gears.escape_wheel_ratio(bph, escape_teeth)
    esc_pinion = 6
    esc_wheel = int(round(esc_ratio * esc_pinion))
    esc = gears.Stage("fourth", "escape", esc_wheel, esc_pinion, module,
                      module * (esc_wheel + esc_pinion) / 2, esc_wheel / esc_pinion,
                      "escape pinion driven by fourth wheel")

    # motion works: centre -> hour wheel = 12:1 over two meshes
    sol_mw = gears.best_ratio(12, 2, module * 0.9, radius * 0.8,
                              pinions=range(8, 17), wheels=gears.MOTION_WHEELS)
    hour_ratio = 12.0
    if sol_mw:
        sol_mw.stages[0].driver, sol_mw.stages[0].driven = "cannon pinion", "minute wheel"
        sol_mw.stages[1].driver, sol_mw.stages[1].driven = "minute pinion", "hour wheel"
        motion_stages = [s.to_dict() for s in sol_mw.stages]
        hour_ratio = sol_mw.actual_ratio
    else:
        motion_stages = []

    all_stages = [bc] + sol_cf.stages + [esc]
    total = 1.0
    for s in all_stages:
        total *= s.ratio

    layout = _layout(bc, sol_cf.stages[0], sol_cf.stages[1], esc, radius)
    spec.set("derived.train", {
        "module_mm": module,
        "beat_rate_bph": bph,
        "escape_wheel_teeth": escape_teeth,
        "stages": [s.to_dict() for s in all_stages],
        "motion_works": motion_stages,
        "barrel_to_centre": round(bc.ratio, 6),
        "centre_to_fourth": round(sol_cf.actual_ratio, 6),
        "fourth_to_escape": round(esc.ratio, 6),
        "total_barrel_to_escape": round(total, 2),
        "hour_hand_ratio": round(hour_ratio, 6),
        "effective_power_reserve_h": round(bc.ratio * turns, 2),
        "layout": layout,
        "seconds_per_hour": 3600.0,
    }, tag="derived")
    report.append({"field": "derived.train", "value": f"{len(all_stages)} meshes, "
                   f"total {total:.1f}:1 barrel-to-escape",
                   "why": "exact tooth counts for 60:1 centre-to-fourth and the beat rate"})

    # --- kinematic error ------------------------------------------------
    fourth_revs_per_hour = bph / (2 * escape_teeth) / esc.ratio
    rate_error = abs(fourth_revs_per_hour - 60.0) / 60.0 * 1e6
    report.append({"field": "derived.train.error_ppm", "value": round(rate_error, 3),
                   "why": "ratio closure against 1 rev/min at the fourth wheel"})

    # --- torque budget --------------------------------------------------
    mesh_count = len(all_stages)
    torque_esc = torque / total * TRAIN_EFFICIENCY ** mesh_count
    draw = BASE_DRAW_UW + compose(spec.get("movement.complications") or {})["draw_uW"]
    available_uw = torque * 1e-3 * theta / ((reserve or 1) * 3600.0) * 1e6
    spec.set("derived.torque", {
        "barrel_torque_nmm": round(torque, 3),
        "escape_torque_nmm": round(torque_esc, 6),
        "unlock_threshold_nmm": UNLOCK_TORQUE_NMM,
        "escape_margin": round(torque_esc / UNLOCK_TORQUE_NMM, 2),
        "available_power_uw": round(available_uw, 3),
        "required_power_uw": round(draw, 3),
        "power_margin": round(available_uw / draw, 2) if draw else 0,
        "efficiency_per_mesh": TRAIN_EFFICIENCY,
        "note": "energy and torque are estimates calibrated to typical movements",
    }, tag="derived")


def _derive_quartz(spec: Spec, report: list[dict]) -> None:
    name = (spec.get("quartz.module") or "generic 3-hand").lower()
    module = QUARTZ_MODULES.get(name, QUARTZ_MODULES["generic 3-hand"])
    _write(spec, "movement.width_mm", module["width_mm"], "quartz module footprint", report)
    _write(spec, "movement.height_mm", module["height_mm"], "quartz module height", report)
    _write(spec, "movement.jewels", 0, "quartz modules use no jewels", report)
    spec.set("movement.complications", {}, tag="derived", note="quartz build: 3-hand only")
    step_hz = float(spec.get("quartz.step_hz") or 1.0)
    # Lavet motor: two pulses per rotor revolution, seconds hand 1 rev/min
    # Lavet stepper: two pulses per rotor revolution, seconds hand 1 rev/min.
    target = (step_hz * 60.0) / 2.0
    sol = gears.best_ratio(target, 2, 0.10, spec.get("movement.width_mm") / 2,
                           pinions=range(8, 17), wheels=range(24, 81))
    stages = []
    if sol:
        sol.stages[0].driver, sol.stages[0].driven = "rotor", "intermediate"
        sol.stages[1].driver, sol.stages[1].driven = "intermediate", "seconds"
        stages = [s.to_dict() for s in sol.stages]
    capacity = float(spec.get("quartz.battery_mAh") or 32)
    current = float(spec.get("quartz.current_uA") or 1.2)
    life = capacity * 1000.0 / current / 8766.0 * 0.85
    spec.set("derived.quartz_train", {
        "pulses_per_minute": step_hz * 60,
        "rotor_revs_per_minute": step_hz * 60 / 2,
        "seconds_reduction": round(target, 4),
        "stages": stages,
        "note": "30:1 typical for a 1 Hz Lavet stepper",
    }, tag="derived")
    spec.set("derived.battery_life_years", round(life, 2), tag="derived",
             note="capacity / draw x 0.85 derating")
    spec.set("derived.torque", {"available_power_uw": 0.0, "required_power_uw": 0.0,
                                "power_margin": 0.0,
                                "note": "not applicable to quartz"}, tag="derived")
    report.append({"field": "derived.battery_life_years", "value": round(life, 2),
                   "why": "battery capacity / module current with 15% derating"})


def _derive_stack(spec: Spec, report: list[dict]) -> None:
    movement_h = float(spec.get("movement.height_mm") or 4.6)
    comps = compose(spec.get("movement.complications") or {})
    comp_h = comps["height_mm"]
    dial_h = float(spec.get("dial.thickness_mm") or 0.4)
    hand_h = float(spec.get("hands.stack_height_mm") or HAND_STACK_MM)
    crystal_h = float(spec.get("case.crystal_thickness_mm") or 1.2)
    back_h = CASEBACK_THICKNESS_MM
    gaps = DIAL_CLEARANCE_MM + CRYSTAL_GAP_MM
    required = back_h + movement_h + comp_h + dial_h + hand_h + crystal_h + gaps
    case_thickness = float(spec.get("case.thickness_mm") or 11)
    crystal_bottom = case_thickness - crystal_h
    z_hands_top = crystal_bottom - CRYSTAL_GAP_MM
    z_hands_bottom = z_hands_top - hand_h
    z_dial_top = z_hands_bottom - DIAL_CLEARANCE_MM
    z_dial_bottom = z_dial_top - dial_h
    z_comp_top = z_dial_bottom - 0.05
    z_movement_top = back_h + movement_h
    stack = {
        "caseback_mm": back_h,
        "movement_mm": movement_h,
        "complications_mm": comp_h,
        "dial_mm": dial_h,
        "hands_mm": hand_h,
        "crystal_mm": crystal_h,
        "clearances_mm": round(gaps, 2),
        "required_case_thickness_mm": round(required, 2),
        "available_case_thickness_mm": case_thickness,
        "margin_mm": round(case_thickness - required, 2),
        "radial_margin_mm": round((case_thickness - required), 2),
        # z levels, measured from the outside of the caseback
        "z_caseback_top": back_h,
        "z_plate_top": round(back_h + PLATE_THICKNESS_MM, 2),
        "z_movement_top": round(z_movement_top, 2),
        "z_complications_top": round(z_comp_top, 2),
        "z_dial_bottom": round(z_dial_bottom, 2),
        "z_dial_top": round(z_dial_top, 2),
        "z_hands_bottom": round(z_hands_bottom, 2),
        "z_hands_top": round(z_hands_top, 2),
        "z_crystal_bottom": round(crystal_bottom, 2),
        "z_case_top": round(case_thickness, 2),
        "movement_to_dial_gap_mm": round(z_dial_bottom - (z_movement_top + comp_h), 2),
    }
    spec.set("derived.stack", stack, tag="derived")
    report.append({"field": "derived.stack", "value": f"needs {required:.1f} mm of "
                   f"{case_thickness:.1f} mm case", "why": "sum of every layer plus clearances"})



def _derive_hands(spec: Spec, report: list[dict]) -> None:
    r = float(spec.get("dial.diameter_mm") or 32) / 2
    _write(spec, "hands.hour_length_mm", round(r * 0.58, 1), "58% of dial radius", report)
    _write(spec, "hands.minute_length_mm", round(r * 0.88, 1), "88% of dial radius", report)
    _write(spec, "hands.second_length_mm", round(r * 0.92, 1), "92% of dial radius", report)


def _layout(bc, cf0, cf1, esc, movement_radius: float) -> list[dict]:
    """Place the four arbors plus the barrel.

    The chain is centre -> third -> fourth -> escape, with the barrel meshing
    the centre pinion.  Each arbor carries the pinion of the incoming mesh and
    the wheel of the outgoing one, so its envelope is the larger of the two.

    `plane` is the height layer of the arbor's main wheel.  Meshing wheels share
    a plane; wheels on different planes may overlap in plan exactly as they do
    in a real movement.
    """
    m = bc.module_mm
    nodes = {
        "centre": dict(x=0.0, y=0.0, plane=1,
                       radius=max(cf0.wheel_teeth, bc.pinion_teeth) * m / 2 + m),
        "barrel": dict(radius=bc.wheel_teeth * m / 2 + m, plane=0, from_="centre", dist=bc.centre_mm),
        "third": dict(radius=max(cf1.wheel_teeth, cf0.pinion_teeth) * m / 2 + m,
                      plane=2, from_="centre", dist=cf0.centre_mm),
        "fourth": dict(radius=max(esc.wheel_teeth, cf1.pinion_teeth) * m / 2 + m,
                       plane=3, from_="third", dist=cf1.centre_mm),
        "escape": dict(radius=max(esc.pinion_teeth, 15) * m / 2 + m,
                       plane=3, from_="fourth", dist=esc.centre_mm),
    }
    order = ["centre", "barrel", "third", "fourth", "escape"]
    prefs = {"barrel": 180, "third": -40, "fourth": 130, "escape": 80}
    for name in order:
        node = nodes[name]
        if name == "centre":
            continue
        prev = nodes[node.pop("from_")]
        d = node.pop("dist")
        best = None
        for k in range(36):
            a = math.radians(prefs[name] + k * 10)
            x = prev["x"] + d * math.cos(a)
            y = prev["y"] + d * math.sin(a)
            # clearance only against arbors sharing this wheel plane
            same = [q for q in nodes.values()
                    if "x" in q and q is not node and q.get("plane") == node["plane"]]
            clear = min((math.hypot(x - q["x"], y - q["y"]) - (node["radius"] + q["radius"])
                         for q in same), default=99.0)
            inside = math.hypot(x, y) + node["radius"] <= movement_radius - 0.2
            if inside:
                score = clear - 0.02 * k
                if best is None or score > best[0]:
                    best = (score, round(x, 3), round(y, 3))
        if best is None:
            a = math.radians(prefs[name])
            best = (0.0, round(prev["x"] + d * math.cos(a), 3),
                    round(prev["y"] + d * math.sin(a), 3))
        node["x"], node["y"] = best[1], best[2]
    return [{"name": n, "x": nodes[n]["x"], "y": nodes[n]["y"],
             "plane": nodes[n]["plane"], "radius": round(nodes[n]["radius"], 3)}
            for n in order]


def _wheel_radius(stage, which: int) -> float:
    teeth = stage.wheel_teeth if which == 0 else stage.pinion_teeth
    return stage.module_mm * teeth / 2 + stage.module_mm


# ---------------------------------------------------------------------------
MATERIAL_DENSITY = {
    "316L stainless steel": 8.0, "titanium": 4.5, "bronze": 8.8,
    "ceramic": 3.2, "carbon": 1.6, "gold": 19.3, "brass": 8.5,
}


def _estimate_mass(spec: Spec) -> float:
    """Envelope-level mass estimate (the detailed mesh pass refines it)."""
    d = spec.get("case.diameter_mm")
    l2l = spec.get("case.lug_to_lug_mm")
    t = spec.get("case.thickness_mm")
    density = MATERIAL_DENSITY.get(spec.get("case.material"), 8.0)
    # case as a ring plus lugs and caseback
    wall = 1.6
    ring_vol = math.pi * ((d / 2) ** 2 - (d / 2 - wall) ** 2) * t
    back_vol = math.pi * (d / 2 - 0.4) ** 2 * CASEBACK_THICKNESS_MM
    lug_vol = 2 * (l2l - d + 2) * spec.get("case.lug_width_mm") * 2.4 * 0.7
    case_g = (ring_vol + back_vol + lug_vol) * density / 1000.0
    mw = spec.get("movement.width_mm") or 28
    mh = spec.get("movement.height_mm") or 4.6
    movement_g = mw * mw * mh * 0.55 * 8.3 / 1000.0
    dial_g = math.pi * (spec.get("dial.diameter_mm") / 2) ** 2 * 0.4 * 2.7 / 1000
    return case_g + movement_g + dial_g


def _checksum(spec: Spec) -> str:
    payload = {k: v for k, v in spec.data.items() if k != "derived"}
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
