"""Parametric part builders.

Every mesh is a pure function of the solved spec.  Nothing here is random, so a
rebuild is deterministic and cacheable by spec checksum.

The 3-D model is engineering-presentation grade: correct envelopes, correct
stacking and correct gear tooth counts.  Fillets, thread forms and cosmetic
details are simplified; the drawing layer carries the tolerances instead.
"""

from __future__ import annotations

import math

from . import gears, mesh as M
from .modules import MODULES
from .schema import Spec

STEEL = "#c9ced6"
DARK_STEEL = "#8d949c"
BRASS = "#c8a24a"
GOLD = "#e0c068"
JEWEL = "#b0304a"
BLACK = "#22262c"

CASE_COLORS = {
    "316L stainless steel": "#c9ced6", "titanium": "#b4bcc2",
    "bronze": "#b08d57", "ceramic": "#2b3037", "carbon": "#3a3f45",
}

DIAL_COLORS = {
    "black": "#14171c", "blue": "#1c3f6e", "green": "#204436", "white": "#e9e6dd",
    "silver": "#c6c8cb", "grey": "#6a7078", "gray": "#6a7078", "salmon": "#c98d72",
    "brown": "#5b4032", "red": "#7a2126", "champagne": "#d8c49a",
}

DENSITY = {"steel": 8.0, "brass": 8.5, "gold": 19.3, "jewel": 3.6,
           "crystal": 3.98, "dial": 2.7, "spring": 7.9, "titanium": 4.5}


# ---------------------------------------------------------------------------
def build_parts(spec: Spec) -> list[M.Mesh]:
    stack = spec.get("derived.stack") or {}
    if not stack:
        raise ValueError("spec is not solved: run solver first")
    parts: list[M.Mesh] = []
    parts += case_parts(spec, stack)
    parts += movement_parts(spec, stack)
    parts += complication_parts(spec, stack)
    parts += dial_parts(spec, stack)
    parts += hand_parts(spec, stack)
    return parts


def dial_color(name: str) -> str:
    low = (name or "").lower()
    for key, color in DIAL_COLORS.items():
        if key in low:
            return color
    return "#2b3037" if "dark" in low else "#3a4a5a"


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------
def case_parts(spec: Spec, stack: dict) -> list[M.Mesh]:
    d = float(spec.get("case.diameter_mm"))
    t = float(spec.get("case.thickness_mm"))
    l2l = float(spec.get("case.lug_to_lug_mm"))
    lug_w = float(spec.get("case.lug_width_mm"))
    color = CASE_COLORS.get(spec.get("case.material"), STEEL)
    wall = 1.15
    inner = max(8.0, d / 2 - 1.6)
    bezel_h = 1.6
    bezel_bottom = t - bezel_h
    crystal_r = float(spec.get("dial.diameter_mm")) / 2 + 0.2
    parts: list[M.Mesh] = []

    # mid case: tube from the caseback lip to the bezel
    parts.append(M.tube(d / 2 - 1.6, d / 2, 1.2, max(1.3, bezel_bottom), segments=96))
    mid = parts[-1]
    mid.name, mid.color, mid.role, mid.material = "case_mid", color, "case", "steel"

    # bezel with an inward lip that traps the crystal
    lip = crystal_r - 0.05
    bezel = M.revolve([(inner, bezel_bottom), (d / 2, bezel_bottom), (d / 2, t),
                       (lip, t), (lip, t - 0.6), (inner + 0.4, bezel_bottom + 0.2)],
                      segments=96, closed=True)
    bezel.name, bezel.color, bezel.role, bezel.material = "bezel", color, "case", "steel"
    parts.append(bezel)

    # spacer ring between movement and case wall
    spacer = M.tube(max(8.0, float(spec.get("movement.width_mm")) / 2 + 0.3),
                    d / 2 - 1.55, 1.2, stack.get("z_plate_top", 2.4), segments=96)
    spacer.name, spacer.color, spacer.role = "movement_ring", "#3a3f46", "case"
    parts.append(spacer)

    # caseback (solid or exhibition)
    back_r = d / 2 - 0.35
    if spec.get("case.caseback") == "screw-down exhibition":
        back = M.tube(back_r - 2.2, back_r, 0.0, 1.2, segments=96)
        glass = M.disc(back_r - 2.3, 0.6, 1.2, segments=96)
        glass.name, glass.color, glass.role, glass.material = "caseback_glass", "#9fc4d8", "crystal", "crystal"
        back.name, back.color, back.role, back.material = "caseback", color, "case", "steel"
        parts += [back, glass]
    else:
        back = M.revolve([(0.0, 0.0), (back_r, 0.0), (back_r, 0.9),
                          (back_r - 0.5, 1.2), (0.0, 1.2)], segments=96, closed=True)
        back.name, back.color, back.role, back.material = "caseback", color, "case", "steel"
        parts.append(back)

    # lugs: two per side, tapering outward
    ext = max(0.5, (l2l - d) / 2 + 1.2)
    for side in (-1, 1):
        for lx in (-1, 1):
            lug = M.rounded_slab(lug_w / 2.05, ext + 1.5, 2.6, 1.0, segments=6, name="lug")
            lug.rotated_z(90).translated(lx * lug_w / 4.0,
                                         side * (l2l / 2 - (ext + 1.5) / 2), 1.6)
            lug.color, lug.role, lug.material = color, "case", "steel"
            parts.append(lug)
        # spring bar between the lugs
        bar = M.disc(0.45, 0.0, lug_w + 0.4, segments=16)
        bar.verts = rotate_y(bar.verts, 90)
        bar.verts = [(x - (lug_w + 0.4) / 2, y + side * (l2l / 2 - 1.1), z + 3.0)
                     for x, y, z in bar.verts]
        bar.name, bar.color, bar.role, bar.material = "spring_bar", DARK_STEEL, "case", "steel"
        parts.append(bar)

    # crown and tube
    crown_r = float(spec.get("case.crown_diameter_mm")) / 2
    crown_z = t * 0.45
    tube = M.disc(crown_r * 0.55, 0, 1.0, segments=32)
    tube.verts = [(x + d / 2, y, z + crown_z) for x, y, z in rotate_y(tube.verts, 90)]
    tube.name, tube.color, tube.role, tube.material = "crown_tube", DARK_STEEL, "case", "steel"
    parts.append(tube)
    crown = M.disc(crown_r, 0, 2.0, segments=40)
    crown.verts = [(x + d / 2 + 1.1, y, z + crown_z) for x, y, z in rotate_y(crown.verts, 90)]
    crown.name, crown.color, crown.role, crown.material = "crown", color, "case", "steel"
    parts.append(crown)

    if "chronograph" in (spec.get("movement.complications") or {}):
        for py in (-4.6, 4.6):
            pusher = M.tube(0.6, 1.1, 0, 2.2, segments=24)
            pusher.verts = [(x + d / 2 + 0.6, y + py, z + crown_z) for x, y, z in rotate_y(pusher.verts, 90)]
            pusher.name, pusher.color, pusher.role, pusher.material = "pusher", color, "case", "steel"
            parts.append(pusher)

    # crystal
    cr = crystal_r
    if spec.get("case.crystal_type") == "domed":
        crystal = M.dome(cr, t - 1.15, 1.15, segments=96, steps=8)
    else:
        crystal = M.disc(cr, t - 1.15, t, segments=96)
    crystal.name, crystal.color, crystal.role, crystal.material = "crystal", "#b9dcea", "crystal", "crystal"
    parts.append(crystal)

    gasket = M.tube(cr - 0.05, cr + 0.18, t - 1.2, t - 1.0, segments=64)
    gasket.name, gasket.color, gasket.role, gasket.material = "crystal_gasket", "#1c1f22", "gasket", "rubber"
    parts.append(gasket)
    return parts


def rotate_y(verts, degrees=90):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return [(x * c + z * s, y, -x * s + z * c) for x, y, z in verts]


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------
def movement_parts(spec: Spec, stack: dict) -> list[M.Mesh]:
    if spec.get("movement.kind") != "mechanical":
        return _quartz_parts(spec, stack)
    train = spec.get("derived.train") or {}
    barrel = spec.get("derived.barrel") or {}
    layout = {n["name"]: n for n in train.get("layout", [])}
    z_plate = 1.2
    z_wheel = z_plate + 1.2
    z_bridge = z_wheel + 0.35
    width = float(spec.get("movement.width_mm"))
    radius = width / 2 - 0.3
    parts: list[M.Mesh] = []

    plate = M.disc(radius, z_plate, z_plate + 1.2, segments=72)
    plate.name, plate.color, plate.role, plate.material = "mainplate", "#b9a06a", "movement", "brass"
    parts.append(plate)

    # barrel drum with a toothed rim
    b = layout.get("barrel", {"x": 0, "y": 0})
    bd = float(barrel.get("inner_diameter_mm", 9.0))
    drum = M.tube(bd / 2 - 0.15, bd / 2, z_wheel, z_bridge, segments=48)
    drum.name, drum.color, drum.role, drum.material = "barrel", "#cdb47a", "movement", "brass"
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
    lid = M.disc(bd / 2 - 0.2, z_bridge, z_bridge + 0.25, segments=40)
    lid.name, lid.color, lid.role = "barrel_lid", "#e0c98d", "movement"
    parts.append(lid.translated(b["x"], b["y"], 0))

    # train wheels: use the real tooth counts from the solver
    stages = train.get("stages", [])
    if stages:
        # barrel stage wheel sits on the barrel; the rest on their arbors
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
            arbor = M.disc(0.35, z_plate + 0.1, z_bridge + 0.2, segments=16)
            arbor.name, arbor.role, arbor.material, arbor.color = "arbor", "movement", "steel", DARK_STEEL
            parts.append(arbor.translated(pos["x"], pos["y"], 0))
            jewel = M.disc(0.65, z_plate + 1.05, z_plate + 1.15, segments=20)
            jewel.name, jewel.role, jewel.material, jewel.color = "jewel", "movement", "jewel", JEWEL
            parts.append(jewel.translated(pos["x"], pos["y"], 0))

    # escape wheel and pallet fork
    esc = layout.get("escape", {"x": 0, "y": 0})
    esc_teeth = int(train.get("escape_wheel_teeth", 15))
    esc_plane = esc.get("plane", 3)
    parts.append(_gear_mesh("escape_wheel", stages[-1]["module_mm"] if stages else 0.12,
                            esc_teeth, z_wheel + 0.06 + esc_plane * 0.28, esc, "#d9c68c"))
    fork = M.rounded_slab(1.4, 4.2, 0.35, 0.7, segments=6, name="pallet_fork")
    fork.rotated_z(35).translated(esc["x"] + 1.6, esc["y"] + 1.2, z_wheel + 0.5)
    fork.color, fork.role, fork.material = DARK_STEEL, "movement", "steel"
    parts.append(fork)
    pallet = M.rounded_slab(1.0, 1.8, 0.25, 0.3, segments=4, name="pallet_stones")
    pallet.rotated_z(-15).translated(esc["x"] + 2.6, esc["y"] + 2.1, z_wheel + 0.55)
    pallet.color, pallet.role, pallet.material = JEWEL, "movement", "jewel"
    parts.append(pallet)

    # balance wheel + hairspring near the escape wheel
    bal_x, bal_y = esc["x"] - 3.4, esc["y"] + 3.2
    bal = M.torus(3.0, 0.42, z=z_wheel + 0.75, segments=64, wire_segments=8)
    bal.name, bal.color, bal.role, bal.material = "balance_wheel", GOLD, "movement", "brass"
    parts.append(bal.translated(bal_x, bal_y, 0))
    for spoke in range(3):
        spoke_mesh = M.box(-3.0, -0.18, -0.18, 3.0, 0.18, 0.18)
        spoke_mesh.rotated_z(spoke * 60).translated(bal_x, bal_y, z_wheel + 0.75)
        spoke_mesh.name = "balance_spoke"
        spoke_mesh.color, spoke_mesh.role, spoke_mesh.material = GOLD, "movement", "brass"
        parts.append(spoke_mesh)
    hair = _hairspring(bal_x, bal_y, z_wheel + 0.95)
    parts.append(hair)
    cock = M.rounded_slab(2.4, 6.4, 0.5, 1.0, segments=6, name="balance_cock")
    cock.rotated_z(-30).translated(bal_x - 0.7, bal_y + 1.9, z_bridge + 0.2)
    cock.color, cock.role, cock.material = "#cfd5dc", "movement", "steel"
    parts.append(cock)

    # keyless works: crown wheel, ratchet wheel and click above the barrel
    for name, r, dx, dy in (("ratchet_wheel", bd / 2 - 0.4, 0.0, 0.0),
                            ("crown_wheel", 2.1, bd / 2 + 1.6, 0.6),
                            ("click", 0.9, -bd / 2 - 1.1, -0.9)):
        part = _gear_mesh(name, 0.14, max(12, int(r * 12)), z_bridge + 0.55,
                          {"x": b["x"] + dx, "y": b["y"] + dy}, DARK_STEEL)
        parts.append(part)

    # bridges: one curved plate over the train, one over the barrel
    for label, cx, cy, length, width_, ang in (
            ("train_bridge", 0, -2.2, 16.5, 4.0, 18),
            ("barrel_bridge", b["x"] * 0.7, b["y"] - 0.4, 12.0, 3.2, -35)):
        bridge = M.rounded_slab(width_, length, 0.6, width_ / 2, segments=8, name=label)
        bridge.rotated_z(ang).translated(cx, cy, z_bridge)
        bridge.color, bridge.role, bridge.material = "#c2c9d0", "movement", "steel"
        parts.append(bridge)
    return parts


def _gear_mesh(name: str, module: float, teeth: int, z: float, pos: dict,
               color: str, seed: int = 0,
               phase_deg: float | None = None) -> M.Mesh:
    teeth = max(6, min(200, int(round(teeth))))
    prof = gears.gear_profile(teeth, max(0.05, module), points=3 if teeth > 40 else 4)
    m = M.extrude(prof["points"], z, z + 0.22, name)
    m.rotated_z(seed * 13.7 if phase_deg is None else phase_deg)
    m.translated(pos.get("x", 0.0), pos.get("y", 0.0), 0)
    m.color, m.role, m.material = color, "movement", "brass"
    m.meta = {"teeth": teeth, "module_mm": module, "pitch_diameter_mm": round(prof["r_pitch"] * 2, 3),
              "phase_deg": round(seed * 13.7 if phase_deg is None else phase_deg, 4),
              "profile": prof["profile"]}
    return m


def _hairspring(cx: float, cy: float, z: float) -> M.Mesh:
    """Flat Archimedean spiral extruded into a thin ribbon."""
    turns = 9
    pts = []
    steps = 220
    for i in range(steps + 1):
        t = i / steps
        a = t * turns * 2 * math.pi
        r = 0.55 + t * 2.15
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    m = M.sweep_ribbon(pts, 0.05, z, z + 0.06, "hairspring")
    m.color, m.role, m.material = "#9fb4c8", "movement", "spring"
    return m


def _quartz_parts(spec: Spec, stack: dict) -> list[M.Mesh]:
    width = float(spec.get("movement.width_mm"))
    height = float(spec.get("movement.height_mm"))
    parts = []
    module = M.extrude(_circle_poly(width / 2 - 0.4, 40), 1.2, 1.2 + height * 0.75, "quartz_module")
    module.color, module.role, module.material = "#2f7a52", "movement", "pcb"
    parts.append(module)
    battery = M.disc(width * 0.22, 1.2 + height * 0.75, 1.2 + height, segments=40)
    battery.name, battery.color, battery.role, battery.material = "battery", "#d8dde2", "movement", "steel"
    battery.translated(-width * 0.18, 0, 0)
    parts.append(battery)
    coil = M.torus(1.1, 0.35, z=1.2 + height * 0.8, segments=24, wire_segments=6)
    coil.name, coil.color, coil.material = "stepper_coil", "#b7793f", "copper"
    coil.translated(width * 0.2, -width * 0.18, 0)
    parts.append(coil)
    return parts


def _circle_poly(r: float, segments: int) -> list[tuple[float, float]]:
    return [(r * math.cos(2 * math.pi * i / segments),
             r * math.sin(2 * math.pi * i / segments)) for i in range(segments)]


# ---------------------------------------------------------------------------
# Complications
# ---------------------------------------------------------------------------
def complication_parts(spec: Spec, stack: dict) -> list[M.Mesh]:
    ids = list((spec.get("movement.complications") or {}).keys())
    if not ids:
        return []
    # indicators are drawn on the dial side so they stay visible in the viewer
    z = stack.get("z_dial_top", 6.0) + 0.02
    parts: list[M.Mesh] = []
    for module_id in ids:
        info = MODULES.get(module_id)
        if not info:
            continue
        if module_id in ("date", "day-date"):
            r = float(spec.get("dial.diameter_mm")) / 2 * 0.68
            ring = M.torus(r, 0.55, z=z, segments=96, wire_segments=6)
            ring.name, ring.color, ring.role, ring.material = f"{module_id}_ring", "#e8e4d8", "complication", "brass"
            parts.append(ring)
        elif module_id == "moonphase":
            disc = M.disc(3.4, z, z + 0.15, segments=48)
            disc.name, disc.color, disc.role = "moon_disc", "#20304c", "complication"
            disc.translated(0, -float(spec.get("dial.diameter_mm")) * 0.18, 0)
            parts.append(disc)
            for side in (-1, 1):
                moon = M.disc(0.9, z + 0.16, z + 0.2, segments=24)
                moon.name, moon.color = "moon", "#e6dfc4"
                moon.translated(side * 1.3, -float(spec.get("dial.diameter_mm")) * 0.18, 0)
                parts.append(moon)
        elif module_id == "gmt":
            hand = M.rounded_slab(1.2, 16.0, 0.3, 0.6, segments=6, name="gmt_hand")
            hand.rotated_z(25).translated(0, 0, z + 0.35)
            hand.color, hand.role, hand.material = "#b03a3a", "complication", "steel"
            parts.append(hand)
        elif module_id == "power-reserve":
            for i in range(14):
                a = math.radians(-55 + i * 110 / 13)
                rr = float(spec.get("dial.diameter_mm")) * 0.30
                tick = M.box(-0.12, -0.5, 0, 0.12, 0.5, 0.08)
                tick.rotated_z(math.degrees(a) + 90).translated(rr * math.cos(a), rr * math.sin(a), z + 0.1)
                tick.color, tick.role = "#e8e4d8", "complication"
                parts.append(tick)
        elif module_id == "tourbillon":
            cage = M.tube(4.2, 5.0, z, z + 0.4, segments=48)
            cage.name, cage.color, cage.role = "tourbillon_cage", "#d8bd6a", "complication"
            cage.translated(0, -float(spec.get("dial.diameter_mm")) * 0.12, 0)
            parts.append(cage)
        elif module_id == "chronograph":
            for cx, cy in ((-9.5, 4.0), (9.5, 4.0)):
                counter = M.tube(1.7, 2.0, z, z + 0.25, segments=32)
                counter.name, counter.color, counter.role = "chrono_counter", "#e8e4d8", "complication"
                counter.translated(cx, cy, 0)
                parts.append(counter)
        else:
            marker = M.tube(2.0, 2.6, z, z + 0.2, segments=32)
            marker.name, marker.color, marker.role = f"{module_id}_indicator", "#d8bd6a", "complication"
            parts.append(marker)
    return parts


# ---------------------------------------------------------------------------
# Dial and hands
# ---------------------------------------------------------------------------
def dial_parts(spec: Spec, stack: dict) -> list[M.Mesh]:
    r = float(spec.get("dial.diameter_mm")) / 2
    z0 = stack.get("z_dial_top", 6.0) - float(spec.get("dial.thickness_mm"))
    color = dial_color(spec.get("dial.color"))
    parts: list[M.Mesh] = []
    dial = M.disc(r, z0, z0 + float(spec.get("dial.thickness_mm")), segments=96)
    dial.name, dial.color, dial.role, dial.material = "dial", color, "dial", "dial"
    parts.append(dial)

    # rehaut ring
    rehaut = M.tube(r - 0.05, r + 0.35, z0, z0 + 0.55, segments=96)
    rehaut.name, rehaut.color, rehaut.role = "rehaut", "#8f959c", "dial"
    parts.append(rehaut)

    style = spec.get("dial.index_style")
    if style != "no indices":
        count = 12
        for i in range(count):
            a = math.radians(i * 30)
            length = 1.9 if i % 3 == 0 else 1.2
            idx = M.box(-0.22, r * 0.78, z0 + 0.02, 0.22, r * 0.78 + length, z0 + 0.14)
            idx.rotated_z(i * 30).translated(0, 0, 0)
            # rotate about the dial centre, then push the bar to radius
            idx.verts = [(x, y, z_) for x, y, z_ in idx.verts]
            idx.color = "#e9edf2" if style == "applied batons" else "#d8d3c4"
            idx.role, idx.material = "dial", "steel"
            idx.name = "index"
            parts.append(idx)
        # minute track
        for i in range(60):
            if i % 5 == 0:
                continue
            a = math.radians(i * 6)
            tick = M.box(-0.05, r * 0.94, z0 + 0.01, 0.05, r * 0.975, z0 + 0.04)
            tick.rotated_z(i * 6)
            tick.name, tick.color, tick.role = "minute_tick", "#b9b3a4", "dial"
            parts.append(tick)

    if spec.get("dial.date_window"):
        wr = r * 0.62
        frame = M.tube(1.4, 1.75, z0 + 0.16, z0 + 0.22, segments=24)
        frame.name, frame.color, frame.role = "date_frame", "#d8bd6a", "dial"
        frame.translated(wr * 0.72, wr * 0.72, 0)
        parts.append(frame)
        patch = M.box(wr * 0.72 - 1.3, wr * 0.72 - 1.3, z0 + 0.04,
                      wr * 0.72 + 1.3, wr * 0.72 + 1.3, z0 + 0.05)
        patch.color, patch.role = "#12151a", "dial"
        parts.append(patch)

    logo = spec.get("dial.logo_text")
    if logo:
        # simple mark: a thin bar where the signature sits
        mark = M.box(-2.5, r * 0.42, z0 + 0.01, 2.5, r * 0.42 + 0.25, z0 + 0.05)
        mark.color, mark.role = "#e8e4d8", "dial"
        mark.meta = {"text": logo}
        parts.append(mark)
    return parts


def hand_parts(spec: Spec, stack: dict) -> list[M.Mesh]:
    z0 = stack.get("z_hands_top", 7.0) - float(spec.get("hands.stack_height_mm"))
    t = float(spec.get("hands.thickness_mm"))
    style = spec.get("hands.style")
    out = []

    hour_len = float(spec.get("hands.hour_length_mm"))
    minute_len = float(spec.get("hands.minute_length_mm"))
    second_len = float(spec.get("hands.second_length_mm"))

    out.append(_hand("hour_hand", style, hour_len, 3.4, t, z0, -55, "#e9edf2"))
    out.append(_hand("minute_hand", style, minute_len, 2.6, t, z0 + t + 0.08, 60, "#e9edf2"))

    sec = M.rounded_slab(0.55, second_len + 4.0, t * 0.7, 0.28, segments=4, name="second_hand")
    # shift so the tail is behind the centre
    sec.verts = [(x + (second_len - 4.0) / 2, y, z_) for x, y, z_ in sec.verts]
    sec.rotated_z(200).translated(0, 0, z0 + 2 * t + 0.16)
    sec.color, sec.role, sec.material = "#b03a3a", "hand", "steel"
    out.append(sec)
    counter = M.disc(0.9, 0, t * 0.7, segments=20)
    counter.name, counter.color, counter.role = "second_counterweight", "#b03a3a", "hand"
    counter.rotated_z(200).translated(
        -math.cos(math.radians(200)) * (second_len - 4.0) / 2,
        -math.sin(math.radians(200)) * (second_len - 4.0) / 2,
        z0 + 2 * t + 0.16)
    out.append(counter)

    boss = M.tube(0.45, 0.75, z0, z0 + 2 * t + 0.28, segments=20)
    boss.name, boss.color, boss.role = "hand_boss", "#cfd5dc", "hand"
    out.append(boss)
    return out


def _hand(name, style, length, width, thickness, z, angle, color) -> M.Mesh:
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
        body = M.rounded_slab(width, length + tail, thickness, min(width / 2, 0.9),
                              segments=5, name=name)
        body.verts = [(x + (length - tail) / 2, y, z_ + z) for x, y, z_ in body.verts]
        m = body
    m.rotated_z(angle)
    m.color, m.role, m.material = color, "hand", "steel"
    m.meta = {"length_mm": length}
    return m


# ---------------------------------------------------------------------------
def build_scene(spec: Spec) -> dict:
    """Parts plus a tree, mass roll-up and viewer payload."""
    parts = build_parts(spec)
    tree = {}
    masses = {}
    for p in parts:
        role = p.role
        group = {"case": "Case", "dial": "Dial", "hand": "Hands",
                 "movement": "Movement", "complication": "Complications",
                 "crystal": "Crystal", "gasket": "Case"}.get(role, "Other")
        tree.setdefault(group, []).append(p.name)
        density = DENSITY.get(p.material, 7.85)
        masses[p.name] = round(p.volume() * density / 1000.0, 3)
    return {
        "parts": parts,
        "tree": tree,
        "mass_g": round(sum(masses.values()), 2),
        "part_masses_g": masses,
        "triangles": sum(len(p.faces) for p in parts),
        "bounds": _assembly_bounds(parts),
    }


def _assembly_bounds(parts) -> dict:
    if not parts:
        return {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
    mins = [min(p.bounds()["min"][i] for p in parts) for i in range(3)]
    maxs = [max(p.bounds()["max"][i] for p in parts) for i in range(3)]
    return {"min": [round(v, 2) for v in mins], "max": [round(v, 2) for v in maxs],
            "size": [round(maxs[i] - mins[i], 2) for i in range(3)]}
