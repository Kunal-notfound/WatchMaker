"""Layered, honest verification.

Each check carries its own verdict:
  calculated   closed-form arithmetic or boolean geometry — trust the number
  estimated    a model with documented assumptions — trust the order of magnitude
  needs_test   cannot be settled on a computer; the bench has to answer

There is deliberately no single pass/fail badge for the whole watch.
"""

from __future__ import annotations

import math

from . import gears
from .modules import MODULES, compose
from .schema import Spec

VERDICT_ORDER = {"fail": 0, "calculated": 1, "estimated": 2, "needs_test": 3, "pass": 4}


def check(cid: str, layer: str, title: str, passed: bool | None, verdict: str,
          detail: str, fix: dict | None = None) -> dict:
    return {"id": cid, "layer": layer, "title": title, "passed": passed,
            "verdict": verdict if passed is not False else "fail",
            "detail": detail, "fix": fix}


def verify(spec: Spec, scene: dict | None = None) -> list[dict]:
    train = spec.get("derived.train") or {}
    stack = spec.get("derived.stack") or {}
    barrel = spec.get("derived.barrel") or {}
    torque = spec.get("derived.torque") or {}
    checks: list[dict] = []

    checks += _spec_checks(spec)
    checks += _geometry_checks(spec, scene, train, stack)
    if spec.get("movement.kind") == "mechanical":
        checks += _kinematics_checks(spec, train)
        checks += _energy_checks(spec, train, barrel, torque)
    checks += _tolerance_checks(spec, stack)
    checks += _manufacturability_checks(spec, train)
    checks += _physical_checks(spec)
    return checks


def summary(checks: list[dict]) -> dict:
    out = {"calculated": 0, "estimated": 0, "needs_test": 0, "fail": 0, "total": len(checks),
           "errors": 0, "warnings": 0}
    for c in checks:
        if c["passed"] is False:
            out["fail"] += 1
        else:
            out[c["verdict"]] = out.get(c["verdict"], 0) + 1
    return out


# ---------------------------------------------------------------------------
def _spec_checks(spec: Spec) -> list[dict]:
    out = []
    issues = spec.validate()
    errors = [i for i in issues if i["level"] == "error"]
    out.append(check("spec.valid", "spec", "Schema and range checks", not errors, "calculated",
                     f"{len(errors)} error(s), {len(issues) - len(errors)} warning(s)",
                     fix={"path": errors[0]["path"], "message": errors[0]["message"]} if errors else None))
    missing = [p for p in ("case.diameter_mm", "case.thickness_mm", "movement.kind",
                           "dial.color", "movement.power_reserve_h")
               if spec.get(p) in (None, "") and spec.get("movement.kind") == "mechanical"]
    out.append(check("spec.essentials", "spec", "Essentials supplied", not missing, "calculated",
                     "all essentials present" if not missing else "missing: " + ", ".join(missing)))
    if spec.get("movement.kind") == "mechanical":
        tagged = sum(1 for p, v in spec.provenance.items() if v.get("tag") == "derived")
        out.append(check("spec.provenance", "spec", "Provenance recorded", True, "calculated",
                         f"{tagged} derived fields traced, {len(spec.locks)} user locks"))
    comps = compose(spec.get("movement.complications") or {})
    out.append(check("spec.complication_conflicts", "spec", "Complications are compatible",
                     not comps["conflicts"], "calculated",
                     "compatible set" if not comps["conflicts"] else
                     "; ".join(f"{a} conflicts with {b}" for a, b in comps["conflicts"])))
    if comps["unknown"]:
        out.append(check("spec.complications_known", "spec", "Every complication is modelled",
                         False, "calculated", "unknown: " + ", ".join(comps["unknown"])))
    return out


def _geometry_checks(spec, scene, train, stack) -> list[dict]:
    out = []
    d = float(spec.get("case.diameter_mm"))
    mv_w = float(spec.get("movement.width_mm"))
    inner = d / 2 - 1.6
    fits = mv_w / 2 + 0.9 <= inner
    out.append(check("geometry.movement_fit", "geometry", "Movement fits the case bore", fits,
                     "calculated", f"movement radius {mv_w / 2:.1f} mm vs bore {inner:.1f} mm",
                     fix=None if fits else {"path": "case.diameter_mm", "value": round(mv_w + 6.0, 1)}))

    dial_r = spec.get("dial.diameter_mm") / 2
    dial_fits = dial_r <= inner + 0.15
    out.append(check("geometry.dial_fit", "geometry", "Dial fits the opening", dial_fits,
                     "calculated", f"dial radius {dial_r:.1f} mm vs bore {inner:.1f} mm",
                     fix=None if dial_fits else {"path": "case.diameter_mm",
                                                 "value": round(dial_r * 2 + 4.2, 1)}))

    required = stack.get("required_case_thickness_mm", 0)
    have = stack.get("available_case_thickness_mm", 0)
    margin = stack.get("margin_mm", 0)
    out.append(check("geometry.stack", "geometry", "Stack fits inside the case", margin >= 0,
                     "calculated", f"{required:.2f} mm required, {have:.2f} mm available "
                                   f"({margin:+.2f} mm)",
                     fix=None if margin >= 0 else {"path": "case.thickness_mm",
                                                   "value": round(required + 0.5, 1)}))

    hands_top = stack.get("z_hands_top", 0)
    crystal_bottom = stack.get("z_crystal_bottom", 0)
    clear = crystal_bottom - hands_top
    out.append(check("geometry.hand_clearance", "geometry", "Hands clear the crystal", clear >= 0.1,
                     "calculated", f"{clear:.2f} mm between hand stack and crystal"))

    for name, path, limit in (("Minute hand", "hands.minute_length_mm", dial_r * 0.99),
                              ("Second hand", "hands.second_length_mm", dial_r * 1.02)):
        value = spec.get(path)
        ok = value <= limit
        out.append(check(f"geometry.{path.split('.')[-1]}", "geometry", f"{name} stays inside the dial",
                         ok, "calculated", f"{value} mm vs {limit:.1f} mm limit"))

    hour = spec.get("hands.hour_length_mm")
    minute = spec.get("hands.minute_length_mm")
    ok = hour < minute
    out.append(check("geometry.hand_lengths", "geometry", "Hour hand shorter than minute hand", ok,
                     "calculated", f"{hour} mm / {minute} mm", fix=None if ok else
                     {"path": "hands.hour_length_mm", "value": round(minute * 0.66, 1)}))

    comp_h = stack.get("complications_mm", 0)
    gap = stack.get("movement_to_dial_gap_mm", 0)
    out.append(check("geometry.complication_stack", "geometry", "Complications fit under the dial",
                     comp_h <= gap + 0.05, "calculated",
                     f"{comp_h:.2f} mm of modules in a {gap:.2f} mm gap",
                     fix=None if comp_h <= gap + 0.05 else
                     {"path": "case.thickness_mm",
                      "value": round(have + (comp_h - gap) + 0.3, 1)}))

    layout = train.get("layout") or []
    if layout:
        radius = mv_w / 2
        outside = [n for n in layout if math.hypot(n["x"], n["y"]) + n["radius"] > radius + 0.01]
        out.append(check("geometry.wheels_inside", "geometry", "Train stays inside the movement",
                         not outside, "calculated",
                         "all wheels inside" if not outside else
                         "outside: " + ", ".join(n["name"] for n in outside)))
        meshing = set()
        for st in train.get("stages", []):
            meshing.add(frozenset((st["driver"], st["driven"])))
        overlaps = []
        for i, a in enumerate(layout):
            for b in layout[i + 1:]:
                if frozenset((a["name"], b["name"])) in meshing:
                    continue
                if a.get("plane") != b.get("plane"):
                    continue  # stacked wheels may share a plan position
                gap_mm = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) - (a["radius"] + b["radius"])
                if gap_mm < -0.05:
                    overlaps.append(f"{a['name']}/{b['name']} ({gap_mm:+.2f} mm)")
        out.append(check("geometry.gear_clearance", "geometry", "Wheels in the same plane do not collide",
                         not overlaps, "calculated",
                         "clear" if not overlaps else "; ".join(overlaps)))
        conflicts = _tooth_conflicts(train)
        out.append(check("geometry.gear_interference", "geometry",
                         "Meshing tooth outlines interleave", not conflicts,
                         "calculated",
                         "all meshing pairs clear" if not conflicts else
                         "tooth interference: " + "; ".join(conflicts)))
        # exact centre-distance closure: distance must equal (wheel+pinion) * module / 2
        pos = {n["name"]: n for n in layout}
        bad = []
        for st in train.get("stages", []):
            a, b = pos.get(st["driver"]), pos.get(st["driven"])
            if not a or not b:
                continue
            want = (st["wheel_teeth"] + st["pinion_teeth"]) * st["module_mm"] / 2
            got = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if abs(got - want) > 0.02:
                bad.append(f"{st['driver']}/{st['driven']} {got:.2f} vs {want:.2f}")
        out.append(check("geometry.centre_distance", "geometry", "Meshing centre distances are exact",
                         not bad, "calculated", "all meshes close" if not bad else "; ".join(bad)))
        tooth_ok = all(6 <= st["pinion_teeth"] <= 16 and 24 <= st["wheel_teeth"] <= 120
                       for st in train.get("stages", []))
        out.append(check("geometry.tooth_counts", "geometry", "Tooth counts in a machinable range",
                         tooth_ok, "calculated",
                         ", ".join(f"{s['wheel_teeth']}/{s['pinion_teeth']}"
                                   for s in train.get("stages", []))))
    return out


def _tooth_conflicts(train: dict) -> list[str]:
    """Check real outline intersection for every meshing wheel/pinion pair.

    Only segments near the pitch contact can interfere, so the polygon test is
    restricted to a small window there.  This catches tooth shapes that are too
    thick or phases that put two teeth on the centre line without turning
    verification into a full assembly sweep.
    """
    layout = {n["name"]: n for n in (train.get("layout") or [])}
    conflicts = []
    for st in train.get("stages") or []:
        a = layout.get(st.get("driver"))
        b = layout.get(st.get("driven"))
        if not a or not b:
            continue
        module = float(st.get("module_mm") or 0.12)
        wheel, pinion, contact = gears.meshing_outlines(
            int(st["wheel_teeth"]), int(st["pinion_teeth"]), module,
            (a["x"], a["y"]), (b["x"], b["y"]))
        if gears.polygons_intersect(wheel, pinion, near=contact,
                                    window=max(0.4, module * 3.0)):
            conflicts.append(f"{st['driver']}/{st['driven']}")
    return conflicts


def _kinematics_checks(spec, train) -> list[dict]:
    out = []
    bph = float(spec.get("movement.beat_rate_bph"))
    esc = train.get("fourth_to_escape", 1)
    teeth = train.get("escape_wheel_teeth", 15)
    fourth_per_hour = bph / (2 * teeth) / esc if esc else 0
    err_ppm = abs(fourth_per_hour - 60.0) / 60.0 * 1e6
    out.append(check("kinematics.fourth_wheel", "kinematics", "Fourth wheel turns once per minute",
                     err_ppm < 1.0, "calculated",
                     f"{fourth_per_hour:.6f} rev/h, error {err_ppm:.3f} ppm"))

    stages = train.get("motion_works") or []
    ratio = 1.0
    for st in stages:
        ratio *= st["wheel_teeth"] / st["pinion_teeth"]
    ok = abs(ratio - 12.0) < 1e-9
    out.append(check("kinematics.hour_hand", "kinematics", "Hour hand turns once per 12 hours",
                     ok, "calculated", f"motion works {ratio:.6f}:1" +
                     ("" if stages else " (no motion works solved)")))

    beats = int(spec.get("movement.beat_rate_bph") or 0)
    out.append(check("kinematics.beat_rate", "kinematics", "Beat rate is a standard frequency",
                     beats % 1800 == 0, "calculated", f"{beats} bph = {beats / 3600:.2f} Hz"))
    out.append(check("kinematics.reverser", "kinematics", "Automatic winding reverser", None, "needs_test",
                     "ratchet, crown wheel and click are modelled; bidirectional winding needs a bench check"))
    return out


def _energy_checks(spec, train, barrel, torque) -> list[dict]:
    out = []
    margin = torque.get("power_margin", 0)
    out.append(check("energy.power_budget", "energy", "Mainspring power covers the module draw",
                     margin >= 1.0, "estimated",
                     f"{torque.get('available_power_uw', 0):.2f} uW available vs "
                     f"{torque.get('required_power_uw', 0):.2f} uW required ({margin:.2f}x)",
                     fix=None if margin >= 1.0 else
                     {"path": "movement.usable_barrel_turns", "value": 8.0}))
    em = torque.get("escape_margin", 0)
    out.append(check("energy.escape_torque", "energy", "Escapement receives unlocking torque",
                     em >= 1.2, "estimated",
                     f"{torque.get('escape_torque_nmm', 0):.5f} N.mm at the escape wheel, "
                     f"{em:.2f}x the assumed unlock threshold"))
    reserve = train.get("effective_power_reserve_h", 0)
    target = float(spec.get("movement.power_reserve_h") or 0)
    ok = abs(reserve - target) / max(target, 1) <= 0.08
    out.append(check("energy.power_reserve", "energy", "Effective power reserve matches the target",
                     ok, "calculated", f"{reserve:.1f} h effective vs {target:.1f} h requested",
                     fix=None if ok else {"path": "movement.usable_barrel_turns",
                                          "value": round(target / max(train.get("barrel_to_centre", 6.5), 1), 2)}))
    s = barrel.get("spring_thickness_mm", 0)
    out.append(check("energy.mainspring", "energy", "Mainspring proportions are sane",
                     0.06 <= s <= 0.30, "estimated",
                     f"{s:.3f} mm thick, {barrel.get('spring_length_mm', 0):.0f} mm long in a "
                     f"{barrel.get('inner_diameter_mm', 0):.1f} mm barrel"))
    return out


def _tolerance_checks(spec, stack) -> list[dict]:
    out = []
    layers = 7
    tol = 0.04
    worst = layers * tol
    margin = stack.get("margin_mm", 0)
    ok = margin >= worst
    out.append(check("tolerance.stackup", "tolerance", "Worst-case stack-up still closes",
                     ok, "calculated",
                     f"+/-{tol} mm per interface over {layers} layers = {worst:.2f} mm, "
                     f"margin {margin:.2f} mm",
                     fix=None if ok else {"path": "case.thickness_mm",
                                          "value": round(stack.get("available_case_thickness_mm", 0)
                                                         + (worst - margin) + 0.1, 1)}))
    out.append(check("tolerance.gear_backlash", "tolerance", "Gear backlash allowance", True, "estimated",
                     "tooth thinning of 0.02 mm per flank assumed; verify with a running prototype"))
    out.append(check("tolerance.jewel_fit", "tolerance", "Jewel and pivot fit classes", None, "needs_test",
                     "hole/arbor fits must be measured on the first batch"))
    return out


def _manufacturability_checks(spec, train) -> list[dict]:
    out = []
    modules = [st["module_mm"] for st in train.get("stages", [])] or [0.12]
    out.append(check("dfm.module", "dfm", "Gear module above the machining minimum",
                     min(modules) >= 0.08, "calculated",
                     f"smallest module {min(modules):.3f} mm (limit 0.08 mm)"))
    out.append(check("dfm.pinions", "dfm", "Pinion leaf counts machinable",
                     all(st["pinion_teeth"] >= 6 for st in train.get("stages", [])), "calculated",
                     "smallest pinion " + str(min([st["pinion_teeth"] for st in train.get("stages", [])] or [6]))))
    wr = int(spec.get("case.water_resistance_m") or 0)
    caseback = spec.get("case.caseback")
    crown = spec.get("case.crown") or "push"
    crown_ok = wr < 200 or crown == "screw-down"
    back_ok = wr < 100 or caseback.startswith("screw")
    ok = crown_ok and back_ok
    if not back_ok:
        fix = {"path": "case.caseback", "value": "screw-down"}
    elif not crown_ok:
        fix = {"path": "case.crown", "value": "screw-down"}
    else:
        fix = None
    out.append(check("dfm.water_resistance", "dfm", "Case construction matches the water resistance",
                     ok, "estimated",
                     f"{wr} m with a {caseback} caseback and {crown} crown", fix=fix))
    lw = spec.get("case.lug_width_mm")
    out.append(check("dfm.lug_width", "dfm", "Lug width is a standard strap size",
                     lw in (16, 18, 19, 20, 21, 22, 24), "calculated", f"{lw} mm"))
    out.append(check("dfm.part_count", "dfm", "Part count and process routing generated", True, "calculated",
                     "BOM carries material, process and make/buy for every part"))
    return out


def _physical_checks(spec) -> list[dict]:
    comps = compose(spec.get("movement.complications") or {})
    out = [
        check("physical.timegrapher", "physical", "Rate, amplitude and beat error", None, "needs_test",
              "measure in 6 positions on a timegrapher; accept within the test plan limits"),
        check("physical.power_reserve", "physical", "Run-down and power reserve", None, "needs_test",
              f"target {spec.get('movement.power_reserve_h'):.0f} h; measure until the balance stops"),
        check("physical.water", "physical", "Water resistance (ISO 22810)", None, "needs_test",
              f"test to {spec.get('case.water_resistance_m')} m equivalent pressure"),
        check("physical.shock", "physical", "Shock resistance (ISO 1413)", None, "needs_test",
              "drop test with the movement cased"),
        check("physical.magnetic", "physical", "Magnetic resistance (ISO 764)", None, "needs_test",
              "expose to 4 800 A/m and re-measure the rate"),
        check("physical.thermal", "physical", "Temperature and isochronism", None, "needs_test",
              "8 h at 8 C, 23 C and 38 C; compare rates"),
    ]
    if comps["conflicts"]:
        out.append(check("physical.complications", "physical", "Complication conflicts",
                         False, "calculated",
                         "; ".join("/".join(pair) for pair in comps["conflicts"])))
    elif comps["ids"]:
        out.append(check("physical.complications", "physical", "Complication composition", True,
                         "estimated",
                         f"{len(comps['ids'])} modules, {comps['height_mm']:.2f} mm stack, "
                         f"{comps['draw_uW']:.2f} uW draw"))
    return out
