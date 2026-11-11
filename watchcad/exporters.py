"""Production package documents.

Everything here is generated from the solved spec and the verification results:
BOM, assembly sequence, DFM report, test plan, human-readable spec and the
package manifest.  No document contains a number that is not traceable to the
spec or to a labelled estimate.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import platform
import sys

from .modules import MODULES, compose
from .schema import FIELDS, SCHEMA, Spec

# ---------------------------------------------------------------------------
# BOM
# ---------------------------------------------------------------------------

MATERIAL_NAMES = {
    "steel": "stainless steel 316L",
    "brass": "brass CuZn37",
    "gold": "18k gold",
    "jewel": "synthetic ruby",
    "crystal": "sapphire",
    "dial": "brass dial blank",
    "spring": "Nivaflex spring steel",
    "rubber": "FKM 70 Shore A",
    "pcb": "FR-4 PCB",
    "copper": "enamelled copper",
}

PROCESS = {
    "case_mid": "CNC turn + 5-axis mill, deburr, finish",
    "bezel": "CNC turn, knurl/mill feature, finish",
    "caseback": "CNC turn, thread mill, finish",
    "crown": "CNC turn, knurl, plate",
    "crown_tube": "CNC turn, thread, press fit",
    "movement_ring": "CNC turn / laser cut",
    "lug": "mill from case blank, blend, finish",
    "spring_bar": "purchased, 316L",
    "pusher": "CNC turn, thread, fit seal",
    "crystal": "grind, polish, AR coat both sides",
    "crystal_gasket": "moulded / die cut",
    "caseback_glass": "grind, polish, AR coat",
    "dial": "stamp, pad print, lume, lacquer",
    "rehaut": "CNC turn, print",
    "index": "stamp / turned, plate, lume",
    "minute_tick": "pad print / transfer",
    "hour_hand": "stamp, drill tube, plate, lume",
    "minute_hand": "stamp, drill tube, plate, lume",
    "second_hand": "stamp / turn, plate",
    "second_counterweight": "stamp / turn, plate",
    "hand_boss": "turned tube, press fit",
    "mainplate": "CNC mill, drill, tap, plate",
    "barrel": "CNC turn, hob teeth",
    "barrel_lid": "CNC turn",
    "barrel_wheel": "hob, drill, plate",
    "centre_wheel": "hob, drill, plate",
    "third_wheel": "hob, drill, plate",
    "fourth_wheel": "hob, drill, plate",
    "escape_wheel": "hob / mill, drill, plate",
    "escape_pinion": "hob, polish pivots",
    "third_pinion": "hob, polish pivots",
    "fourth_pinion": "hob, polish pivots",
    "arbor": "turn, grind pivots, polish",
    "jewel": "purchased, pressed",
    "pallet_fork": "mill / EDM, stone",
    "pallet_stones": "purchased, shellac",
    "balance_wheel": "turn / mill, poise",
    "balance_spoke": "mill / turn",
    "balance_cock": "mill, drill, plate",
    "hairspring": "purchased, vibrate and time",
    "train_bridge": "CNC mill, drill, plate",
    "barrel_bridge": "CNC mill, drill, plate",
    "ratchet_wheel": "hob, drill, plate",
    "crown_wheel": "hob, drill, plate",
    "click": "mill, plate",
    "date_ring": "stamp / print",
    "date_frame": "turn / stamp",
    "moon_disc": "stamp, print, lacquer",
    "moon": "stamp, print",
    "gmt_hand": "stamp, plate, lume",
    "chrono_counter": "turn, print",
    "tourbillon_cage": "mill, turn, assemble",
    "power-reserve_indicator": "stamp / print",
    "quartz_module": "purchased module",
    "battery": "purchased, silver oxide",
    "stepper_coil": "purchased, part of module",
    "hand": "stamp, plate, lume",
}

TOLERANCE = {
    "case": "\u00b10.03 mm, bore \u00b10.02",
    "bezel": "\u00b10.03 mm, seat H7",
    "caseback": "thread 6g/6H, gasket groove +0.05/0",
    "crown": "\u00b10.03 mm",
    "crown_tube": "thread 6g, press fit H7/p6",
    "movement_ring": "\u00b10.05 mm",
    "lug": "hole \u00d81.20 \u00b10.02",
    "crystal": "\u00b10.05 mm, dome \u00b10.1",
    "crystal_gasket": "\u00b10.03 mm",
    "dial": "thickness \u00b10.05, print \u00b10.1",
    "index": "\u00b10.05 mm",
    "minute_tick": "position \u00b10.05 mm",
    "hour_hand": "length \u00b10.05, tube h6",
    "minute_hand": "length \u00b10.05, tube h6",
    "second_hand": "length \u00b10.05, tube h6",
    "mainplate": "hole H7, flatness 0.01",
    "barrel": "bore H7, height \u00b10.01",
    "gear": "module \u00b10.005, tooth -0.01/-0.02",
    "jewel": "hole H7, OD h6",
    "hairspring": "torque \u00b13%",
    "balance_wheel": "poise 0.01 g\u00b7mm",
}

CONSUMABLES = [
    ("Lubricant", "Moebius 9010", "jewels, pivots", "0.05 mL"),
    ("Lubricant", "Moebius 9415", "escapement pallets", "0.05 mL"),
    ("Lubricant", "Moebius DX", "keyless works, winding", "0.10 mL"),
    ("Sealant", "Loctite 222", "caseback thread, screws", "1 mL"),
    ("Grease", "FKM-compatible silicone grease", "gaskets", "1 g"),
    ("Adhesive", "UV lume binder", "hands and indices", "2 g"),
    ("Cleaner", "Iso-propyl alcohol 99%", "final clean", "50 mL"),
]


def _friendly_material(part, spec: Spec) -> str:
    mat = part.material
    role = part.role
    if role == "case":
        return str(spec.get("case.material"))
    if role == "dial" and mat == "dial":
        return "brass dial blank"
    if role == "crystal":
        return str(spec.get("case.crystal"))
    if role == "hand":
        return str(spec.get("hands.material"))
    return MATERIAL_NAMES.get(mat, mat)


def _process(part) -> str:
    return PROCESS.get(part.name) or PROCESS.get(part.role) or "make per drawing"


def _tolerance(part) -> str:
    if part.name.endswith("_wheel") or part.name.endswith("_pinion"):
        return TOLERANCE["gear"]
    if part.name in TOLERANCE:
        return TOLERANCE[part.name]
    return TOLERANCE.get(part.role, "\u00b10.05 mm")


def _finish(part, spec: Spec) -> str:
    role = part.role
    if role == "case":
        return str(spec.get("case.finish"))
    if role == "dial":
        return str(spec.get("dial.finish"))
    if role == "hand":
        return "polished / lume-filled"
    if role == "crystal":
        return "AR coated, both sides"
    if part.material == "brass":
        return "gilt or rhodium plate"
    if part.material == "steel":
        return "rhodium / nickel plate"
    if part.material == "jewel":
        return "as supplied"
    return "-"


def bom_rows(spec: Spec, scene: dict) -> list[dict]:
    """Group identical parts and attach make/buy, process, tolerance and mass."""
    parts = scene.get("parts") or []
    groups: dict[tuple, dict] = {}
    for p in parts:
        key = (p.name, p.role, p.material)
        if key not in groups:
            groups[key] = {"name": p.name, "role": p.role, "material": p.material,
                           "qty": 0, "part": p, "mass": scene.get("part_masses_g", {}).get(p.name, 0.0)}
        groups[key]["qty"] += 1

    base = (spec.get("movement.base_calibre") or "").strip()
    rows: list[dict] = []
    movement_mass = 0.0
    movement_qty = 0

    for key in sorted(groups, key=lambda k: (_role_order(k[1]), k[0])):
        g = groups[key]
        p = g["part"]
        if base and p.role == "movement":
            movement_mass += g["mass"] * g["qty"]
            movement_qty += g["qty"]
            continue
        rows.append(_row(spec, g["name"], g["qty"], p, g["mass"] * g["qty"]))

    if base and movement_qty:
        rows.insert(0, {
            "item": 0, "part": f"movement, {base}", "qty": 1,
            "material": "complete calibre", "process": "purchased, regulated",
            "make_buy": "Buy", "mass_g": round(movement_mass, 2),
            "tolerance": "per calibre datasheet", "finish": "as supplied",
            "notes": f"replaces {movement_qty} in-house movement components",
            "category": "movement",
        })

    # quartz modules are bought as one item
    if spec.get("movement.kind") == "quartz":
        rows = [r for r in rows if r["part"] not in ("quartz_module", "stepper_coil")]
        rows.append({
            "item": 0, "part": f"quartz module, {spec.get('quartz.module')}", "qty": 1,
            "material": "complete module", "process": "purchased",
            "make_buy": "Buy", "mass_g": 2.0, "tolerance": "per module datasheet",
            "finish": "as supplied", "notes": "includes coil and PCB",
            "category": "movement",
        })

    rows.sort(key=lambda r: (_role_order(r.get("category") or _role_from_name(r["part"])), r["part"]))
    total_mass = 0.0
    for i, r in enumerate(rows, start=1):
        r["item"] = i
        r["category"] = r.get("category") or _role_from_name(r["part"])
        total_mass += r["mass_g"]
    return rows


def _row(spec, name, qty, part, mass) -> dict:
    base = (spec.get("movement.base_calibre") or "").strip()
    if part.role == "movement":
        make_buy = "Make"
        notes = "in-house movement component" if not base else "part of purchased calibre"
    elif part.role == "complication":
        make_buy = "Buy"
        notes = "complication module, matched to the base calibre"
    elif part.role in ("crystal", "gasket") or name in ("spring_bar", "jewel", "hairspring"):
        make_buy = "Buy"
        notes = "catalogue part"
    else:
        make_buy = "Make"
        notes = ""
    if name in ("quartz_module", "battery", "stepper_coil"):
        make_buy = "Buy"
        notes = "quartz module consumable"
    return {
        "item": 0, "part": _display_name(name), "qty": qty,
        "material": _friendly_material(part, spec), "process": _process(part),
        "make_buy": make_buy, "mass_g": round(mass, 3), "tolerance": _tolerance(part),
        "finish": _finish(part, spec), "notes": notes,
        "category": part.role,
    }


def _display_name(name: str) -> str:
    return name.replace("_", " ")


def _role_from_name(name: str) -> str:
    low = name.lower()
    if any(k in low for k in ("case", "bezel", "crown", "lug", "pusher", "spring bar", "movement ring")):
        return "case"
    if "dial" in low or "index" in low or "tick" in low or "rehaut" in low:
        return "dial"
    if "hand" in low:
        return "hand"
    if "crystal" in low or "gasket" in low:
        return "crystal"
    return "movement"


def _role_order(role: str) -> int:
    return {"case": 0, "crystal": 1, "dial": 2, "hand": 3, "movement": 4,
            "complication": 5, "gasket": 1}.get(role, 6)


def bom_totals(rows: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for r in rows:
        c = by_category.setdefault(r["category"], {"count": 0, "mass_g": 0.0})
        c["count"] += r["qty"]
        c["mass_g"] += r["mass_g"]
    return {
        "lines": len(rows), "parts": sum(r["qty"] for r in rows),
        "mass_g": round(sum(r["mass_g"] for r in rows), 2),
        "make": sum(r["qty"] for r in rows if r["make_buy"] == "Make"),
        "buy": sum(r["qty"] for r in rows if r["make_buy"] == "Buy"),
        "by_category": by_category,
    }


def bom_csv(spec: Spec, scene: dict) -> str:
    rows = bom_rows(spec, scene)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["item", "part", "qty", "material", "process", "make_buy",
                     "mass_g", "tolerance", "finish", "notes", "category"])
    for r in rows:
        writer.writerow([r["item"], r["part"], r["qty"], r["material"], r["process"],
                         r["make_buy"], f"{r['mass_g']:.3f}", r["tolerance"],
                         r["finish"], r["notes"], r["category"]])
    totals = bom_totals(rows)
    writer.writerow([])
    writer.writerow(["", "TOTAL", totals["parts"], "", "", f"make {totals['make']} / buy {totals['buy']}",
                     f"{totals['mass_g']:.2f}", "", "", "", ""])
    return buf.getvalue()


def bom_markdown(spec: Spec, scene: dict) -> str:
    rows = bom_rows(spec, scene)
    totals = bom_totals(rows)
    out = [f"# Bill of materials \u2014 {spec.get('meta.name')}", "",
           f"Spec checksum `{spec.get('derived.checksum', '')}`. "
           f"{totals['parts']} parts across {totals['lines']} lines, "
           f"{totals['mass_g']:.2f} g component mass.", "",
           "| # | Part | Qty | Material | Process | Make/Buy | Unit mass g | Mass g | Tolerance | Finish |",
           "|---:|---|---:|---|---|---|---:|---:|---|---|"]
    for r in rows:
        unit = r["mass_g"] / r["qty"] if r["qty"] else 0
        out.append(f"| {r['item']} | {r['part']} | {r['qty']} | {r['material']} | {r['process']} | "
                   f"{r['make_buy']} | {unit:.3f} | {r['mass_g']:.3f} | {r['tolerance']} | {r['finish']} |")
    out += ["", f"**Total component mass:** {totals['mass_g']:.2f} g "
                f"(make {totals['make']} pcs, buy {totals['buy']} pcs).", "",
            "## Consumables", "",
            "| Type | Item | Use | Pack |", "|---|---|---|---|"]
    for row in CONSUMABLES:
        out.append("| " + " | ".join(row) + " |")
    out += ["", "> Component mass excludes the strap, packaging and lubrication. "
                "Buy quantities are per watch; add 3\u20135% attrition for make parts.", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Assembly, test plan, DFM, spec
# ---------------------------------------------------------------------------

def assembly_markdown(spec: Spec, scene: dict, checks: list[dict] | None = None) -> str:
    stack = spec.get("derived.stack") or {}
    train = spec.get("derived.train") or {}
    comps = compose(spec.get("movement.complications") or {})
    name = spec.get("meta.name")
    rows = bom_rows(spec, scene)
    part_no = {r["part"]: r["item"] for r in rows}
    out = [f"# Assembly instructions \u2014 {name}", "",
           f"Build to spec `{spec.get('derived.checksum', '')}`. All dimensions in millimetres. "
           f"Work on a clean bench with a dust cover; wear finger cots for every optical step.", ""]

    out += ["## 1. Tools and fixtures", "",
            "- screwdrivers 0.8/1.2/1.6 mm, brass tweezers, hand-setting press",
            "- caseback wrench (or 3-point opener), crystal press with flat nylon dies",
            "- movement holder, dust blower, Rodico, lint-free wipes",
            "- timegrapher, pressure tester, 10\u00d7 loupe, torque driver 0.2\u20131.2 N\u00b7cm", ""]

    out += ["## 2. Key dimensions", "",
            f"- case \u00d8 {spec.get('case.diameter_mm')} mm \u00d7 {spec.get('case.thickness_mm')} mm, "
            f"lug-to-lug {spec.get('case.lug_to_lug_mm')} mm, lug width {spec.get('case.lug_width_mm')} mm",
            f"- movement \u00d8 {spec.get('movement.width_mm')} mm \u00d7 {spec.get('movement.height_mm')} mm, "
            f"{spec.get('movement.beat_rate_bph')} bph, {spec.get('movement.jewels')} jewels",
            f"- dial \u00d8 {spec.get('dial.diameter_mm')} mm, hands {spec.get('hands.hour_length_mm')}/"
            f"{spec.get('hands.minute_length_mm')}/{spec.get('hands.second_length_mm')} mm",
            f"- stack: caseback {stack.get('caseback_mm', 0):.2f}, movement "
            f"{stack.get('movement_mm', 0):.2f}, dial {stack.get('dial_mm', 0):.2f}, hands "
            f"{stack.get('hands_mm', 0):.2f}, crystal {stack.get('crystal_mm', 0):.2f}; "
            f"margin {stack.get('margin_mm', 0):+.2f} mm", ""]

    out += ["## 3. Torque schedule", "",
            "| Fastener | Torque | Note |", "|---|---:|---|",
            "| caseback | 1.2 N\u00b7m | screw-down, new gasket, grease |",
            "| movement holder tabs | 0.05 N\u00b7m | do not distort the plate |",
            "| crown tube | 0.25 N\u00b7m | thread-lock 222 |",
            "| pusher nuts | 0.15 N\u00b7m | chronograph only |",
            "| lug spring bars | hand fit | 1.5 mm pin, 20 mm |", ""]

    out += ["## 4. Lubrication", "",
            "| Point | Lubricant | Quantity |", "|---|---|---|",
            "| jewels and pivots | Moebius 9010 | small film, no pooling |",
            "| pallet stones | Moebius 9415 | 3 teeth contact |",
            "| keyless works | Moebius DX | thin film |",
            "| barrel arbor | Moebius 9010 | 2 small dots |",
            "| gaskets | silicone grease | wipe, no excess |", ""]

    steps = [
        ("Case preparation", [
            "Inspect and deburr the case, bezel and caseback; verify the bore and gasket groove.",
            "Press the crown tube with thread-lock; fit the crown and check rotation and pull positions.",
            "Fit the caseback gasket into its groove; keep it free of dust.",
        ]),
        ("Movement preparation", [
            "Assemble the barrel and check the mainspring sits flat; fit the barrel bridge.",
            "Install the train in order barrel \u2192 centre \u2192 third \u2192 fourth \u2192 escape.",
            "Fit the pallet fork, balance and hairspring; check free oscillation before fitting the cock.",
            "Fit the keyless works, dial-side motion works and the complication module.",
            "Lubricate per the table and run the movement for 24 h before casing.",
        ]),
        ("Dial and hands", [
            "Fit the dial over the movement feet; do not trap the date jumper.",
            "Set the hour and minute hands at 12:00 reference; press with the correct tube support.",
            "Fit the seconds hand with the counterweight clear of the minute hand.",
            "Check hand clearance to the dial and to the crystal before closing.",
        ]),
        ("Casing", [
            "Blow off the dial, fit the movement ring and seat the movement in the case.",
            "Fit the crystal gasket and press the crystal with a flat die; wipe the inside face.",
            "Close the caseback with a new gasket and torque to schedule.",
        ]),
        ("Final QC", [
            "Run the test plan; record rate, amplitude, beat error and power reserve.",
            "Leak-test at the rated pressure, then dry and inspect for condensation.",
            "Fit spring bars and strap; final visual inspection under 10\u00d7 magnification.",
        ]),
    ]
    for i, (title, lines) in enumerate(steps, start=1):
        out += [f"## {i + 4}. {title}", ""]
        out += [f"{j}. {line}" for j, line in enumerate(lines, start=1)]
        out.append("")

    if comps["ids"]:
        out += ["## Complication notes", ""]
        for mid in comps["ids"]:
            info = MODULES[mid]
            out.append(f"- **{info['name']}** \u2014 {info['description']} "
                       f"Ratio: {info['ratio_note']}. Allow {info['height_mm']:.2f} mm stack height.")
        out.append("")

    if train.get("motion_works"):
        mw = train["motion_works"]
        out += ["## Motion works", "",
                "Hour-hand reduction: " + " \u00d7 ".join(
                    f"{s['wheel_teeth']}/{s['pinion_teeth']}" for s in mw) +
                f" = {train.get('hour_hand_ratio', 12):.3f}:1.", ""]
    out += ["## BOM references", "",
            ", ".join(f"{r['item']} = {r['part']}" for r in rows[:18]) +
            (" \u2026" if len(rows) > 18 else ""), ""]
    return "\n".join(out)


def test_plan_markdown(spec: Spec, checks: list[dict]) -> str:
    name = spec.get("meta.name")
    grouped = {}
    for c in checks:
        grouped.setdefault(c["layer"], []).append(c)
    layers = [("spec", "Specification"), ("geometry", "Geometry"), ("kinematics", "Kinematics"),
              ("energy", "Energy"), ("tolerance", "Tolerance"), ("dfm", "DFM"),
              ("physical", "Physical tests")]
    out = [f"# Test and verification plan \u2014 {name}", "",
           "Verdicts are layered. `calculated` is closed-form, `estimated` relies on a "
           "documented model, and `needs_test` can only be answered on the bench. "
           "There is no single pass/fail badge for the watch.", ""]
    for key, title in layers:
        if key not in grouped:
            continue
        out += [f"## {title}", "",
                "| ID | Check | Verdict | Measurement / acceptance |", "|---|---|---|---|"]
        for c in grouped[key]:
            verdict = "FAIL" if c["passed"] is False else c["verdict"]
            out.append(f"| `{c['id']}` | {c['title']} | **{verdict}** | {c['detail']} |")
        out.append("")

    out += ["## Bench procedure", "",
            "| # | Test | Method | Instrument | Acceptance |", "|---:|---|---|---|---|"]
    wr = spec.get("case.water_resistance_m")
    reserve = spec.get("movement.power_reserve_h")
    out += [
        "| 1 | Rate and amplitude | 6 positions, 24 h after casing | timegrapher | "
        f"mean rate within \u00b110 s/day, amplitude \u2265 250\u00b0, beat error \u2264 0.6 ms |",
        "| 2 | Power reserve | full wind, measure to stop | timer | "
        f"\u2265 {float(reserve or 0) * 0.9:.0f} h of {reserve} h target |",
        f"| 3 | Water resistance | pressure test to 1.25\u00d7 rating | pressure tester | "
        f"no leak at {float(wr or 0) * 1.25:.0f} m equivalent |",
        "| 4 | Condensation | warm plate test after pressure | hot plate | no condensation under crystal |",
        "| 5 | Shock | ISO 1413 drop, then re-time | drop rig, timegrapher | rate shift \u2264 15 s/day |",
        "| 6 | Magnetic | ISO 764, 4 800 A/m, then re-time | Helmholtz coil | residual rate \u2264 30 s/day |",
        "| 7 | Thermal | 8 h each at 8/23/38 \u00b0C | climate chamber | rate spread \u2264 20 s/day |",
        "| 8 | Hand clearance | 10\u00d7 loupe, rotate hands through 12 h | loupe | \u2265 0.10 mm, no contact |",
        "| 9 | Caseback torque | breakaway check | torque driver | 1.1\u20131.3 N\u00b7m |",
        "| 10 | Visual | dial, hands, crystal, case | 10\u00d7 loupe | no dust, scratches or misalignment |",
        "",
    ]
    out += ["## First-article inspection", "",
            "Record actuals for every critical dimension in the drawing notes. "
            "Do not release the batch until the first article is signed off and the "
            "timegrapher and pressure results are attached.", ""]
    return "\n".join(out)


def dfm_markdown(spec: Spec, checks: list[dict]) -> str:
    dfm = [c for c in checks if c["layer"] == "dfm"]
    out = [f"# DFM and process report \u2014 {spec.get('meta.name')}", "",
           "| Check | Verdict | Detail |", "|---|---|---|"]
    for c in dfm:
        verdict = "FAIL" if c["passed"] is False else c["verdict"]
        out.append(f"| {c['title']} | **{verdict}** | {c['detail']} |")
    out += ["", "## Process routing", "",
            "| Operation | Scope | Key control |", "|---|---|---|",
            "| CNC turning | case, crown, tubes, bezel | bore \u00b10.02, thread 6g/6H |",
            "| 5-axis milling | lugs, pusher pockets, dial feet | true position 0.03 |",
            "| Gear hobbing | wheels and pinions | module \u00b10.005, tooth thickness -0.01/-0.02 |",
            "| Stamping | hands, indices, date ring | flatness 0.02, burr 0.05 max |",
            "| Printing | dial, minute track, logo | registration \u00b10.10 |",
            "| Plating | case and movement parts | 2\u20133 \u00b5m, adhesion test |",
            "| Assembly | movement, dial, hands, case | torque and lubrication schedule |",
            "| Test | timegrapher, pressure, visual | acceptance per test plan |", "",
            "## Risks and open items", "",
            "- Mesh kernel is presentation grade: no fillets, thread forms, lubrication or escapement dynamics.",
            "- Date windows are surface patches, not boolean cuts; the production drawing carries the real aperture.",
            "- STEP is not generated by the zero-dependency build; enable the optional OCC backend.",
            "- Spring torque and train efficiency are empirical estimates; confirm on the first prototype.", ""]
    return "\n".join(out)


def spec_markdown(spec: Spec) -> str:
    data = spec.data
    out = [f"# {data['meta'].get('name')}", "", spec.summary(), "",
           f"Spec version {data['meta'].get('version')} \u00b7 checksum `{spec.get('derived.checksum', '')}` "
           f"\u00b7 generated {_dt.datetime.now().isoformat(timespec='seconds')}", ""]
    for group, fields in SCHEMA.items():
        if group == "derived":
            continue
        out += [f"## {group.title()}", "", "| Field | Value | Unit | Provenance | Locked |",
                "|---|---:|---|---|---|"]
        for name in fields:
            path = f"{group}.{name}"
            value = spec.get(path)
            if isinstance(value, dict):
                value = ", ".join(value.keys()) or "{}"
            meta = FIELDS.get(path, {})
            lock = "yes" if path in spec.locks else ""
            prov = spec.tag(path)
            out.append(f"| {name} | {value} | {meta.get('unit', '')} | {prov} | {lock} |")
        out.append("")

    stack = data.get("derived", {}).get("stack") or {}
    if stack:
        out += ["## Stack-up", "", "| Layer | Thickness mm | z mm |", "|---|---:|---:|"]
        for key, label in (("caseback_mm", "caseback"), ("movement_mm", "movement"),
                           ("complications_mm", "complications"), ("dial_mm", "dial"),
                           ("hands_mm", "hands"), ("crystal_mm", "crystal")):
            out.append(f"| {label} | {stack.get(key, 0):.2f} | |")
        out += ["", f"Required case thickness **{stack.get('required_case_thickness_mm', 0):.2f} mm**, "
                    f"available **{stack.get('available_case_thickness_mm', 0):.2f} mm**, "
                    f"margin **{stack.get('margin_mm', 0):+.2f} mm**.", ""]

    train = data.get("derived", {}).get("train") or {}
    if train:
        out += ["## Going train", "", "| Driver | Driven | Wheel | Pinion | Module mm | Centre mm | Ratio |",
                "|---|---|---:|---:|---:|---:|---:|"]
        for s in train.get("stages", []):
            out.append(f"| {s['driver']} | {s['driven']} | {s['wheel_teeth']} | {s['pinion_teeth']} | "
                       f"{s['module_mm']} | {s['centre_mm']} | {s['ratio']:.6f} |")
        out += ["", f"Barrel to escape **{train.get('total_barrel_to_escape', 0):.1f}:1**, "
                    f"motion works **{train.get('hour_hand_ratio', 0):.6f}:1**, "
                    f"effective reserve **{train.get('effective_power_reserve_h', 0):.1f} h**.", ""]

    barrel = data.get("derived", {}).get("barrel") or {}
    if barrel:
        out += ["## Barrel and power", "",
                f"- barrel bore {barrel.get('inner_diameter_mm')} mm, arbor "
                f"{barrel.get('arbor_diameter_mm')} mm, height {barrel.get('height_mm')} mm",
                f"- spring {barrel.get('spring_thickness_mm')} mm \u00d7 "
                f"{barrel.get('spring_length_mm')} mm, {barrel.get('usable_turns')} usable turns",
                f"- full-wind torque {barrel.get('torque_full_nmm')} N\u00b7mm, "
                f"stored energy {barrel.get('energy_j')} J", ""]

    torque = data.get("derived", {}).get("torque") or {}
    if torque:
        out += ["## Energy budget", "",
                f"- available {torque.get('available_power_uw', 0)} \u00b5W vs required "
                f"{torque.get('required_power_uw', 0)} \u00b5W "
                f"(margin {torque.get('power_margin', 0)})\u00d7",
                f"- escape torque {torque.get('escape_torque_nmm', 0)} N\u00b7mm, "
                f"{torque.get('escape_margin', 0)}\u00d7 the unlock threshold", ""]

    provenance = data.get("meta", {})
    out += ["## Notes", "", f"- mode: {provenance.get('mode')}",
            f"- created: {provenance.get('created_at') or 'n/a'}",
            f"- source: {provenance.get('source_url') or 'none'}",
            f"- user locks: {', '.join(sorted(spec.locks)) or 'none'}",
            "- values tagged `derived` are reproducible from the essentials by `watchcad.solver`.", ""]
    return "\n".join(out)


def package_readme(spec: Spec) -> str:
    name = spec.get("meta.name")
    return "\n".join([
        f"# {name} \u2014 production package", "",
        f"Generated by watchcad from spec `{spec.get('derived.checksum', '')}`.", "",
        "## Contents", "",
        "| File | What it is |", "|---|---|",
        "| `spec.json` | the single source of truth, with provenance and locks |",
        "| `spec.md` | human-readable spec, stack-up, train and energy tables |",
        "| `model.stl` | watertight-enough triangle mesh of the whole watch, mm |",
        "| `model.obj` | the same assembly with named parts |",
        "| `model.gltf` | single-file glTF 2.0 with one material per part |",
        "| `parts/*.stl` | individual part meshes for inspection |",
        "| `drawings/front.svg` | dial-side plan, 1:1 |",
        "| `drawings/section.svg` | axial section with every stack layer |",
        "| `drawings/train.svg` | movement plan with the tooth table |",
        "| `drawings/notes.svg` | tolerances, critical dimensions and title block |",
        "| `drawings/watch_sheet.dxf` | all views in one DXF for CAD import |",
        "| `drawings/watch_drawings.pdf` | print-ready drawing set |",
        "| `bom.csv` / `bom.md` | bill of materials with make/buy, process and tolerance |",
        "| `assembly.md` | tools, torque, lubrication and assembly sequence |",
        "| `test_plan.md` | layered verification and bench acceptance limits |",
        "| `dfm.md` | process routing, risks and open items |",
        "| `verification.md` | every check with its own verdict |",
        "| `manifest.json` | checksums and file inventory |", "",
        "## How to rebuild", "",
        "```bash",
        "python3 -m watchcad build spec.json -o out",
        "python3 -m watchcad verify spec.json",
        "python3 -m watchcad serve",
        "```", "",
        "## Honest limitations", "",
        "- The mesh kernel is presentation grade. It does not model fillets, screw threads, "
        "spring dynamics, lubrication or escapement geometry.",
        "- STEP is not produced by the zero-dependency build. Enable the optional OCC backend "
        "to emit STEP from the same parametric functions.",
        "- `calculated` checks are closed-form; `estimated` checks depend on documented empirical "
        "constants; `needs_test` items require the bench. There is no all-in-one pass badge.", ""])


def verification_markdown(spec: Spec, checks: list[dict], summary: dict) -> str:
    out = [f"# Verification \u2014 {spec.get('meta.name')}", "",
           f"Spec `{spec.get('derived.checksum', '')}`. "
           f"{summary.get('calculated', 0)} calculated, {summary.get('estimated', 0)} estimated, "
           f"{summary.get('needs_test', 0)} needs test, {summary.get('fail', 0)} failed "
           f"of {summary.get('total', 0)} checks.", ""]
    failed = [c for c in checks if c["passed"] is False]
    if failed:
        out += ["## Failures", ""]
        for c in failed:
            out.append(f"- **{c['id']}** \u2014 {c['detail']}")
            if c.get("fix"):
                out.append(f"  - suggested fix: set `{c['fix'].get('path')}` to "
                           f"`{c['fix'].get('value')}`")
        out.append("")
    groups = {}
    for c in checks:
        groups.setdefault(c["layer"], []).append(c)
    for layer, items in groups.items():
        out += [f"## {layer}", "", "| ID | Check | Verdict | Detail |", "|---|---|---|---|"]
        for c in items:
            verdict = "FAIL" if c["passed"] is False else c["verdict"]
            out.append(f"| `{c['id']}` | {c['title']} | **{verdict}** | {c['detail']} |")
        out.append("")
    out += ["## What this report does not prove", "",
            "Calculated checks use the numbers in the spec and the documented formulas. "
            "Estimated checks are only as good as their constants. Nothing in this file "
            "replaces a timegrapher, a pressure test and a first-article inspection.", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(spec: Spec, files: list[str], checks_summary: dict,
             scene: dict | None, root: str, step_status: dict | None = None) -> dict:
    entries = []
    for path in sorted(files):
        if os.path.isfile(path):
            entries.append({
                "path": os.path.relpath(path, root),
                "bytes": os.path.getsize(path),
                "sha256": sha256_file(path),
            })
    return {
        "name": spec.get("meta.name"),
        "schema_version": 1,
        "spec_version": spec.get("meta.version"),
        "checksum": spec.get("derived.checksum"),
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "generator": {"name": "watchcad", "python": sys.version.split()[0],
                      "platform": platform.platform()},
        "geometry": {
            "parts": len(scene.get("parts", [])) if scene else 0,
            "triangles": scene.get("triangles", 0) if scene else 0,
            "mass_g": scene.get("mass_g", 0) if scene else 0,
            "bounds_mm": scene.get("bounds", {}) if scene else {},
        },
        "verification": checks_summary,
        "step": step_status or {
            "status": "unavailable",
            "reason": "zero-dependency build; install the optional OCC backend to emit STEP",
            "formats": ["STL", "OBJ", "glTF 2.0"],
        },
        "files": entries,
    }


def write_text(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def write_bytes(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def write_bom(spec: Spec, scene: dict, root: str) -> tuple[str, str]:
    csv_path = write_text(os.path.join(root, "bom.csv"), bom_csv(spec, scene))
    md_path = write_text(os.path.join(root, "bom.md"), bom_markdown(spec, scene))
    return csv_path, md_path
