"""Complication and quartz-module library.

A complication is a plug-in.  It declares what it costs (torque draw, stacked
height, dial real estate), what it needs from the base movement, and which other
modules it conflicts with.  Geometry builders live in parts.py, verification
hooks in verify.py; this file is the single registry both read.

Numbers are engineering estimates, not manufacturer data.  They are labelled
`estimated` in verification output.
"""

from __future__ import annotations

MODULES: dict[str, dict] = {
    "date": dict(
        name="Date", torque_uW=0.03, height_mm=0.45, envelope_r=0.34,
        needs="hour_wheel", dial_opening=True, conflicts=[],
        ratio_note="1 step / 24 h from the hour wheel",
        description="Date ring with jumper and corrector.",
    ),
    "day-date": dict(
        name="Day-Date", torque_uW=0.05, height_mm=0.55, envelope_r=0.40,
        needs="hour_wheel", dial_opening=True, conflicts=[],
        ratio_note="two rings, 1 step / 24 h",
        description="Day and date rings driven from the hour wheel.",
    ),
    "power-reserve": dict(
        name="Power reserve", torque_uW=0.02, height_mm=0.50, envelope_r=0.55,
        needs="barrel", dial_opening=True, conflicts=[],
        ratio_note="differential from barrel arbor",
        description="Differential and sector driven by the barrel arbor.",
    ),
    "gmt": dict(
        name="GMT", torque_uW=0.04, height_mm=0.55, envelope_r=0.30,
        needs="hour_wheel", dial_opening=False, conflicts=[],
        ratio_note="12 h hand on its own wheel, 1:1",
        description="Additional 12-hour hand with a second time zone.",
    ),
    "moonphase": dict(
        name="Moon phase", torque_uW=0.03, height_mm=0.60, envelope_r=0.50,
        needs="hour_wheel", dial_opening=True, conflicts=[],
        ratio_note="59:1 from the hour wheel (2 lunations / 59 days)",
        description="Moon disc with 59-tooth wheel and finger piece.",
    ),
    "chronograph": dict(
        name="Chronograph", torque_uW=0.60, height_mm=1.60, envelope_r=0.90,
        needs="fourth_wheel", dial_opening=True, conflicts=["gmt"],
        ratio_note="seconds coupling from the fourth wheel, 1:1",
        description="Column wheel, horizontal clutch, two pushers.",
    ),
    "annual-calendar": dict(
        name="Annual calendar", torque_uW=0.10, height_mm=1.10, envelope_r=0.80,
        needs="hour_wheel", dial_opening=True, conflicts=["chronograph"],
        ratio_note="month cam with 30/31 day switching",
        description="Month cam corrects the 30-day months once a year.",
    ),
    "perpetual-calendar": dict(
        name="Perpetual calendar", torque_uW=0.20, height_mm=1.80, envelope_r=0.90,
        needs="hour_wheel", dial_opening=True, conflicts=["chronograph"],
        ratio_note="48-month cam plus leap-year wheel",
        description="Perpetual calendar with leap-year indication.",
    ),
    "tourbillon": dict(
        name="Tourbillon", torque_uW=0.40, height_mm=2.40, envelope_r=0.55,
        needs="fourth_wheel", dial_opening=True, conflicts=["chronograph", "perpetual-calendar"],
        ratio_note="carriage 1 rev / min, escapement inside",
        description="Rotating carriage carrying the escapement.",
    ),
    "minute-repeater": dict(
        name="Minute repeater", torque_uW=1.20, height_mm=3.20, envelope_r=0.95,
        needs="hour_wheel", dial_opening=False, conflicts=["chronograph", "tourbillon"],
        ratio_note="rack and snail, hour/quarter/minute cams",
        description="Chiming mechanism on racks and snails.",
    ),
}

# Base draw of a mechanical movement excluding complications (estimated).
BASE_DRAW_UW = 1.00
# Unlocking torque the escapement needs at the escape wheel (estimated).
UNLOCK_TORQUE_NMM = 0.0003

COMPLICATIONS = MODULES  # schema alias

QUARTZ_MODULES = {
    "generic 3-hand": dict(width_mm=26.0, height_mm=2.6, current_uA=1.2, jewels=0),
    "ronda 515": dict(width_mm=25.6, height_mm=2.5, current_uA=1.1, jewels=0),
    "miyota 2035": dict(width_mm=26.0, height_mm=3.15, current_uA=1.2, jewels=0),
    "ronda 763": dict(width_mm=23.3, height_mm=3.0, current_uA=1.5, jewels=0),
}


def get_module(module_id: str) -> dict:
    return MODULES[module_id]


def compose(module_ids) -> dict:
    """Check a set of complications for conflicts, height and torque draw."""
    ids = [m for m in module_ids if m in MODULES]
    unknown = [m for m in module_ids if m not in MODULES]
    conflicts = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if b in MODULES[a]["conflicts"] or a in MODULES[b]["conflicts"]:
                conflicts.append([a, b])
    return {
        "ids": ids,
        "unknown": unknown,
        "conflicts": conflicts,
        "height_mm": round(sum(MODULES[m]["height_mm"] for m in ids), 2),
        "draw_uW": round(sum(MODULES[m]["torque_uW"] for m in ids), 3),
        "dial_openings": [m for m in ids if MODULES[m]["dial_opening"]],
        "needs": sorted({MODULES[m]["needs"] for m in ids}),
    }
