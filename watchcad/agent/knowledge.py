"""Small, honest reference knowledge base.

This is deliberately a curated table rather than an internet lookup.  Every
entry says whether its numbers are published reference data, a standard, or an
engineering estimate.  The agent can cite the entries but must not present an
estimate as a measured specification.

No third-party packages and no network access are required.
"""

from __future__ import annotations

import re
from typing import Iterable


def _entry(category: str, name: str, status: str, summary: str,
           data: dict | None = None, aliases: Iterable[str] = ()) -> dict:
    return {"category": category, "name": name, "status": status,
            "summary": summary, "data": data or {},
            "aliases": [a for a in aliases if a]}


KNOWLEDGE: list[dict] = [
    # -- case materials ---------------------------------------------------
    _entry("material", "316L stainless steel", "reference",
           "Austenitic stainless steel with good corrosion resistance and "
           "excellent machinability. Density about 8.0 g/cm3; the usual case "
           "alloy for series production.",
           {"density_g_cm3": 8.0, "hardness_hv": 200, "finish": ["brushed", "polished", "bead-blasted"]},
           ["steel", "stainless", "316l"]),
    _entry("material", "titanium (grade 5)", "reference",
           "Ti-6Al-4V: about 43% lighter than steel, hypoallergenic and tougher "
           "to finish. Density about 4.5 g/cm3.",
           {"density_g_cm3": 4.43, "hardness_hv": 350, "finish": ["brushed", "bead-blasted"]},
           ["titanium", "ti"]),
    _entry("material", "bronze (CuSn8)", "reference",
           "Phosphor bronze develops a patina and is softer than steel. Density "
           "about 8.8 g/cm3; use a coated caseback if skin contact matters.",
           {"density_g_cm3": 8.8, "hardness_hv": 130, "finish": ["brushed", "polished"]},
           ["bronze", "cuSn8"]),
    _entry("material", "zirconia ceramic", "reference",
           "Scratch-resistant and electrically insulating. Density about 6.0 "
           "g/cm3. Brittle under point impact; case construction needs care.",
           {"density_g_cm3": 6.05, "hardness_hv": 1200, "finish": ["polished", "sandblasted"]},
           ["ceramic", "zirconia"]),
    _entry("material", "forged carbon composite", "estimate",
           "Light and visually directional. Properties vary by layup; treat "
           "strength and density as supplier data, not a catalogue constant.",
           {"density_g_cm3": 1.6, "finish": ["matte", "polished"]},
           ["carbon", "forged carbon"]),

    # -- crystals ---------------------------------------------------------
    _entry("crystal", "sapphire crystal", "reference",
           "Synthetic corundum, Mohs 9, usually AR-coated on the inside. The "
           "standard choice above 100 m water resistance.",
           {"mohs": 9, "density_g_cm3": 3.98, "typical_thickness_mm": 1.15},
           ["sapphire"]),
    _entry("crystal", "mineral glass", "reference",
           "Hardened mineral glass, Mohs about 5.5. Cheaper and easier to "
           "shape than sapphire; scratches more readily.",
           {"mohs": 5.5, "density_g_cm3": 2.5, "typical_thickness_mm": 1.0},
           ["mineral", "glass"]),
    _entry("crystal", "acrylic crystal", "reference",
           "PMMA, Mohs about 3. Light, warm appearance and polishable, but "
           "easily scratched. Common on vintage and field watches.",
           {"mohs": 3, "density_g_cm3": 1.18, "typical_thickness_mm": 1.2},
           ["acrylic", "plexiglass", "pmma"]),

    # -- movements --------------------------------------------------------
    _entry("movement", "generic automatic three-hander", "estimate",
           "A 28 mm class automatic movement with roughly 40 h reserve, "
           "28 800 bph and 21 jewels. This is a modelling envelope, not a "
           "manufacturer datasheet.",
           {"width_mm": 28.0, "height_mm": 4.6, "power_reserve_h": 42,
            "beat_rate_bph": 28800, "jewels": 21},
           ["automatic", "three hander", "three-hander"]),
    _entry("movement", "high-beat movement", "estimate",
           "36 000 bph (5 Hz) improves rate stability at the cost of power. "
           "Expect a shorter service interval and a larger mainspring draw.",
           {"beat_rate_bph": 36000, "power_reserve_h": 50},
           ["high beat", "high-beat", "36000"]),
    _entry("movement", "standard quartz module", "estimate",
           "A 1.5 V stepping module drawing about 1 uA, with a 32 mAh cell "
           "giving several years of life. Module dimensions vary widely.",
           {"current_uA": 1.2, "battery_mAh": 32, "step_hz": 1.0},
           ["quartz"]),

    # -- standards and process -------------------------------------------
    _entry("standard", "ISO 22810 water resistance", "standard",
           "Defines water-resistance marking and test pressure. A 300 m mark "
           "is tested at 30 bar; it is not a dive-depth rating.",
           {"test_pressure_bar_per_100m": 10},
           ["water resistance", "iso 22810", "300m", "300 m"]),
    _entry("standard", "ISO 764 magnetic resistance", "standard",
           "A watch marked antimagnetic must keep its rate after exposure to "
           "4 800 A/m. Higher fields need a soft-iron cage or silicon parts.",
           {"field_a_per_m": 4800},
           ["magnetic", "antimagnetic", "iso 764"]),
    _entry("standard", "ISO 1413 shock resistance", "standard",
           "Covers a drop onto a wooden floor from 1 m with the movement "
           "cased. Shock protection (Incabloc-type) is assumed.",
           {"drop_height_m": 1.0},
           ["shock", "iso 1413"]),
    _entry("process", "gear hobbing and pinion cutting", "estimate",
           "Watch trains normally use modules from 0.08 mm upward. Below that, "
           "tooth flank quality and cutter life become the limit.",
           {"module_min_mm": 0.08, "pinion_min_teeth": 6, "wheel_teeth_max": 120},
           ["gear", "module", "hobbing", "pinion"]),
    _entry("process", "case finishing", "estimate",
           "Brushed surfaces hide scratches; polished surfaces show them. "
           "Bead blasting softens edges and needs masking at the gasket seats.",
           {"finishes": ["brushed", "polished", "bead-blasted", "PVD black", "two-tone"]},
           ["finish", "brushing", "polishing"]),
    _entry("process", "assembly and regulation", "estimate",
           "Expect a rate adjustment in six positions, a 24 h run-down and a "
           "water test after casing. These are bench steps, not CAD outputs.",
           {"positions": 6, "run_down_h": 24},
           ["assembly", "regulation", "timegrapher"]),
    _entry("component", "screw-down crown", "reference",
           "A threaded crown with a gasket seals against the case tube. It is "
           "the normal construction above 100 m, together with a screw-down "
           "caseback.",
           {"water_resistance_min_m": 100},
           ["crown", "screw down crown", "screw-down crown"]),
    _entry("component", "screw-down caseback", "reference",
           "Threaded back with an O-ring; preferred above 100 m. An exhibition "
           "back uses a crystal window and adds height.",
           {"water_resistance_min_m": 100},
           ["caseback", "screw back", "exhibition back"]),
    _entry("component", "mainspring barrel", "estimate",
           "The barrel stores the energy; usable turns are typically 5-7 of "
           "the nominal wound turns. Reserve is the barrel-to-centre ratio "
           "times usable turns.",
           {"usable_turns": 6.5, "efficiency_per_mesh": 0.93},
           ["barrel", "mainspring"]),
]


STATUS_LABELS = {
    "reference": "published reference data",
    "standard": "published standard",
    "estimate": "engineering estimate, not a measurement",
}


def _decorate(entry: dict) -> dict:
    """Add honest provenance fields without changing the curated table."""
    out = dict(entry)
    status = str(entry.get("status") or "estimate")
    out["status_label"] = STATUS_LABELS.get(status, status)
    out["provenance"] = {
        "kind": "curated" if status in ("reference", "standard") else "estimate",
        "source_url": entry.get("source_url", ""),
        "retrieved_at": entry.get("retrieved_at", ""),
        "note": ("Curated by WatchMaker; no live manufacturer datasheet was "
                 "fetched for this entry."),
    }
    return out


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def lookup(query: str, category: str | None = None) -> dict:
    """Find reference entries by name, alias or summary text."""
    needle = _normalise(query)
    if not needle:
        return {"query": query, "category": category, "matches": [],
                "note": "empty query"}
    cat = _normalise(category) if category else ""
    tiers: list[list[dict]] = [[], [], []]
    for entry in KNOWLEDGE:
        if cat and cat not in _normalise(entry["category"]):
            continue
        name = _normalise(entry["name"])
        aliases = [_normalise(a) for a in entry.get("aliases", [])]
        haystack = " ".join([name, *aliases, _normalise(entry["summary"])])
        if needle == name or needle in aliases:
            tiers[0].append(entry)
        elif needle in name or any(needle in alias or alias in needle for alias in aliases):
            tiers[1].append(entry)
        elif needle in haystack:
            tiers[2].append(entry)
    matches = [e for tier in tiers for e in tier]
    matches = matches[:8]
    if not matches:
        return {
            "query": query,
            "category": category or "",
            "matches": [],
            "note": ("No curated reference matched. Do not invent a number; "
                     "ask the user or keep the field as an estimate."),
        }
    return {
        "query": query,
        "category": category or "",
        "matches": [_decorate(entry) for entry in matches],
        "note": ("status=reference/standard values are published; status=estimate "
                 "values are modelling assumptions and must stay labelled that way."),
    }


def categories() -> list[str]:
    seen = []
    for entry in KNOWLEDGE:
        if entry["category"] not in seen:
            seen.append(entry["category"])
    return seen


def summary() -> dict:
    out: dict[str, int] = {}
    for entry in KNOWLEDGE:
        out[entry["category"]] = out.get(entry["category"], 0) + 1
    return {"entries": len(KNOWLEDGE), "by_category": out}
