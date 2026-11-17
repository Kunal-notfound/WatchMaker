"""Parametric part builders.

Every mesh is a pure function of the solved spec.  Nothing here is random, so a
rebuild is deterministic and cacheable by spec checksum.

This is still a *presentation* model: it has correct envelopes, stacking,
material keys and deterministic surfaces, but no true boolean fillets, thread
forms, tolerances or solid-kernel validity.  The drawing/BOM layer carries the
manufacturing caveats.  ``quality`` (low/medium/high) trades triangle count for
viewport speed; it never changes a dimension.
"""

from __future__ import annotations

import math

from . import gears, materials, mesh as M
from .modules import MODULES
from .schema import Spec

STEEL = "#c9ced6"
DARK_STEEL = "#8d949c"
BRASS = "#c8a24a"
GOLD = "#e0c068"
JEWEL = "#b0304a"
BLACK = "#22262c"

CASE_COLORS = materials.CASE_COLOR
CASE_MATERIALS = materials.CASE_MATERIAL_KEY

DIAL_COLORS = {
    "black": "#14171c", "blue": "#1c3f6e", "green": "#204436", "white": "#e9e6dd",
    "silver": "#c6c8cb", "grey": "#6a7078", "gray": "#6a7078", "salmon": "#c98d72",
    "brown": "#5b4032", "red": "#7a2126", "champagne": "#d8c49a",
}

LUME_COLORS = {"green": "#c9f29b", "blue": "#8fd6ff", "cream": "#efe5bd"}

DENSITY = {key: value.density_g_cm3 for key, value in materials.MATERIALS.items()}
DENSITY.update({"dial": 2.7, "spring": 7.9, "pcb": 1.85})

_QUALITY = {"low": 0.45, "medium": 0.75, "high": 1.0}


def _quality_name(value: str | None) -> str:
    value = str(value or "high").lower()
    return value if value in _QUALITY else "high"


def _seg(base: int, quality: str) -> int:
    """Segment count for a circular primitive at a render preset.

    Counts are rounded up to a multiple of four so every circle keeps vertices
    at 0, 90, 180 and 270 degrees.  A low-preset watch therefore has fewer
    triangles but exactly the same measured envelope as the high preset.
    """
    raw = max(8, int(round(base * _QUALITY[_quality_name(quality)])))
    return int(math.ceil(raw / 4.0) * 4)


def _styled(mesh: M.Mesh, name: str, color: str, role: str, material_key: str,
            finish: str | None = None, texture: str | None = None) -> M.Mesh:
    mesh.name = name
    mesh.color = color
    mesh.role = role
    mesh.material = material_key
    mesh.meta = dict(mesh.meta or {})
    mesh.meta["material_key"] = material_key
    if finish:
        mesh.meta["finish"] = finish
    if texture:
        mesh.meta["texture"] = texture
    return mesh


# ---------------------------------------------------------------------------
def build_parts(spec: Spec, quality: str = "high") -> list[M.Mesh]:
    quality = _quality_name(quality)
    stack = spec.get("derived.stack") or {}
    if not stack:
        raise ValueError("spec is not solved: run solver first")
    parts: list[M.Mesh] = []
    parts += case_parts(spec, stack, quality)
    parts += movement_parts(spec, stack, quality)
    parts += complication_parts(spec, stack, quality)
    parts += dial_parts(spec, stack, quality)
    parts += hand_parts(spec, stack, quality)
    parts += strap_parts(spec, stack, quality)
    return parts


def dial_color(name: str) -> str:
    text = (name or "").strip()
    if text.startswith("#") and len(text) in (7, 4):
        body = text[1:]
        if all(c in "0123456789abcdefABCDEF" for c in body):
            if len(body) == 3:
                body = "".join(c * 2 for c in body)
            return f"#{body.lower()}"
    low = text.lower()
    for key, color in DIAL_COLORS.items():
        if key in low:
            return color
    return "#2b3037" if "dark" in low else "#3a4a5a"


def _lume_color(spec: Spec) -> str:
    return LUME_COLORS.get(str(spec.get("dial.lume_color") or "green"), LUME_COLORS["green"])


def _crystal_material(spec: Spec) -> str:
    return {"sapphire": "crystal", "mineral": "mineral", "acrylic": "acrylic"}.get(
        spec.get("case.crystal"), "crystal")


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------
def case_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    d = float(spec.get("case.diameter_mm"))
    t = float(spec.get("case.thickness_mm"))
    l2l = float(spec.get("case.lug_to_lug_mm"))
    lug_w = float(spec.get("case.lug_width_mm"))
    case_material = CASE_MATERIALS.get(spec.get("case.material"), "steel")
    color = CASE_COLORS.get(spec.get("case.material"), STEEL)
    finish = str(spec.get("case.finish") or "brushed")
    ro = d / 2
    ri = max(8.0, ro - 1.6)
    bezel_h = 1.6
    bezel_bottom = t - bezel_h
    crystal_h = float(spec.get("case.crystal_thickness_mm") or 1.2)
    crystal_bottom = float(stack.get("z_crystal_bottom") or (t - crystal_h))
    crystal_r = float(spec.get("dial.diameter_mm")) / 2 + 0.2
    seg = _seg(96, quality)
    parts: list[M.Mesh] = []

    # Mid case: a revolved profile with a chamfered lower edge, a gently curved
    # flank and a step at the bezel seat.  A true variable-radius fillet needs a
    # solid kernel; this is a deterministic presentation approximation.
    mid_profile = [
        (ri, 0.80), (ro - 0.9, 0.80), (ro - 0.35, 1.02), (ro, 1.65),
        (ro, t - 2.35), (ro - 0.22, t - 1.72), (ro - 0.70, bezel_bottom + 0.12),
        (ri + 0.25, bezel_bottom), (ri, bezel_bottom - 0.10),
    ]
    mid = M.revolve(mid_profile, segments=seg, closed=True)
    _styled(mid, "case_mid", color, "case", case_material, finish,
            materials.finish(finish).get("texture"))

    # A polished transition ring draws the eye along the bezel/case interface.
    transition = M.revolve([
        (ro - 0.72, bezel_bottom - 0.12), (ro - 0.08, bezel_bottom - 0.16),
        (ro - 0.08, bezel_bottom + 0.04), (ro - 0.72, bezel_bottom + 0.06),
    ], segments=seg, closed=True)
    _styled(transition, "case_transition", color, "case", case_material, "polished")
    parts += [mid, transition]

    # Spacer ring between movement and case wall.
    spacer = M.tube(max(8.0, float(spec.get("movement.width_mm")) / 2 + 0.3),
                    ro - 1.55, 1.2, float(stack.get("z_plate_top", 2.4)),
                    segments=seg)
    _styled(spacer, "movement_ring", "#3a3f46", "case", "steel", "bead-blasted")
    parts.append(spacer)

    # Bezel: a closed profile with a chamfer and a crystal-retaining lip.
    lip = max(crystal_r - 0.10, ri + 0.3)
    bezel_profile = [
        (ri + 0.15, bezel_bottom), (ro, bezel_bottom), (ro, t - 0.30),
        (ro - 0.30, t), (lip, t), (lip, t - 0.55),
        (ri + 0.70, bezel_bottom + 0.10),
    ]
    bezel = M.revolve(bezel_profile, segments=seg, closed=True)
    _styled(bezel, "bezel", color, "case", case_material, finish,
            materials.finish(finish).get("texture"))
    parts.append(bezel)

    bezel_style = str(spec.get("case.bezel") or "plain")
    if bezel_style in ("dive", "coin-edge", "fluted"):
        parts += _bezel_details(spec, t, ro, ri, lip, color, case_material,
                                bezel_style, quality)
    parts += _lugs(spec, d, l2l, lug_w, color, case_material, finish, quality)
    parts += _crown_parts(spec, d, t, color, case_material, finish, quality)
    parts += _caseback(spec, d, stack, color, case_material, finish, quality)
    parts += _crystal_and_gasket(spec, crystal_r, crystal_bottom, crystal_h, t, quality)
    return parts


def _bezel_details(spec, t, ro, ri, lip, color, case_material, style, quality):
    seg = _seg(72, quality)
    parts: list[M.Mesh] = []
    if style == "dive":
        insert = M.arc_ring(lip + 0.15, ro - 0.12, t - 0.10, t + 0.02,
                            start_deg=0, end_deg=360, segments=_seg(96, quality))
        _styled(insert, "bezel_insert", "#1f242a", "case", "ceramic", "matte")
        parts.append(insert)
        # 5-minute graduations around the insert.
        step = 12 if _quality_name(quality) == "low" else 24
        for i in range(step):
            a = 360.0 * i / step
            tick = M.box(-0.06, ro - 1.55, t + 0.03, 0.06, ro - 0.35, t + 0.09)
            tick.rotated_z(a)
            _styled(tick, "bezel_tick", "#e8e4d8", "case", "ceramic", "polished")
            parts.append(tick)
        triangle = M.extrude([(0, ro - 1.55), (0.42, ro - 0.55), (-0.42, ro - 0.55)],
                             t + 0.03, t + 0.11, "bezel_triangle")
        _styled(triangle, "bezel_marker", "#d8bd6a", "case", "gold", "polished")
        parts.append(triangle)
    else:
        teeth = 36 if _quality_name(quality) == "low" else 54
        for i in range(teeth):
            a = 360.0 * i / teeth
            ridge = M.box(-0.16, ro - 0.30, t - 0.55, 0.16, ro + 0.12, t - 0.08)
            ridge.rotated_z(a)
            _styled(ridge, "bezel_ridge", color, "case", case_material,
                    "polished" if style == "fluted" else "brushed")
            parts.append(ridge)
        if style == "coin-edge":
            ring = M.tube(ro - 0.35, ro + 0.10, t - 0.70, t - 0.16, segments=seg)
            _styled(ring, "bezel_edge", color, "case", case_material, "polished")
            parts.append(ring)
    return parts


def _lugs(spec, d, l2l, lug_w, color, case_material, finish, quality):
    """Build solid lugs whose centre-line follows the case-to-strap curve.

    The root starts inside the case flank and the tip ends at the spring-bar
    axis.  Width, thickness and centre height vary along that path, so the top
    surface reads as a continuous curved lug rather than a flat slab.
    """
    parts: list[M.Mesh] = []
    lug_count = int(spec.get("case.lug_count") or 4)
    lx_values = (-1, 1) if lug_count >= 4 else (0,)
    lug_style = str(spec.get("case.lug_style") or "curved")
    corner_steps = max(2, int(round(3 * _QUALITY[_quality_name(quality)])))
    for side in (-1, 1):
        y_root = side * (d / 2 - 1.0)
        y_tip = side * (l2l / 2 - 0.25)
        for lx in lx_values:
            path: list[tuple[float, float, float]] = []
            sections: list[tuple[float, float, float]] = []
            for fraction in (0.0, 0.30, 0.66, 1.0):
                y = y_root + (y_tip - y_root) * fraction
                arch = math.sin(math.pi * fraction) if lug_style != "straight" else 0.0
                centre_z = 2.72 + 0.34 * arch + 0.16 * fraction
                centre_x = lx * lug_w / 4.0 * (1.0 - 0.035 * fraction)
                width = max(6.0, lug_w / 2.05 - 0.85 * fraction)
                height = 3.0 - 0.72 * fraction
                radius = min(1.05, width * 0.16, height * 0.30)
                path.append((centre_x, y, centre_z))
                sections.append((width, height, radius))
            lug = M.sweep_sections(path, sections, "lug",
                                   corner_segments=corner_steps)
            if lug_style in ("curved", "drilled"):
                root_path = [
                    (lx * lug_w / 4.0, side * (d / 2 - 2.0), 2.68),
                    (lx * lug_w / 4.0, side * (d / 2 - 0.6), 2.82),
                    (lx * lug_w / 4.0 * 0.99, side * (d / 2 + 0.9), 2.88),
                ]
                root_sections = []
                for fade in (0.0, 0.5, 1.0):
                    width = max(6.0, lug_w / 2.25 - 0.35 * fade)
                    root_sections.append((width, 3.15 - 0.25 * fade,
                                          min(0.9, width * 0.16)))
                root = M.sweep_sections(root_path, root_sections, "lug_root",
                                        corner_segments=corner_steps)
                _styled(root, "lug_root", color, "case", case_material, finish)
                parts.append(root)
            _styled(lug, "lug", color, "case", case_material, finish,
                    materials.finish(finish).get("texture"))
            parts.append(lug)
            if lug_style == "drilled":
                pin = M.disc(0.28, 0.0, 0.32, segments=_seg(16, quality))
                pin.verts = [(x, y, z_) for x, y, z_ in rotate_y(pin.verts, 90)]
                pin.verts = [(x + lx * lug_w / 2.6, y + side * (l2l / 2 - 1.2),
                              z_ + 3.0) for x, y, z_ in pin.verts]
                _styled(pin, "lug_pin", DARK_STEEL, "case", "steel", "polished")
                parts.append(pin)
        bar = M.disc(0.45, 0.0, lug_w + 0.4, segments=_seg(16, quality))
        bar.verts = rotate_y(bar.verts, 90)
        bar.verts = [(x - (lug_w + 0.4) / 2, y + side * (l2l / 2 - 1.1),
                      z + 3.0) for x, y, z in bar.verts]
        _styled(bar, "spring_bar", DARK_STEEL, "case", "spring", "polished")
        parts.append(bar)
    return parts


def _crown_parts(spec, d, t, color, case_material, finish, quality):
    parts: list[M.Mesh] = []
    crown_r = float(spec.get("case.crown_diameter_mm")) / 2
    crown_z = (spec.get("derived.stack") or {}).get("z_crown_axis")
    if crown_z is None:
        crown_z = float(spec.get("case.crown_height_mm") or t * 0.45)
    tube = M.disc(crown_r * 0.52, 0, 1.15, segments=_seg(32, quality))
    tube.verts = [(x + d / 2 + 0.15, y, z + crown_z)
                  for x, y, z in rotate_y(tube.verts, 90)]
    _styled(tube, "crown_tube", DARK_STEEL, "case", "steel", "polished")
    parts.append(tube)

    # Knurled crown body.  The ridges are real radial geometry, not a texture.
    crown = M.knurled_disc(crown_r, 0.0, 2.2, teeth=_seg(36, quality),
                           depth=min(0.35, crown_r * 0.28),
                           segments=_seg(40, quality))
    crown.verts = [(x, y, z) for x, y, z in rotate_y(crown.verts, 90)]
    crown.verts = [(x + d / 2 + 1.05, y, z + crown_z) for x, y, z in crown.verts]
    _styled(crown, "crown", color, "case", case_material, finish)
    parts.append(crown)
    cap = M.disc(crown_r * 0.78, 0.0, 0.22, segments=_seg(32, quality))
    cap.verts = [(x, y, z) for x, y, z in rotate_y(cap.verts, 90)]
    cap.verts = [(x + d / 2 + 3.28, y, z + crown_z) for x, y, z in cap.verts]
    _styled(cap, "crown_cap", color, "case", case_material, "polished")
    parts.append(cap)

    guards = bool(spec.get("case.crown_guards")) or (
        spec.get("case.bezel") == "dive" and int(spec.get("case.water_resistance_m") or 0) >= 200)
    if guards:
        for side in (-1, 1):
            guard = M.rounded_slab(1.2, 2.8, 2.5, 0.4,
                                   segments=_seg(6, quality), name="crown_guard")
            guard.rotated_z(90).translated(
                d / 2 + 0.55, side * (crown_r + 0.75), crown_z - 0.7)
            _styled(guard, "crown_guard", color, "case", case_material, finish)
            parts.append(guard)

    if "chronograph" in (spec.get("movement.complications") or {}):
        for py in (-4.6, 4.6):
            pusher = M.tube(0.55, 1.05, 0, 2.2, segments=_seg(24, quality))
            pusher.verts = [(x + d / 2 + 0.4, y + py, z + crown_z)
                            for x, y, z in rotate_y(pusher.verts, 90)]
            _styled(pusher, "pusher", color, "case", case_material, "polished")
            parts.append(pusher)
            collar = M.tube(1.08, 1.38, 0, 0.45, segments=_seg(20, quality))
            collar.verts = [(x + d / 2 + 0.25, y + py, z + crown_z)
                            for x, y, z in rotate_y(collar.verts, 90)]
            _styled(collar, "pusher_collar", DARK_STEEL, "case", "steel", "polished")
            parts.append(collar)
    return parts


def _caseback(spec, d, stack, color, case_material, finish, quality):
    parts: list[M.Mesh] = []
    back_r = d / 2 - 0.35
    seg = _seg(96, quality)
    kind = str(spec.get("case.caseback") or "screw-down")
    if kind == "screw-down exhibition":
        ring = M.revolve([
            (back_r - 2.1, 0.0), (back_r, 0.0), (back_r, 0.95),
            (back_r - 0.20, 1.25), (back_r - 2.1, 1.25),
        ], segments=seg, closed=True)
        _styled(ring, "caseback", color, "case", case_material, finish)
        glass = M.chamfered_disc(back_r - 2.25, 0.55, 1.25, 0.10,
                                 segments=seg)
        _styled(glass, "caseback_glass", "#9fc4d8", "crystal",
                _crystal_material(spec), "polished")
        parts += [ring, glass]
        # A visible movement ring under the exhibition glass.
        inner = M.tube(back_r - 4.1, back_r - 2.4, 1.25, 1.55, segments=seg)
        _styled(inner, "caseback_ring", "#7d858d", "case", "steel", "brushed")
        parts.append(inner)
    else:
        if kind == "snap":
            profile = [(0.0, 0.0), (back_r - 1.0, 0.0), (back_r, 0.35),
                       (back_r, 1.0), (back_r - 0.55, 1.3), (0.0, 1.3)]
        else:
            profile = [(0.0, 0.0), (back_r - 0.8, 0.0), (back_r, 0.45),
                       (back_r, 1.0), (back_r - 0.3, 1.2), (0.0, 1.2)]
        back = M.revolve(profile, segments=seg, closed=True)
        _styled(back, "caseback", color, "case", case_material, finish,
                materials.finish(finish).get("texture"))
        parts.append(back)
        engrave = M.arc_ring(back_r - 3.2, back_r - 2.6, 0.03, 0.10,
                             segments=_seg(64, quality))
        _styled(engrave, "caseback_engraving", "#7d858d", "case", "steel",
                "polished")
        parts.append(engrave)
        if kind == "screw-down":
            for i in range(8):
                a = 360.0 * i / 8
                notch = M.box(-0.22, back_r - 0.55, 0.95, 0.22, back_r + 0.02, 1.26)
                notch.rotated_z(a)
                _styled(notch, "caseback_notch", "#7d858d", "case", "steel",
                        "polished")
                parts.append(notch)
    return parts


def _crystal_and_gasket(spec, crystal_r, crystal_bottom, crystal_h, t, quality):
    seg = _seg(96, quality)
    material = _crystal_material(spec)
    if spec.get("case.crystal_type") == "domed":
        total = max(0.2, crystal_h)
        rise = float(spec.get("case.crystal_dome_height_mm") or total)
        # The dome rise is the curved cap; a short cylindrical land keeps the
        # total thickness equal to the spec value even when the dome is shallow.
        edge = max(0.05, min(total * 0.9, total - rise))
        crystal = M.lens(crystal_r, crystal_bottom, total, edge=edge,
                         segments=seg, steps=_seg(8, quality))
    else:
        crystal = M.chamfered_disc(crystal_r, crystal_bottom,
                                   crystal_bottom + crystal_h, 0.10,
                                   segments=seg)
    _styled(crystal, "crystal", "#b9dcea" if material == "crystal" else "#c7e2ea",
            "crystal", material, "polished", "none")
    gasket = M.tube(crystal_r - 0.05, crystal_r + 0.18, crystal_bottom - 0.05,
                    crystal_bottom + 0.18, segments=_seg(64, quality))
    _styled(gasket, "crystal_gasket", "#1c1f22", "gasket", "rubber", "matte")
    return [crystal, gasket]


def rotate_y(verts, degrees=90):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return [(x * c + z * s, y, -x * s + z * c) for x, y, z in verts]


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------
def movement_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    if spec.get("movement.kind") != "mechanical":
        return _quartz_parts(spec, stack, quality)
    train = spec.get("derived.train") or {}
    barrel = spec.get("derived.barrel") or {}
    layout = {n["name"]: n for n in train.get("layout", [])}
    z_plate = 1.2
    z_wheel = z_plate + 1.2
    z_bridge = z_wheel + 0.35
    width = float(spec.get("movement.width_mm"))
    radius = width / 2 - 0.3
    seg72 = _seg(72, quality)
    parts: list[M.Mesh] = []

    plate = M.disc(radius, z_plate, z_plate + 1.2, segments=seg72)
    _styled(plate, "mainplate", "#b9a06a", "movement", "brass", "bead-blasted")
    parts.append(plate)

    b = layout.get("barrel", {"x": 0, "y": 0})
    bd = float(barrel.get("inner_diameter_mm", 9.0))
    drum = M.tube(bd / 2 - 0.15, bd / 2, z_wheel, z_bridge, segments=_seg(48, quality))
    _styled(drum, "barrel", "#cdb47a", "movement", "brass", "bead-blasted")
    drum.translated(b["x"], b["y"], 0)
    parts.append(drum)
    barrel_teeth = (train.get("stages") or [{"wheel_teeth": 84}])[0]["wheel_teeth"]
    stages = train.get("stages", [])
    barrel_pos = layout.get("barrel", {})
    centre_pos = layout.get("centre", {"x": 0.0, "y": 0.0})
    if stages:
        st0 = stages[0]
        theta = math.atan2(centre_pos.get("y", 0.0) - barrel_pos.get("y", 0.0),
                           centre_pos.get("x", 0.0) - barrel_pos.get("x", 0.0))
        wheel_rot, pinion_rot = gears.mesh_rotation(
            st0["wheel_teeth"], st0["pinion_teeth"], theta)
        parts.append(_gear_mesh("barrel_wheel", st0["module_mm"], st0["wheel_teeth"],
                                z_wheel + 0.06, barrel_pos, BRASS, 5,
                                phase_deg=math.degrees(wheel_rot)))
        parts.append(_gear_mesh("centre_pinion", st0["module_mm"],
                                st0["pinion_teeth"], z_wheel + 0.06,
                                centre_pos, DARK_STEEL, 5,
                                phase_deg=math.degrees(pinion_rot)))
    else:
        parts.append(_gear_mesh("barrel_wheel", 0.12, barrel_teeth,
                                z_bridge - 0.22, barrel_pos, BRASS, 5))
    lid = M.disc(bd / 2 - 0.2, z_bridge, z_bridge + 0.25, segments=_seg(40, quality))
    _styled(lid, "barrel_lid", "#e0c98d", "movement", "brass", "polished")
    parts.append(lid.translated(b["x"], b["y"], 0))

    stages = train.get("stages", [])
    if stages:
        for idx, st in enumerate(stages):
            if st["driver"] == "barrel":
                continue
            pos = layout.get(st["driver"]) or layout.get(st["driven"]) or {"x": 0, "y": 0}
            pinion_pos = layout.get(st["driven"], pos)
            theta = math.atan2(pinion_pos.get("y", 0.0) - pos.get("y", 0.0),
                               pinion_pos.get("x", 0.0) - pos.get("x", 0.0))
            wheel_rot, pinion_rot = gears.mesh_rotation(
                st["wheel_teeth"], st["pinion_teeth"], theta)
            plane = pos.get("plane", idx)
            wheel = _gear_mesh(f"{st['driver']}_wheel", st["module_mm"], st["wheel_teeth"],
                               z_wheel + 0.06 + plane * 0.28, pos, BRASS, idx,
                               phase_deg=math.degrees(wheel_rot))
            parts.append(wheel)
            pinion = _gear_mesh(f"{st['driven']}_pinion", st["module_mm"], st["pinion_teeth"],
                                z_wheel + 0.06 + plane * 0.28,
                                pinion_pos, DARK_STEEL, idx,
                                phase_deg=math.degrees(pinion_rot))
            parts.append(pinion)
            arbor = M.disc(0.35, z_plate + 0.1, z_bridge + 0.2,
                           segments=_seg(16, quality))
            _styled(arbor, "arbor", DARK_STEEL, "movement", "steel", "polished")
            parts.append(arbor.translated(pos["x"], pos["y"], 0))
            jewel = M.disc(0.65, z_plate + 1.05, z_plate + 1.15,
                           segments=_seg(20, quality))
            _styled(jewel, "jewel", JEWEL, "movement", "jewel", "polished")
            parts.append(jewel.translated(pos["x"], pos["y"], 0))

    esc = layout.get("escape", {"x": 0, "y": 0})
    esc_teeth = int(train.get("escape_wheel_teeth", 15))
    esc_plane = esc.get("plane", 3)
    parts.append(_gear_mesh("escape_wheel", stages[-1]["module_mm"] if stages else 0.12,
                            esc_teeth, z_wheel + 0.06 + esc_plane * 0.28, esc,
                            "#d9c68c"))
    fork = M.rounded_slab(1.4, 4.2, 0.35, 0.7, segments=_seg(6, quality),
                          name="pallet_fork")
    fork.rotated_z(35).translated(esc["x"] + 1.6, esc["y"] + 1.2, z_wheel + 0.5)
    _styled(fork, "pallet_fork", DARK_STEEL, "movement", "steel", "polished")
    parts.append(fork)
    pallet = M.rounded_slab(1.0, 1.8, 0.25, 0.3, segments=_seg(4, quality),
                            name="pallet_stones")
    pallet.rotated_z(-15).translated(esc["x"] + 2.6, esc["y"] + 2.1, z_wheel + 0.55)
    _styled(pallet, "pallet_stones", JEWEL, "movement", "jewel", "polished")
    parts.append(pallet)

    bal_x, bal_y = esc["x"] - 3.4, esc["y"] + 3.2
    bal = M.torus(3.0, 0.42, z=z_wheel + 0.75, segments=_seg(64, quality),
                  wire_segments=_seg(8, quality))
    _styled(bal, "balance_wheel", GOLD, "movement", "brass", "polished")
    parts.append(bal.translated(bal_x, bal_y, 0))
    for spoke in range(3):
        spoke_mesh = M.box(-3.0, -0.18, -0.18, 3.0, 0.18, 0.18)
        spoke_mesh.rotated_z(spoke * 60).translated(bal_x, bal_y, z_wheel + 0.75)
        _styled(spoke_mesh, "balance_spoke", GOLD, "movement", "brass", "polished")
        parts.append(spoke_mesh)
    parts.append(_hairspring(bal_x, bal_y, z_wheel + 0.95, quality))
    cock = M.rounded_slab(2.4, 6.4, 0.5, 1.0, segments=_seg(6, quality),
                          name="balance_cock")
    cock.rotated_z(-30).translated(bal_x - 0.7, bal_y + 1.9, z_bridge + 0.2)
    _styled(cock, "balance_cock", "#cfd5dc", "movement", "steel", "brushed")
    parts.append(cock)

    for name, r, dx, dy in (("ratchet_wheel", bd / 2 - 0.4, 0.0, 0.0),
                            ("crown_wheel", 2.1, bd / 2 + 1.6, 0.6),
                            ("click", 0.9, -bd / 2 - 1.1, -0.9)):
        part = _gear_mesh(name, 0.14, max(12, int(r * 12)), z_bridge + 0.55,
                          {"x": b["x"] + dx, "y": b["y"] + dy}, DARK_STEEL)
        parts.append(part)

    for label, cx, cy, length, width_, ang in (
            ("train_bridge", 0, -2.2, 16.5, 4.0, 18),
            ("barrel_bridge", b["x"] * 0.7, b["y"] - 0.4, 12.0, 3.2, -35)):
        bridge = M.rounded_slab(width_, length, 0.6, width_ / 2,
                                segments=_seg(8, quality), name=label)
        bridge.rotated_z(ang).translated(cx, cy, z_bridge)
        _styled(bridge, label, "#c2c9d0", "movement", "steel", "brushed")
        parts.append(bridge)

    # Automatic winding rotor: a real annular sector above the bridges.
    if spec.get("movement.winding") == "automatic":
        rotor = M.arc_ring(max(2.6, radius * 0.22), radius * 0.92,
                           z_bridge + 0.85, z_bridge + 1.05,
                           start_deg=-35, end_deg=215,
                           segments=_seg(48, quality))
        _styled(rotor, "rotor", "#b7a05e", "movement", "brass", "polished")
        parts.append(rotor)
        hub = M.tube(0.9, 2.4, z_bridge + 0.65, z_bridge + 1.12,
                     segments=_seg(28, quality))
        _styled(hub, "rotor_hub", "#cfd5dc", "movement", "steel", "polished")
        parts.append(hub)
    return parts


def _gear_mesh(name: str, module: float, teeth: int, z: float, pos: dict,
               color: str, seed: int = 0,
               phase_deg: float | None = None) -> M.Mesh:
    teeth = max(6, min(200, int(round(teeth))))
    prof = gears.gear_profile(teeth, max(0.05, module), points=3 if teeth > 40 else 4)
    m = M.extrude(prof["points"], z, z + 0.22, name)
    m.rotated_z(seed * 13.7 if phase_deg is None else phase_deg)
    m.translated(pos.get("x", 0.0), pos.get("y", 0.0), 0)
    _styled(m, name, color, "movement", "brass", "polished")
    m.meta.update({"teeth": teeth, "module_mm": module,
                   "pitch_diameter_mm": round(prof["r_pitch"] * 2, 3),
                   "phase_deg": round(seed * 13.7 if phase_deg is None else phase_deg, 4),
                   "profile": prof["profile"]})
    return m


def _hairspring(cx: float, cy: float, z: float, quality: str = "high") -> M.Mesh:
    """Flat Archimedean spiral extruded into a thin ribbon."""
    turns = 9
    pts = []
    steps = _seg(220, quality)
    for i in range(steps + 1):
        t = i / steps
        a = t * turns * 2 * math.pi
        r = 0.55 + t * 2.15
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    m = M.sweep_ribbon(pts, 0.05, z, z + 0.06, "hairspring")
    _styled(m, "hairspring", "#9fb4c8", "movement", "spring", "polished")
    return m


def _quartz_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    width = float(spec.get("movement.width_mm"))
    height = float(spec.get("movement.height_mm"))
    parts = []
    module = M.extrude(_circle_poly(width / 2 - 0.4, _seg(40, quality)),
                       1.2, 1.2 + height * 0.75, "quartz_module")
    _styled(module, "quartz_module", "#2f7a52", "movement", "pcb", "matte")
    parts.append(module)
    battery = M.disc(width * 0.22, 1.2 + height * 0.75, 1.2 + height,
                     segments=_seg(40, quality))
    _styled(battery, "battery", "#d8dde2", "movement", "steel", "brushed")
    battery.translated(-width * 0.18, 0, 0)
    parts.append(battery)
    coil = M.torus(1.1, 0.35, z=1.2 + height * 0.8, segments=_seg(24, quality),
                   wire_segments=_seg(6, quality))
    _styled(coil, "stepper_coil", "#b7793f", "movement", "copper", "polished")
    coil.translated(width * 0.2, -width * 0.18, 0)
    parts.append(coil)
    return parts


def _circle_poly(r: float, segments: int) -> list[tuple[float, float]]:
    return [(r * math.cos(2 * math.pi * i / segments),
             r * math.sin(2 * math.pi * i / segments)) for i in range(segments)]


# ---------------------------------------------------------------------------
# Complications
# ---------------------------------------------------------------------------
def complication_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    ids = list((spec.get("movement.complications") or {}).keys())
    if not ids:
        return []
    z = stack.get("z_dial_top", 6.0) + 0.02
    parts: list[M.Mesh] = []
    for module_id in ids:
        info = MODULES.get(module_id)
        if not info:
            continue
        if module_id in ("date", "day-date"):
            r = float(spec.get("dial.diameter_mm")) / 2 * 0.68
            ring = M.torus(r, 0.55, z=z, segments=_seg(96, quality),
                           wire_segments=_seg(6, quality))
            _styled(ring, f"{module_id}_ring", "#e8e4d8", "complication",
                    "brass", "polished")
            parts.append(ring)
        elif module_id == "moonphase":
            disc = M.disc(3.4, z, z + 0.15, segments=_seg(48, quality))
            _styled(disc, "moon_disc", "#20304c", "complication", "dial", "lacquered")
            disc.translated(0, -float(spec.get("dial.diameter_mm")) * 0.18, 0)
            parts.append(disc)
            for side in (-1, 1):
                moon = M.disc(0.9, z + 0.16, z + 0.2, segments=_seg(24, quality))
                _styled(moon, "moon", "#e6dfc4", "complication", "gold", "polished")
                moon.translated(side * 1.3, -float(spec.get("dial.diameter_mm")) * 0.18, 0)
                parts.append(moon)
        elif module_id == "gmt":
            hand = M.rounded_slab(1.2, 16.0, 0.3, 0.6, segments=_seg(6, quality),
                                  name="gmt_hand")
            hand.rotated_z(25).translated(0, 0, z + 0.35)
            _styled(hand, "gmt_hand", "#b03a3a", "complication", "steel", "polished")
            parts.append(hand)
        elif module_id == "power-reserve":
            for i in range(14):
                a = math.radians(-55 + i * 110 / 13)
                rr = float(spec.get("dial.diameter_mm")) * 0.30
                tick = M.box(-0.12, -0.5, 0, 0.12, 0.5, 0.08)
                tick.rotated_z(math.degrees(a) + 90).translated(
                    rr * math.cos(a), rr * math.sin(a), z + 0.1)
                _styled(tick, "power_reserve_indicator", "#e8e4d8",
                        "complication", "brass", "polished")
                parts.append(tick)
        elif module_id == "tourbillon":
            cage = M.tube(4.2, 5.0, z, z + 0.4, segments=_seg(48, quality))
            _styled(cage, "tourbillon_cage", "#d8bd6a", "complication",
                    "gold", "polished")
            cage.translated(0, -float(spec.get("dial.diameter_mm")) * 0.12, 0)
            parts.append(cage)
        elif module_id == "chronograph":
            for cx, cy in ((-9.5, 4.0), (9.5, 4.0)):
                counter = M.tube(1.7, 2.0, z, z + 0.25, segments=_seg(32, quality))
                _styled(counter, "chrono_counter", "#e8e4d8", "complication",
                        "brass", "polished")
                counter.translated(cx, cy, 0)
                parts.append(counter)
        else:
            marker = M.tube(2.0, 2.6, z, z + 0.2, segments=_seg(32, quality))
            _styled(marker, f"{module_id}_indicator", "#d8bd6a", "complication",
                    "gold", "polished")
            parts.append(marker)
    return parts


# ---------------------------------------------------------------------------
# Dial and hands
# ---------------------------------------------------------------------------
def dial_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    r = float(spec.get("dial.diameter_mm")) / 2
    thickness = float(spec.get("dial.thickness_mm") or 0.4)
    z1 = float(stack.get("z_dial_top", 6.0))
    z0 = z1 - thickness
    color = dial_color(spec.get("dial.color"))
    finish = str(spec.get("dial.finish") or "sunburst")
    surface = materials.dial_material(finish)
    parts: list[M.Mesh] = []
    seg = _seg(96, quality)

    dial = M.chamfered_disc(r, z0, z1, min(0.10, thickness * 0.25),
                            segments=seg)
    _styled(dial, "dial", color, "dial", "dial", finish, surface.texture)
    parts.append(dial)

    rehaut_top = float(stack.get("z_crystal_bottom", z1 + 1.0))
    rehaut = M.revolve([
        (r - 0.04, z1 + 0.02), (r + 0.34, rehaut_top),
        (r + 0.34, rehaut_top - 0.10), (r - 0.04, z1 - 0.08),
    ], segments=seg, closed=True)
    _styled(rehaut, "rehaut", "#8f959c", "dial", "steel", "polished")
    parts.append(rehaut)

    style = str(spec.get("dial.index_style") or "applied batons")
    lume_on = bool(spec.get("dial.lume"))
    lume_color = _lume_color(spec)
    index_material = _index_material(spec)
    if style != "no indices":
        parts += _indices(spec, r, z1, style, lume_on, lume_color,
                          index_material, quality)
    if spec.get("dial.minute_track", True):
        parts += _minute_track(spec, r, z1, quality)
    if spec.get("dial.date_window"):
        parts += _date_window(spec, r, z1, quality)
    if spec.get("dial.small_seconds"):
        parts += _subdial(spec, r * 0.46, -r * 0.42, r * 0.16, z1,
                          "small_seconds", quality)
    if "chronograph" in (spec.get("movement.complications") or {}):
        parts += _subdial(spec, -r * 0.30, r * 0.18, r * 0.15, z1,
                          "chrono_minutes", quality)
        parts += _subdial(spec, r * 0.30, r * 0.18, r * 0.15, z1,
                          "chrono_seconds", quality)

    logo = spec.get("dial.logo_text")
    if logo:
        mark = M.rounded_slab(5.0, 0.28, 0.04, 0.10, segments=_seg(6, quality),
                              name="dial_logo")
        mark.translated(0, r * 0.42, z1 + 0.02)
        _styled(mark, "dial_logo", "#e8e4d8", "dial", "gold", "polished")
        mark.meta["text"] = logo
        parts.append(mark)
    return parts


def _index_material(spec: Spec) -> tuple[str, str]:
    name = str(spec.get("dial.index_material") or "polished steel")
    if name in ("gold", "white gold"):
        return "gold", "#e0c068" if name == "gold" else "#d8d8d8"
    return "steel", "#e9edf2"


def _indices(spec, r, z, style, lume_on, lume_color, index_material, quality):
    parts: list[M.Mesh] = []
    material_key, color = index_material
    applied = style in ("applied batons", "applied numerals", "arabic numerals",
                        "roman numerals")
    height = 0.16 if applied else 0.035
    for i in range(12):
        angle = i * 30
        hour = 12 if i == 0 else i
        if style in ("printed", "applied batons"):
            length = r * 0.17 if i % 3 == 0 else r * 0.11
            width = 0.44 if i % 3 == 0 else 0.32
            inner = r * 0.77
            bar = M.box(-width / 2, inner, z + 0.015,
                        width / 2, inner + length, z + 0.015 + height)
            bar.rotated_z(angle)
            _styled(bar, "index", color, "dial", material_key,
                    "polished" if applied else "lacquered")
            bar.meta["index_kind"] = "baton"
            bar.meta["applied"] = applied
            parts.append(bar)
            if applied:
                bevel = M.box(-width * 0.34, inner + 0.05, z + 0.015 + height,
                              width * 0.34, inner + length - 0.05,
                              z + 0.015 + height + 0.04)
                bevel.rotated_z(angle)
                _styled(bevel, "index_bevel", color, "dial", material_key,
                        "polished")
                parts.append(bevel)
            if lume_on and i % 3 == 0:
                lume = M.box(-width * 0.22, inner + 0.15, z + 0.015 + height,
                             width * 0.22, inner + length - 0.15,
                             z + 0.015 + height + 0.025)
                lume.rotated_z(angle)
                _styled(lume, "lume_marker", lume_color, "dial", "lume", "matte")
                parts.append(lume)
        else:
            strokes = (_roman_strokes(hour) if style == "roman numerals"
                       else _arabic_strokes(hour))
            parts += _numeral_parts(strokes, r * 0.72, angle, z, height,
                                    material_key, color, quality,
                                    style == "roman numerals")
            if lume_on and i % 3 == 0:
                pip = M.disc(max(0.18, r * 0.016), z + 0.02,
                             z + 0.02 + height * 0.6, segments=_seg(16, quality))
                pip.translated(r * 0.90 * math.sin(math.radians(angle)),
                               r * 0.90 * math.cos(math.radians(angle)), 0)
                _styled(pip, "lume_marker", lume_color, "dial", "lume", "matte")
                parts.append(pip)
    return parts


def _arabic_strokes(number: int) -> list[list[tuple[float, float]]]:
    """A small, dependency-free stroke font for Arabic hour numerals.

    The glyphs are deliberately simple line strokes.  They are a visual index,
    not a typographic reproduction of any brand's dial printing.
    """
    digits = {
        "0": ["a", "b", "c", "d", "e", "f"],
        "1": ["b", "c"], "2": ["a", "b", "g", "e", "d"],
        "3": ["a", "b", "g", "c", "d"], "4": ["f", "g", "b", "c"],
        "5": ["a", "f", "g", "c", "d"], "6": ["a", "f", "g", "e", "c", "d"],
        "7": ["a", "b", "c"], "8": ["a", "b", "c", "d", "e", "f", "g"],
        "9": ["a", "b", "c", "d", "f", "g"],
    }
    segs = {
        "a": [(0.0, 0.55), (0.42, 0.55)],
        "b": [(0.42, 0.55), (0.42, 0.0)],
        "c": [(0.42, 0.0), (0.42, -0.55)],
        "d": [(0.0, -0.55), (0.42, -0.55)],
        "e": [(0.0, 0.0), (0.0, -0.55)],
        "f": [(0.0, 0.55), (0.0, 0.0)],
        "g": [(0.0, 0.0), (0.42, 0.0)],
    }
    text = str(int(number))
    out: list[list[tuple[float, float]]] = []
    width = 0.42
    gap = 0.12
    total = len(text) * width + (len(text) - 1) * gap
    x0 = -total / 2
    for char in text:
        for key in digits.get(char, []):
            out.append([(x + x0, y) for x, y in segs[key]])
        x0 += width + gap
    return out


def _roman_strokes(number: int) -> list[list[tuple[float, float]]]:
    values = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    n = max(1, min(12, int(number)))
    text = ""
    for value, glyph in values:
        while n >= value:
            text += glyph
            n -= value
    glyphs = {
        "I": [[(0.0, 0.5), (0.0, -0.5)]],
        "V": [[(-0.22, 0.5), (0.0, -0.5)], [(0.0, -0.5), (0.22, 0.5)]],
        "X": [[(-0.24, 0.5), (0.24, -0.5)], [(0.24, 0.5), (-0.24, -0.5)]],
    }
    out: list[list[tuple[float, float]]] = []
    width = 0.32
    gap = 0.10
    total = len(text) * width + max(0, len(text) - 1) * gap
    x0 = -total / 2
    for char in text:
        for stroke in glyphs.get(char, []):
            out.append([(x + x0, y) for x, y in stroke])
        x0 += width + gap
    return out


def _numeral_parts(strokes, radius, angle_deg, z, height, material_key, color,
                   quality, roman, scale: float = 2.0,
                   centre: tuple[float, float] = (0.0, 0.0)):
    parts: list[M.Mesh] = []
    # Small numerals sit at a fixed radius, oriented radially (top outward).
    for stroke in strokes:
        if len(stroke) < 2:
            continue
        mesh = M.sweep_ribbon(stroke, 0.10 if roman else 0.075, z + 0.02,
                              z + 0.02 + height, "numeral")
        # Scale the stroke font, move it to the dial edge and rotate it into place.
        mesh.verts = [(x * scale + centre[0], y * scale + centre[1] + radius, z_)
                      for x, y, z_ in mesh.verts]
        if angle_deg:
            mesh.rotated_z(angle_deg)
        _styled(mesh, "numeral", color, "dial", material_key,
                "polished" if height > 0.08 else "lacquered")
        mesh.meta["index_kind"] = "roman" if roman else "arabic"
        mesh.meta["applied"] = height > 0.08
        parts.append(mesh)
    return parts


def _minute_track(spec, r, z, quality):
    parts: list[M.Mesh] = []
    low = _quality_name(quality) == "low"
    for i in range(60):
        if low and i % 5:
            continue
        if i % 5 == 0:
            length, width = 0.34, 0.055
        else:
            length, width = 0.18, 0.032
        tick = M.box(-width, r * 0.925, z + 0.008,
                     width, r * 0.925 + length, z + 0.028)
        tick.rotated_z(i * 6)
        _styled(tick, "minute_tick", "#b9b3a4", "dial", "steel", "lacquered")
        parts.append(tick)
    return parts


def _date_window(spec, r, z, quality):
    position = str(spec.get("dial.date_window_position") or "3")
    angle = {"3": 0.0, "6": -90.0, "4.5": -45.0}.get(position, 0.0)
    radius = r * 0.66
    cx = radius * math.cos(math.radians(angle))
    cy = radius * math.sin(math.radians(angle))
    w, h = 2.0, 1.45
    parts: list[M.Mesh] = []
    frame_color = "#d8bd6a"
    for dx, dy, sx, sy in ((-1, 0, 0, 0), (1, 0, 0, 0), (0, -1, 0, 0), (0, 1, 0, 0)):
        if dx:
            bar = M.box(cx + dx * w - 0.14, cy - h, z + 0.05,
                        cx + dx * w + 0.14, cy + h, z + 0.13)
        else:
            bar = M.box(cx - w, cy + dy * h - 0.14, z + 0.05,
                        cx + w, cy + dy * h + 0.14, z + 0.13)
        _styled(bar, "date_frame", frame_color, "dial", "gold", "polished")
        parts.append(bar)
    disc = M.chamfered_disc(max(w, h) * 0.72, z + 0.03, z + 0.06, 0.02,
                            segments=_seg(32, quality))
    _styled(disc, "date_disc", "#e9e6dd", "dial", "dial", "matte")
    disc.translated(cx, cy, 0)
    parts.append(disc)
    digits = _arabic_strokes(15)
    parts += _numeral_parts(digits, 0, 0, z + 0.065, 0.035, "dial", "#171a1e",
                            quality, False, scale=1.35, centre=(cx, cy))
    patch = M.box(cx - w + 0.2, cy - h + 0.2, z + 0.02,
                  cx + w - 0.2, cy + h - 0.2, z + 0.03)
    _styled(patch, "date_patch", "#12151a", "dial", "dial", "matte")
    parts.append(patch)
    return parts


def _subdial(spec, cx, cy, radius, z, name, quality):
    parts: list[M.Mesh] = []
    inset = M.disc(radius, z + 0.015, z + 0.035, segments=_seg(40, quality))
    _styled(inset, name, "#2a2f36", "dial", "dial", "guilloche")
    inset.translated(cx, cy, 0)
    parts.append(inset)
    ring = M.torus(radius, 0.055, z=z + 0.04, segments=_seg(48, quality),
                   wire_segments=_seg(6, quality))
    _styled(ring, f"{name}_ring", "#d8d3c4", "dial", "steel", "polished")
    ring.translated(cx, cy, 0)
    parts.append(ring)
    hand = M.rounded_slab(0.10, radius * 0.82, 0.035, 0.04,
                          segments=_seg(4, quality), name=f"{name}_hand")
    hand.rotated_z(35).translated(cx, cy, z + 0.06)
    _styled(hand, f"{name}_hand", "#e9edf2", "hand", "steel", "polished")
    parts.append(hand)
    return parts


def hand_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    z_bottom = float(stack.get("z_hands_bottom",
                               stack.get("z_hands_top", 7.0)
                               - float(spec.get("hands.stack_height_mm") or 0.9)))
    t = float(spec.get("hands.thickness_mm") or 0.25)
    style = spec.get("hands.style")
    material_name = str(spec.get("hands.material") or "polished steel")
    material_key, color = _hand_material(material_name)
    lume_on = bool(spec.get("hands.lume")) and material_name != "blued steel"
    lume_color = _lume_color(spec)
    out: list[M.Mesh] = []

    hour_len = float(spec.get("hands.hour_length_mm"))
    minute_len = float(spec.get("hands.minute_length_mm"))
    second_len = float(spec.get("hands.second_length_mm"))

    out += _hand("hour_hand", style, hour_len, 3.4, t, z_bottom, -55, color,
                 material_key, lume_on, lume_color, quality)
    # Hands are deliberately separated in Z to preserve a real stack order.
    minute_z = z_bottom + t + 0.06
    out += _hand("minute_hand", style, minute_len, 2.6, t, minute_z, 60, color,
                 material_key, lume_on, lume_color, quality)

    second_z = minute_z + t + 0.06
    sec = M.rounded_slab(0.42, second_len + 4.0, t * 0.65, 0.18,
                         segments=_seg(4, quality), name="second_hand")
    sec.verts = [(x + (second_len - 4.0) / 2, y, z_) for x, y, z_ in sec.verts]
    sec.rotated_z(200).translated(0, 0, second_z)
    _styled(sec, "second_hand", "#b03a3a", "hand", "steel", "polished")
    out.append(sec)
    counter = M.disc(0.8, 0, t * 0.65, segments=_seg(20, quality))
    counter.rotated_z(200).translated(
        -math.cos(math.radians(200)) * (second_len - 4.0) / 2,
        -math.sin(math.radians(200)) * (second_len - 4.0) / 2, second_z)
    _styled(counter, "second_counterweight", "#b03a3a", "hand", "steel",
            "polished")
    out.append(counter)

    boss_z = z_bottom
    boss = M.tube(0.35, 0.72, boss_z, second_z + t * 0.65 + 0.08,
                  segments=_seg(20, quality))
    _styled(boss, "hand_boss", "#cfd5dc", "hand", "steel", "polished")
    out.append(boss)
    cap = M.chamfered_disc(0.42, second_z + t * 0.65 + 0.05,
                           second_z + t * 0.65 + 0.12, 0.03,
                           segments=_seg(20, quality))
    _styled(cap, "hand_cap", "#cfd5dc", "hand", "steel", "polished")
    out.append(cap)
    return out


def _hand_material(name: str) -> tuple[str, str]:
    if name == "gold":
        return "gold", "#e0c068"
    if name == "blued steel":
        return "steel", "#2b4a7a"
    if name == "lume-filled":
        return "steel", "#e9edf2"
    return "steel", "#e9edf2"


def _hand(name, style, length, width, thickness, z, angle, color,
          material_key="steel", lume_on=False, lume_color="#d8f0b0",
          quality="high"):
    tail = width * 1.1
    if style == "leaf":
        poly = [(0, -tail), (width / 2, length * 0.22), (width * 0.32, length * 0.75),
                (0, length), (-width * 0.32, length * 0.75), (-width / 2, length * 0.22)]
        m = M.extrude(poly, z, z + thickness, name)
    elif style == "dauphine":
        poly = [(0, -tail), (width / 2, length * 0.35), (0, length),
                (-width / 2, length * 0.35)]
        m = M.extrude(poly, z, z + thickness, name)
    elif style == "skeleton":
        m = M.box(-width / 2, -tail, z, width / 2, length * 0.15, z + thickness)
        tip = M.extrude([(0, length * 0.15), (width / 2, length * 0.2), (0, length),
                         (-width / 2, length * 0.2)], z, z + thickness, name + "_tip")
        m = M.merge([m, tip], name)
    else:  # baton / syringe
        body = M.rounded_slab(width, length + tail, thickness,
                              min(width / 2, 0.9), segments=_seg(5, quality),
                              name=name)
        body.verts = [(x + (length - tail) / 2, y, z_ + z) for x, y, z_ in body.verts]
        m = body
    m.rotated_z(angle)
    _styled(m, name, color, "hand", material_key, "polished")
    m.meta["length_mm"] = length
    parts = [m]
    if lume_on and style in ("baton", "leaf", "syringe", "dauphine"):
        if style == "dauphine":
            poly = [(0, length * 0.22), (width * 0.22, length * 0.42),
                    (0, length * 0.78), (-width * 0.22, length * 0.42)]
            inset = M.extrude(poly, z + thickness, z + thickness + 0.03,
                              name + "_lume")
        else:
            inset = M.rounded_slab(width * 0.42, max(1.0, length * 0.62),
                                   thickness + 0.025,
                                   min(width * 0.21, 0.4),
                                   segments=_seg(4, quality), name=name + "_lume")
            inset.verts = [(x + (length * 0.62 - width * 0.42) / 2,
                            y + length * 0.20, z_ + z)
                           for x, y, z_ in inset.verts]
        inset.rotated_z(angle)
        _styled(inset, name + "_lume", lume_color, "hand", "lume", "matte")
        parts.append(inset)
    return parts


# ---------------------------------------------------------------------------
# Strap
# ---------------------------------------------------------------------------
def _wrist_strap_geometry(width: float, taper: float, thickness: float,
                          l2l: float) -> dict:
    """Analytic wrist arc shared by every render preset."""
    end_y = max(8.0, l2l / 2 - 0.8)
    radius = max(end_y + 7.0, 30.0)
    theta0 = math.asin(min(0.985, end_y / radius))
    return {
        "width": width, "taper": taper, "thickness": thickness,
        "end_y": end_y, "radius": radius,
        "theta0": theta0, "theta1": 2.0 * math.pi - theta0,
        "z_centre": 3.0 - radius * math.cos(theta0),
    }


def _wrist_strap_point(geometry: dict, theta: float) -> tuple[float, float, float]:
    radius = geometry["radius"]
    return (0.0, radius * math.sin(theta),
            geometry["z_centre"] + radius * math.cos(theta))


def _wrist_strap_section(geometry: dict, theta: float) -> tuple[float, float, float]:
    y = geometry["radius"] * math.sin(theta)
    away = 1.0 - min(1.0, abs(y) / geometry["end_y"])
    width = max(8.0, geometry["width"] - geometry["taper"] * away)
    thickness = geometry["thickness"]
    corner = min(0.65, thickness * 0.34, width * 0.12)
    return width, thickness, corner


def _wrist_strap_frame(geometry: dict, theta: float):
    """Width, tangent and outward normal at one point of the wrist arc."""
    return ((1.0, 0.0, 0.0),
            (0.0, math.cos(theta), -math.sin(theta)),
            (0.0, math.sin(theta), math.cos(theta)))


def _wrist_strap_theta(geometry: dict, fraction: float) -> float:
    return (geometry["theta0"]
            + fraction * (geometry["theta1"] - geometry["theta0"]))


def _wrist_strap_sections(geometry: dict, quality: str):
    """Centre-line and sections for one continuous wrist-following strap.

    Critical angles (both spring-bar ends, +/-Y and the low point) are always
    sampled.  Extra points are distributed between those anchors by the render
    preset, so a low-preset band has fewer triangles but the same envelope.
    """
    theta0 = geometry["theta0"]
    theta1 = geometry["theta1"]
    anchors = (theta0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0, theta1)
    total = max(16, 2 * int(round(32 * _QUALITY[_quality_name(quality)])))
    span = theta1 - theta0
    thetas: list[float] = []
    for start, end in zip(anchors, anchors[1:]):
        count = max(2, int(round(total * (end - start) / span)))
        thetas.extend(start + (end - start) * index / count
                      for index in range(count))
    thetas.append(theta1)
    path = [_wrist_strap_point(geometry, theta) for theta in thetas]
    sections = [_wrist_strap_section(geometry, theta) for theta in thetas]
    return path, sections


def _place_on_frame(mesh: M.Mesh, centre, width_axis, tangent, normal) -> M.Mesh:
    """Map a locally XY/Z mesh onto a swept section (width/tangent/normal)."""
    mesh.verts = [
        tuple(centre[axis] + width_axis[axis] * x + tangent[axis] * y
              + normal[axis] * z for axis in range(3))
        for x, y, z in mesh.verts
    ]
    return mesh


def strap_parts(spec: Spec, stack: dict, quality: str = "high") -> list[M.Mesh]:
    material_name = str(spec.get("strap.material") or "none")
    if material_name == "none":
        return []
    width = float(spec.get("strap.width_mm") or spec.get("case.lug_width_mm") or 20)
    taper = float(spec.get("strap.taper_mm") or 0)
    color = str(spec.get("strap.color") or "black")
    l2l = float(spec.get("case.lug_to_lug_mm"))
    key = {"leather": "leather", "rubber": "rubber",
           "steel bracelet": "steel", "nato": "nato"}[material_name]
    thickness = {"leather": 2.2, "rubber": 2.4, "steel bracelet": 2.8,
                 "nato": 1.3}[material_name]
    geometry = _wrist_strap_geometry(width, taper, thickness, l2l)
    path, sections = _wrist_strap_sections(geometry, quality)
    corner_steps = max(2, int(round(3 * _QUALITY[_quality_name(quality)])))
    parts: list[M.Mesh] = []

    # One continuous band replaces the old row of disconnected flat links.
    band = M.sweep_sections(path, sections, "strap",
                            corner_segments=corner_steps)
    band_color = "#3f4b3a" if material_name == "nato" else (
        "#b7bec6" if material_name == "steel bracelet" else color)
    band_finish = "brushed" if material_name in ("nato", "steel bracelet") else "matte"
    band_texture = "weave" if material_name == "nato" else (
        "grain" if material_name in ("leather", "rubber") else None)
    _styled(band, "strap", band_color, "strap", key, band_finish, band_texture)
    parts.append(band)

    def add_section_part(name: str, fractions, width_delta: float,
                         height_delta: float, part_color: str,
                         part_material: str, finish: str) -> None:
        # Accessories are positioned on the analytic arc, not on a quality
        # dependent sample index.  Only the number of intermediate samples
        # changes with the preset.
        samples = max(3, int(round(8 * _QUALITY[_quality_name(quality)])))
        start, end = fractions
        thetas = [_wrist_strap_theta(
            geometry, start + (end - start) * index / (samples - 1))
                  for index in range(samples)]
        sub_path = [_wrist_strap_point(geometry, theta) for theta in thetas]
        sub_sections = []
        for theta in thetas:
            width, height, corner = _wrist_strap_section(geometry, theta)
            sub_sections.append((
                width + width_delta, height + height_delta,
                min(0.8, corner + max(0.0, width_delta) * 0.3)))
        mesh = M.sweep_sections(sub_path, sub_sections, name,
                                corner_segments=corner_steps)
        _styled(mesh, name, part_color, "strap", part_material, finish)
        parts.append(mesh)

    if material_name != "steel bracelet":
        for fraction in (0.24, 0.76):
            add_section_part("strap_keeper", (fraction - 0.02, fraction + 0.02),
                             1.6, 0.6, band_color, key, band_finish)
    else:
        link_count = 9 if _quality_name(quality) != "low" else 5
        for link in range(link_count):
            fraction = (link + 1) / (link_count + 1)
            add_section_part("strap_link", (fraction - 0.018, fraction + 0.018),
                             0.8, 0.5, "#c3c9d0", "steel", "brushed")

    if material_name in ("leather", "rubber"):
        for fraction in (0.58, 0.68, 0.78):
            theta = _wrist_strap_theta(geometry, fraction)
            width_axis, tangent, normal = _wrist_strap_frame(geometry, theta)
            point = _wrist_strap_point(geometry, theta)
            centre = tuple(point[axis] + normal[axis] * (thickness / 2.0 + 0.01)
                           for axis in range(3))
            hole = M.disc(0.34, 0.0, 0.08, segments=_seg(14, quality))
            _place_on_frame(hole, centre, width_axis, tangent, normal)
            _styled(hole, "strap_hole", "#14171c", "strap", "rubber", "matte")
            parts.append(hole)

        theta = _wrist_strap_theta(geometry, 0.93)
        width_axis, tangent, normal = _wrist_strap_frame(geometry, theta)
        point = _wrist_strap_point(geometry, theta)
        centre = tuple(point[axis] + normal[axis] * (thickness / 2.0 + 0.30)
                       for axis in range(3))
        buckle = M.tube(2.0, 2.7, 0.0, 0.55, segments=_seg(24, quality))
        _place_on_frame(buckle, centre, width_axis, tangent, normal)
        _styled(buckle, "buckle", "#c9ced6", "strap", "steel", "polished")
        parts.append(buckle)
    return parts


# ---------------------------------------------------------------------------
def build_scene(spec: Spec, quality: str = "high") -> dict:
    """Parts plus a tree, mass roll-up and viewer payload.

    ``quality`` is a rendering/performance hint for the mesh builders.  It does
    not alter the spec, the solved dimensions or the verification report.
    """
    quality = _quality_name(quality)
    parts = build_parts(spec, quality)
    tree: dict[str, list[str]] = {}
    masses = {}
    for p in parts:
        role = p.role
        group = {"case": "Case", "dial": "Dial", "hand": "Hands",
                 "movement": "Movement", "complication": "Complications",
                 "crystal": "Crystal", "gasket": "Case", "strap": "Strap"}.get(
                     role, "Other")
        tree.setdefault(group, []).append(p.name)
        density = DENSITY.get(p.material, materials.density(p.material))
        masses[p.name] = round(p.volume() * density / 1000.0, 3)
    return {
        "parts": parts,
        "tree": tree,
        "mass_g": round(sum(masses.values()), 2),
        "part_masses_g": masses,
        "triangles": sum(len(p.faces) for p in parts),
        "bounds": _assembly_bounds(parts),
        "quality": quality,
    }


def _assembly_bounds(parts) -> dict:
    if not parts:
        return {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
    mins = [min(p.bounds()["min"][i] for p in parts) for i in range(3)]
    maxs = [max(p.bounds()["max"][i] for p in parts) for i in range(3)]
    return {"min": [round(v, 2) for v in mins], "max": [round(v, 2) for v in maxs],
            "size": [round(maxs[i] - mins[i], 2) for i in range(3)]}
