"""Sourced movement (calibre) database and case-fit compatibility.

This module is the only place manufacturer data enters the application.  It is
deliberately small: four widely used calibres whose headline figures are
published by the manufacturer.  A field that the retained sources do not
publish stays ``None`` and is reported as ``unknown`` — never filled from a
forum post, a retailer page or a plausible guess.

Provenance model
----------------
Every record carries a list of ``provenance`` groups.  A group names the fields
it covers, the source id and one of:

``verified``    a manufacturer document states the value
``secondary``   a reputable technical source states the value
``estimated``   a documented design assumption, explicitly not manufacturer data
``unknown``     the value is null and the reason is recorded in ``unknowns``

Corrections are made with :func:`correct_record`, which appends a revision
entry (field, old value, new value, source, date, note) instead of overwriting
history.  Records are migrated through :func:`migrate_record` so a stored record
from an older revision keeps every value it already had.

Compatibility is a structured report, not a certificate.  The rules use the
same case-wall convention as ``verify.py`` (1.6 mm) and explicit assembly
clearances.  They do not model tolerances, plating thickness, dial feet,
movement holders, stem cut lengths or a solid-kernel interference check.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from .schema import Spec

MOVEMENT_SCHEMA_VERSION = 1
RETRIEVED = "2026-11-16"

STATUSES = ("verified", "secondary", "estimated", "unknown")
CONFIDENCE = ("high", "medium", "low")
TYPES = ("automatic", "manual", "quartz")

# Design rules used by the compatibility report.  These are engineering
# assumptions, labelled as such in every check.  They mirror the case-wall
# convention in verify.py and the solver's layer clearances.
CASE_WALL_MM = 1.6
MIN_RADIAL_CLEARANCE_MM = 0.15
TARGET_RADIAL_CLEARANCE_MM = 0.30
MIN_STACK_MARGIN_MM = 0.10
MIN_HAND_CLEARANCE_MM = 0.10
STEM_TOLERANCE_MM = 0.25
STEM_WARNING_MM = 0.50
CASEBACK_CLEARANCE_MM = 0.20


def _source(sid: str, name: str, publisher: str, url: str, kind: str,
            *, published: str | None = None, revision: str | None = None,
            retrieved: str = RETRIEVED, note: str = "") -> dict:
    return {"id": sid, "name": name, "publisher": publisher, "url": url,
            "kind": kind, "published": published, "revision": revision,
            "retrieved": retrieved, "note": note}


SOURCES = {
    "eta-2824-2-page": _source(
        "eta-2824-2-page", "ETA 2824-2 product page", "ETA SA Manufacture Horlogère Suisse",
        "https://portal.eta.ch/en/mecaline/2824-2-2824-2-5.html",
        "manufacturer", note="Official product page; undated, retrieved for this build."),
    "miyota-9015-spec": _source(
        "miyota-9015-spec", "Miyota 9015 specification sheet", "Miyota / Citizen Watch Co.",
        "https://miyotamovement.com/uploads/product/product_7ev6UD3QZ0FwfgH1kJ.pdf",
        "manufacturer", published="2025-08-21",
        note="PDF metadata date; the web product page is undated."),
    "miyota-9015-page": _source(
        "miyota-9015-page", "Miyota 9015 product page", "Miyota / Citizen Watch Co.",
        "https://miyotamovement.com/product/9015", "manufacturer",
        note="Product page used to locate the official specification PDF."),
    "sellita-sw200-1": _source(
        "sellita-sw200-1", "Sellita SW 200-1 technical documentation", "Sellita Watch Co. S.A.",
        "https://www.sellita.ch/scripts/calibres/images/DocTec_SW200-1.pdf",
        "manufacturer", published="2024-09-02", revision="07 / 02.09.2024",
        note="Technical documentation, drawing SW106768/SW106769."),
    "seiko-nh35a": _source(
        "seiko-nh35a", "Seiko Cal. NH35A movement specification", "Seiko Instruments Inc.",
        "https://irp.cdn-website.com/18c7541b/files/uploaded/MANUALE%20SEIKO%20NH35.pdf",
        "manufacturer", published="2021-03-18", revision="Version 4",
        note="Official movement specification and casing drawing."),
}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
# A null field below is deliberate.  The matching ``unknowns`` note explains
# why: the retained source does not publish it, it is not "zero".

MOVEMENTS: dict[str, dict] = {
    "eta-2824-2": {
        "schema_version": MOVEMENT_SCHEMA_VERSION,
        "id": "eta-2824-2",
        "manufacturer": "ETA",
        "calibre": "2824-2",
        "name": "ETA 2824-2",
        "line": "Mecaline",
        "type": "automatic",
        "diameter_mm": 25.60,
        "case_fitting_diameter_mm": 25.60,
        "overall_diameter_mm": 25.60,
        "thickness_mm": 4.60,
        "stack_height_mm": None,
        "hand_fitting": {"hour_mm": None, "minute_mm": None, "second_mm": None},
        "stem": {"position_height_mm": None, "diameter_mm": None,
                 "distance_from_centre_mm": None},
        "dial": {"max_diameter_mm": None, "date_position": None,
                 "feet": None},
        "support": {"date": True, "day-date": False, "power-reserve": None,
                    "gmt": None, "moonphase": None, "chronograph": False,
                    "annual-calendar": None, "perpetual-calendar": None,
                    "tourbillon": None, "minute-repeater": None,
                    "small_seconds": None},
        "complications": [{"id": "date", "position": None,
                           "note": "Date display in window; position not stated on the product page."}],
        "power_reserve_h": 42.0,
        "frequency_bph": 28800,
        "jewels": 25,
        "winding": "automatic",
        "manual_winding": None,
        "hacking": True,
        "accuracy_s_per_day": None,
        "lift_angle_deg": None,
        "mounting": {"method": None, "caseback": None},
        "constraints": ["Bidirectional self-winding", "Stop-second for time setting"],
        "sources": ["eta-2824-2-page"],
        "provenance": [
            {"fields": ["manufacturer", "calibre", "name", "line", "type",
                        "diameter_mm", "case_fitting_diameter_mm",
                        "overall_diameter_mm", "thickness_mm", "frequency_bph",
                        "jewels", "power_reserve_h", "winding", "hacking",
                        "support.date", "support.day-date", "support.chronograph"],
             "source": "eta-2824-2-page", "status": "verified"},
        ],
        "unknowns": {
            "stack_height_mm": "The product page gives the movement height, not a separate mounted stack height.",
            "hand_fitting.hour_mm": "Hand-fitting heights are in ETA's technical communication, not the retained public page.",
            "hand_fitting.minute_mm": "Hand-fitting heights are in ETA's technical communication, not the retained public page.",
            "hand_fitting.second_mm": "Hand-fitting heights are in ETA's technical communication, not the retained public page.",
            "stem.position_height_mm": "Stem height is not published on the retained product page.",
            "stem.diameter_mm": "Stem diameter depends on the supplied stem reference.",
            "stem.distance_from_centre_mm": "Not published on the retained product page.",
            "dial.max_diameter_mm": "Dial seating diameter is not published on the retained product page.",
            "dial.date_position": "The page states a date window but not its position; the drawing orientation is not a specification.",
            "dial.feet": "Dial feet positions are in a separate technical drawing.",
            "manual_winding": "Manual winding is not stated on the retained product page.",
            "support.day-date": "The base calibre is listed as a date movement; day-date is not stated.",
            "support.power-reserve": "The base calibre has no power-reserve display in the retained source.",
            "support.gmt": "The base calibre is not listed as a GMT movement.",
            "support.moonphase": "The base calibre is not listed with a moon phase.",
            "support.annual-calendar": "The base calibre is not listed with an annual calendar.",
            "support.perpetual-calendar": "The base calibre is not listed with a perpetual calendar.",
            "support.tourbillon": "The base calibre is not listed with a tourbillon.",
            "support.minute-repeater": "The base calibre is not listed with a minute repeater.",
            "support.small_seconds": "The calibre is described as central seconds; small-seconds conversion is not stated.",
            "accuracy_s_per_day": "Accuracy is not quoted on the retained product page.",
            "lift_angle_deg": "Lift angle is not quoted on the retained product page.",
            "mounting.method": "The public page does not state the casing method.",
            "mounting.caseback": "The public page does not state a caseback requirement.",
        },
        "revisions": [],
        "status": "verified",
        "confidence": "high",
        "verified_at": RETRIEVED,
        "notes": ["Headline figures come from ETA's official 2824-2 product page.",
                  "Do not treat the missing hand/stem heights as zero."],
    },
    "miyota-9015": {
        "schema_version": MOVEMENT_SCHEMA_VERSION,
        "id": "miyota-9015",
        "manufacturer": "Miyota",
        "calibre": "9015",
        "name": "Miyota 9015",
        "line": "Premium Mechanical",
        "type": "automatic",
        "diameter_mm": 26.00,
        "case_fitting_diameter_mm": None,
        "overall_diameter_mm": 26.00,
        "thickness_mm": 3.90,
        "stack_height_mm": None,
        "hand_fitting": {"hour_mm": None, "minute_mm": None, "second_mm": None},
        "stem": {"position_height_mm": None, "diameter_mm": None,
                 "distance_from_centre_mm": None},
        "dial": {"max_diameter_mm": None, "date_position": None,
                 "feet": None},
        "support": {"date": True, "day-date": False, "power-reserve": None,
                    "gmt": None, "moonphase": None, "chronograph": False,
                    "annual-calendar": None, "perpetual-calendar": None,
                    "tourbillon": None, "minute-repeater": None,
                    "small_seconds": None},
        "complications": [{"id": "date", "position": None,
                           "note": "Date shown on the specification drawing; position is not stated in text."}],
        "power_reserve_h": 42.0,
        "frequency_bph": 28800,
        "jewels": 24,
        "winding": "automatic",
        "manual_winding": True,
        "hacking": True,
        "accuracy_s_per_day": None,
        "lift_angle_deg": 51.0,
        "mounting": {"method": None, "caseback": None},
        "constraints": ["Automatic and manual winding", "Stop-second",
                        "Casing clamps or screws listed as exchangeable"],
        "sources": ["miyota-9015-spec", "miyota-9015-page"],
        "provenance": [
            {"fields": ["manufacturer", "calibre", "name", "line", "type",
                        "diameter_mm", "overall_diameter_mm", "thickness_mm",
                        "frequency_bph", "jewels", "power_reserve_h",
                        "winding", "manual_winding", "hacking",
                        "lift_angle_deg", "support.date", "support.day-date",
                        "support.chronograph"],
             "source": "miyota-9015-spec", "status": "verified"},
        ],
        "unknowns": {
            "case_fitting_diameter_mm": "The sheet gives the movement outline diameter, not a separate case-fitting diameter.",
            "stack_height_mm": "The sheet gives the movement height, not a mounted stack height.",
            "hand_fitting.hour_mm": "The sheet shows minimum hand clearance, not per-hand fitting heights.",
            "hand_fitting.minute_mm": "The sheet shows minimum hand clearance, not per-hand fitting heights.",
            "hand_fitting.second_mm": "The sheet shows minimum hand clearance, not per-hand fitting heights.",
            "stem.position_height_mm": "Stem height is not listed on the specification sheet.",
            "stem.diameter_mm": "The attached stem is a part number; its diameter is not stated.",
            "stem.distance_from_centre_mm": "Not published on the specification sheet.",
            "dial.max_diameter_mm": "Dial seating diameter is not published on the sheet.",
            "dial.date_position": "The drawing shows a date but the text does not state its position.",
            "dial.feet": "Dial feet are not dimensioned on the sheet.",
            "support.day-date": "The calibre is listed with a date display only.",
            "support.power-reserve": "No power-reserve display is listed.",
            "support.gmt": "No GMT hand is listed.",
            "support.moonphase": "No moon phase is listed.",
            "support.annual-calendar": "No annual calendar is listed.",
            "support.perpetual-calendar": "No perpetual calendar is listed.",
            "support.tourbillon": "No tourbillon is listed.",
            "support.minute-repeater": "No minute repeater is listed.",
            "support.small_seconds": "The calibre is a centre-seconds design; small-seconds conversion is not stated.",
            "accuracy_s_per_day": "The sheet gives an accuracy band, not a single value; see notes.",
            "mounting.method": "Clamps or screws are listed as exchangeable, but the fitted method is not stated.",
            "mounting.caseback": "No caseback requirement is stated on the sheet.",
        },
        "revisions": [],
        "status": "verified",
        "confidence": "high",
        "verified_at": RETRIEVED,
        "notes": ["Accuracy on the sheet is a range (10 to 30 s/day) and posture difference under 30 s; no single accuracy value is stored.",
                  "Minimum hand clearance 0.40 mm is a clearance note, not a hand-fitting height."],
    },
    "sellita-sw200-1": {
        "schema_version": MOVEMENT_SCHEMA_VERSION,
        "id": "sellita-sw200-1",
        "manufacturer": "Sellita",
        "calibre": "SW 200-1",
        "name": "Sellita SW 200-1",
        "line": "Swiss Made",
        "type": "automatic",
        "diameter_mm": 26.00,
        "case_fitting_diameter_mm": 25.60,
        "overall_diameter_mm": 26.00,
        "thickness_mm": 4.60,
        "stack_height_mm": None,
        "hand_fitting": {"hour_mm": 0.95, "minute_mm": 1.35,
                         "second_mm": 1.75},
        "stem": {"position_height_mm": None, "diameter_mm": None,
                 "distance_from_centre_mm": 12.80},
        "dial": {"max_diameter_mm": None, "date_position": None,
                 "feet": None},
        "support": {"date": True, "day-date": False, "power-reserve": None,
                    "gmt": None, "moonphase": None, "chronograph": False,
                    "annual-calendar": None, "perpetual-calendar": None,
                    "tourbillon": None, "minute-repeater": None,
                    "small_seconds": None},
        "complications": [{"id": "date", "position": None,
                           "note": "Date display in window with quick corrector; position not stated in text."}],
        "power_reserve_h": 41.0,
        "frequency_bph": 28800,
        "jewels": 26,
        "winding": "automatic",
        "manual_winding": True,
        "hacking": True,
        "accuracy_s_per_day": None,
        "lift_angle_deg": 50.0,
        "mounting": {"method": "case clamps", "caseback": None},
        "constraints": ["Case clamps shown in the casing-options drawing",
                        "Screw-down caseback not stated as a requirement"],
        "sources": ["sellita-sw200-1"],
        "provenance": [
            {"fields": ["manufacturer", "calibre", "name", "line", "type",
                        "diameter_mm", "case_fitting_diameter_mm",
                        "overall_diameter_mm", "thickness_mm",
                        "hand_fitting.hour_mm", "hand_fitting.minute_mm",
                        "hand_fitting.second_mm", "stem.distance_from_centre_mm",
                        "frequency_bph", "jewels",
                        "winding", "manual_winding", "hacking",
                        "lift_angle_deg", "mounting.method",
                        "support.date", "support.day-date", "support.chronograph"],
             "source": "sellita-sw200-1", "status": "verified"},
            {"fields": ["power_reserve_h"],
             "source": "sellita-sw200-1", "status": "secondary",
             "note": "41 h is the documented typical value; the documented minimum is 38 h."},
        ],
        "unknowns": {
            "stack_height_mm": "The document gives the movement height, not a mounted stack height.",
            "stem.position_height_mm": "The casing drawing gives stem travel and centre distance, not stem height.",
            "stem.diameter_mm": "Stem diameter depends on the supplied stem reference.",
            "dial.max_diameter_mm": "Dial seating diameter is not stated in the technical documentation.",
            "dial.date_position": "The document states a date window but not its position.",
            "dial.feet": "Dial feet are not dimensioned in the retained pages.",
            "support.day-date": "The document lists a date display, not a day-date.",
            "support.power-reserve": "The base calibre has no power-reserve display in the document.",
            "support.gmt": "The base calibre is not listed as a GMT movement.",
            "support.moonphase": "No moon phase is listed.",
            "support.annual-calendar": "No annual calendar is listed.",
            "support.perpetual-calendar": "No perpetual calendar is listed.",
            "support.tourbillon": "No tourbillon is listed.",
            "support.minute-repeater": "No minute repeater is listed.",
            "support.small_seconds": "A no-seconds variant is documented, but small-seconds is not.",
            "accuracy_s_per_day": "Accuracy is not quoted in the retained technical specification.",
            "mounting.caseback": "No caseback requirement is stated in the retained documentation.",
        },
        "revisions": [],
        "status": "verified",
        "confidence": "high",
        "verified_at": RETRIEVED,
        "notes": ["Hand-fitting heights are the standard (position 2) column of drawing SW106769: hour 0.95 mm, minute 1.35 mm, second 1.75 mm above the dial seat.",
                  "Power reserve is documented as minimum 38 h and typical 41 h; the typical value is stored and marked secondary."],
    },
    "seiko-nh35a": {
        "schema_version": MOVEMENT_SCHEMA_VERSION,
        "id": "seiko-nh35a",
        "manufacturer": "Seiko",
        "calibre": "NH35A",
        "name": "Seiko Cal. NH35A",
        "line": "NH",
        "type": "automatic",
        "diameter_mm": 27.40,
        "case_fitting_diameter_mm": None,
        "overall_diameter_mm": 27.40,
        "thickness_mm": 5.32,
        "stack_height_mm": None,
        "hand_fitting": {"hour_mm": None, "minute_mm": None, "second_mm": None},
        "stem": {"position_height_mm": None, "diameter_mm": None,
                 "distance_from_centre_mm": None},
        "dial": {"max_diameter_mm": None, "date_position": None,
                 "feet": None},
        "support": {"date": True, "day-date": False, "power-reserve": None,
                    "gmt": None, "moonphase": None, "chronograph": False,
                    "annual-calendar": None, "perpetual-calendar": None,
                    "tourbillon": None, "minute-repeater": None,
                    "small_seconds": None},
        "complications": [{"id": "date", "position": None,
                           "note": "Date display with quick correction; position not stated in text."}],
        "power_reserve_h": 41.0,
        "frequency_bph": 21600,
        "jewels": 24,
        "winding": "automatic",
        "manual_winding": True,
        "hacking": True,
        "accuracy_s_per_day": None,
        "lift_angle_deg": None,
        "mounting": {"method": None, "caseback": "screw-down"},
        "constraints": ["Screw-type case back required",
                        "Not corresponding with diver's watches",
                        "Dial holding spacer adds 1.96 mm to the outline diameter"],
        "sources": ["seiko-nh35a"],
        "provenance": [
            {"fields": ["manufacturer", "calibre", "name", "line", "type",
                        "diameter_mm", "overall_diameter_mm", "thickness_mm",
                        "frequency_bph", "jewels", "power_reserve_h",
                        "winding", "manual_winding", "hacking",
                        "mounting.caseback", "support.date", "support.day-date",
                        "support.chronograph"],
             "source": "seiko-nh35a", "status": "verified"},
        ],
        "unknowns": {
            "case_fitting_diameter_mm": "The document gives the movement diameter and the spacer diameter, not a separate case-fitting diameter.",
            "stack_height_mm": "The document gives the movement height, not a mounted stack height.",
            "hand_fitting.hour_mm": "Per-hand fitting heights are not extracted from the drawing; no value is guessed.",
            "hand_fitting.minute_mm": "Per-hand fitting heights are not extracted from the drawing; no value is guessed.",
            "hand_fitting.second_mm": "Per-hand fitting heights are not extracted from the drawing; no value is guessed.",
            "stem.position_height_mm": "The stem height is in the casing drawing but not stated as text; no value is guessed.",
            "stem.diameter_mm": "Stem diameter is not stated in the text.",
            "stem.distance_from_centre_mm": "Not stated as text in the retained document.",
            "dial.max_diameter_mm": "Dial seating diameter is not stated as text.",
            "dial.date_position": "The document shows a date but the text does not state its position.",
            "dial.feet": "Dial feet are not dimensioned in the text pages.",
            "support.day-date": "The document lists a date display only.",
            "support.power-reserve": "No power-reserve display is listed.",
            "support.gmt": "No GMT hand is listed.",
            "support.moonphase": "No moon phase is listed.",
            "support.annual-calendar": "No annual calendar is listed.",
            "support.perpetual-calendar": "No perpetual calendar is listed.",
            "support.tourbillon": "No tourbillon is listed.",
            "support.minute-repeater": "No minute repeater is listed.",
            "support.small_seconds": "The calibre is a centre-seconds design; small-seconds conversion is not stated.",
            "accuracy_s_per_day": "The document gives an accuracy band (-20 to +40 s/day), not a single value.",
            "lift_angle_deg": "Lift angle is not stated in the retained text.",
            "mounting.method": "The casing drawing is not extracted as a mounting dimension; no method is guessed.",
        },
        "revisions": [],
        "status": "verified",
        "confidence": "high",
        "verified_at": RETRIEVED,
        "notes": ["The 27.40 mm figure is the movement diameter; 29.36 mm is the diameter with the dial holding spacer.",
                  "The document explicitly limits the calibre's use: screw-type case back required and not for diver's watches.",
                  "Power reserve is stated as more than 41 hours; 41 h is stored as a conservative floor, not a measured maximum."],
    },
}

# Canonical field paths, kept explicit so validation can reject a typo instead
# of silently accepting a new key.
SCALAR_FIELDS = (
    "manufacturer", "calibre", "name", "line", "type",
    "diameter_mm", "case_fitting_diameter_mm", "overall_diameter_mm",
    "thickness_mm", "stack_height_mm",
    "hand_fitting.hour_mm", "hand_fitting.minute_mm", "hand_fitting.second_mm",
    "stem.position_height_mm", "stem.diameter_mm", "stem.distance_from_centre_mm",
    "dial.max_diameter_mm", "dial.date_position", "dial.feet",
    "power_reserve_h", "frequency_bph", "jewels", "winding",
    "manual_winding", "hacking", "accuracy_s_per_day", "lift_angle_deg",
    "mounting.method", "mounting.caseback",
    "support.date", "support.day-date", "support.power-reserve", "support.gmt",
    "support.moonphase", "support.chronograph", "support.annual-calendar",
    "support.perpetual-calendar", "support.tourbillon", "support.minute-repeater",
    "support.small_seconds",
)


def _path_get(node: Any, path: str, default=None):
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _path_set(node: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    for key in parts[:-1]:
        node = node.setdefault(key, {})
    node[parts[-1]] = value


# ---------------------------------------------------------------------------
# Migration and validation
# ---------------------------------------------------------------------------
def migrate_record(record: dict) -> dict:
    """Return a copy upgraded to the current movement-record schema.

    The migration is additive and pure.  It never invents a specification
    value: missing scalar fields are set to ``None`` (unknown) and the change is
    recorded in ``revisions`` so a reader can see that the blank came from a
    migration rather than from the manufacturer.
    """
    if not isinstance(record, dict):
        raise TypeError("movement record must be a dictionary")
    out = copy.deepcopy(record)
    version = out.get("schema_version")
    version = 1 if version is None else int(version)
    if version < MOVEMENT_SCHEMA_VERSION:
        out.setdefault("revisions", []).append({
            "event": "schema_migration", "from": version,
            "to": MOVEMENT_SCHEMA_VERSION, "at": RETRIEVED,
            "note": "missing fields set to unknown; no value was inferred",
        })
    out["schema_version"] = MOVEMENT_SCHEMA_VERSION
    out.setdefault("hand_fitting", {})
    out.setdefault("stem", {})
    out.setdefault("dial", {})
    out.setdefault("support", {})
    out.setdefault("mounting", {})
    out.setdefault("unknowns", {})
    out.setdefault("revisions", [])
    out.setdefault("provenance", [])
    out.setdefault("sources", [])
    for path in SCALAR_FIELDS:
        if _path_get(out, path) is None and path not in out["unknowns"]:
            out["unknowns"][path] = "not published by the retained sources"
    return out


def validate_record(record: dict) -> list[str]:
    """Return a list of data-quality problems.  Empty means structurally sound."""
    issues: list[str] = []
    for key in ("id", "manufacturer", "calibre", "name", "type", "status",
                "confidence", "verified_at", "sources", "provenance"):
        if not record.get(key):
            issues.append(f"{key} is required")
    if record.get("type") not in TYPES:
        issues.append(f"type must be one of {TYPES}")
    if record.get("status") not in STATUSES:
        issues.append(f"status must be one of {STATUSES}")
    if record.get("confidence") not in CONFIDENCE:
        issues.append(f"confidence must be one of {CONFIDENCE}")
    if not record.get("sources"):
        issues.append("at least one source is required")
    known_sources = {item["id"] for item in record.get("sources_full", [])}
    for group in record.get("provenance", []):
        if group.get("status") not in STATUSES:
            issues.append(f"provenance status invalid: {group.get('status')}")
        if group.get("source") not in known_sources:
            issues.append(f"provenance references unknown source {group.get('source')}")
        for path in group.get("fields", []):
            if path not in SCALAR_FIELDS:
                issues.append(f"provenance covers unknown field {path}")
            value = _path_get(record, path)
            if value is None and group.get("status") != "unknown":
                issues.append(f"provenance marks null field {path} as {group.get('status')}")
    covered = {path for group in record.get("provenance", [])
               for path in group.get("fields", [])}
    for path in SCALAR_FIELDS:
        value = _path_get(record, path)
        if value is not None and path not in covered:
            issues.append(f"non-null field {path} has no provenance")
        if value is None and path not in record.get("unknowns", {}):
            issues.append(f"null field {path} has no unknown note")
    for path in ("diameter_mm", "thickness_mm", "power_reserve_h", "frequency_bph"):
        value = _path_get(record, path)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            issues.append(f"{path} must be numeric or null")
    return issues


def _attach_sources(record: dict) -> dict:
    out = copy.deepcopy(record)
    out["sources_full"] = [copy.deepcopy(SOURCES[sid]) for sid in record.get("sources", [])
                           if sid in SOURCES]
    return out


def _normalised(record: dict) -> dict:
    out = _attach_sources(migrate_record(record))
    issues = validate_record(out)
    if issues:
        raise ValueError(f"invalid movement record {record.get('id')}: " + "; ".join(issues))
    return out


@lru_cache(maxsize=1)
def _records() -> tuple[dict, ...]:
    """Immutable, validated record cache.  Callers get deep copies."""
    return tuple(_normalised(record) for record in MOVEMENTS.values())


def all_records() -> list[dict]:
    return [copy.deepcopy(record) for record in _records()]


def get_record(movement_id: str) -> dict | None:
    needle = str(movement_id or "").strip().lower()
    for record in _records():
        if record["id"].lower() == needle or record["calibre"].lower() == needle:
            return copy.deepcopy(record)
    return None


# ---------------------------------------------------------------------------
# Provenance projection
# ---------------------------------------------------------------------------
def field_provenance(record: dict) -> dict[str, dict]:
    """Flatten a record into ``path -> {value, status, source, note}``.

    Null fields are present with status ``unknown`` and the recorded reason.
    This is what the UI renders next to each specification line.
    """
    groups = record.get("provenance", [])
    unknowns = record.get("unknowns", {})
    source_meta = {item["id"]: item for item in record.get("sources_full", [])}
    out: dict[str, dict] = {}
    for path in SCALAR_FIELDS:
        value = _path_get(record, path)
        if value is None:
            out[path] = {"value": None, "status": "unknown", "source": None,
                         "note": unknowns.get(path, "not published")}
            continue
        match = next((group for group in groups if path in group.get("fields", [])),
                     None)
        if match is None:
            out[path] = {"value": value, "status": "unknown", "source": None,
                         "note": "value present without a source group"}
            continue
        source = source_meta.get(match.get("source"), {})
        out[path] = {
            "value": value,
            "status": match.get("status", "unknown"),
            "source": match.get("source"),
            "source_name": source.get("name"),
            "source_url": source.get("url"),
            "note": match.get("note") or unknowns.get(path, ""),
        }
    return out


def missing_fields(record: dict) -> list[dict]:
    prov = field_provenance(record)
    return [{"path": path, "reason": item["note"]}
            for path, item in prov.items() if item["status"] == "unknown"]


# ---------------------------------------------------------------------------
# Search / catalogue
# ---------------------------------------------------------------------------
def summary(record: dict) -> dict:
    return {
        "id": record["id"],
        "manufacturer": record["manufacturer"],
        "calibre": record["calibre"],
        "name": record["name"],
        "type": record["type"],
        "diameter_mm": record.get("diameter_mm"),
        "thickness_mm": record.get("thickness_mm"),
        "power_reserve_h": record.get("power_reserve_h"),
        "frequency_bph": record.get("frequency_bph"),
        "jewels": record.get("jewels"),
        "winding": record.get("winding"),
        "status": record["status"],
        "confidence": record["confidence"],
        "verified_at": record["verified_at"],
        "missing_count": len(missing_fields(record)),
    }


def search(query: str = "", *, maker: str = "", type: str = "",
           min_diameter: float | None = None, max_diameter: float | None = None,
           min_thickness: float | None = None, max_thickness: float | None = None,
           include_unknown: bool = False) -> list[dict]:
    needle = str(query or "").strip().lower()
    maker = str(maker or "").strip().lower()
    type = str(type or "").strip().lower()
    out = []
    for record in _records():
        haystack = " ".join(str(record.get(key) or "")
                            for key in ("manufacturer", "calibre", "name", "line")).lower()
        if needle and needle not in haystack:
            continue
        if maker and maker != str(record.get("manufacturer") or "").lower():
            continue
        if type and type != record.get("type"):
            continue
        diameter = record.get("diameter_mm")
        thickness = record.get("thickness_mm")
        if not include_unknown:
            if min_diameter is not None and (diameter is None or diameter < min_diameter):
                continue
            if max_diameter is not None and (diameter is None or diameter > max_diameter):
                continue
            if min_thickness is not None and (thickness is None or thickness < min_thickness):
                continue
            if max_thickness is not None and (thickness is None or thickness > max_thickness):
                continue
        out.append(summary(record))
    return sorted(out, key=lambda item: (item["manufacturer"], item["calibre"]))


def catalog() -> dict:
    records = [summary(record) for record in _records()]
    return {
        "schema_version": MOVEMENT_SCHEMA_VERSION,
        "count": len(records),
        "movements": sorted(records, key=lambda item: (item["manufacturer"], item["calibre"])),
        "makers": sorted({item["manufacturer"] for item in records}),
        "types": sorted({item["type"] for item in records}),
        "note": ("Only specifications published by the manufacturer are stored. "
                 "Null means not published by the retained source, not zero."),
        "sources": [copy.deepcopy(source) for source in SOURCES.values()],
    }


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------
def correct_record(record: dict, updates: dict[str, Any], *, source: str,
                   note: str, at: str) -> dict:
    """Return a corrected copy with an append-only revision history.

    ``source`` must name a source already attached to the record.  A correction
    never deletes the old value: it is written to ``revisions`` together with
    the source and the date.
    """
    out = copy.deepcopy(record)
    known_sources = {sid for sid in out.get("sources", [])}
    if source not in known_sources:
        raise ValueError(f"unknown source {source!r}; add it to the record first")
    history = out.setdefault("revisions", [])
    changed = False
    for path, value in updates.items():
        if path not in SCALAR_FIELDS:
            raise KeyError(f"unknown movement field {path!r}")
        old = _path_get(out, path)
        if old == value:
            continue
        _path_set(out, path, value)
        history.append({"field": path, "from": old, "to": value,
                        "source": source, "at": at, "note": note})
        # Move the field to the correcting source's provenance group.
        for group in out.get("provenance", []):
            if path in group.get("fields", []):
                group["fields"].remove(path)
        if value is None:
            out.setdefault("unknowns", {})[path] = note
        else:
            out.setdefault("unknowns", {}).pop(path, None)
            match = next((group for group in out.get("provenance", [])
                          if group.get("source") == source
                          and group.get("status") == "verified"), None)
            if match is None:
                out.setdefault("provenance", []).append(
                    {"fields": [path], "source": source, "status": "verified"})
            else:
                match.setdefault("fields", []).append(path)
        changed = True
    if changed:
        out["verified_at"] = at
    return migrate_record(out)


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------
def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _result(check_id: str, label: str, status: str, detail: str, *,
            measured: Any = None, limit: Any = None, unit: str = "mm",
            fix: dict | None = None, source: str = "calculated") -> dict:
    return {"id": check_id, "label": label, "status": status, "detail": detail,
            "measured": measured, "limit": limit, "unit": unit,
            "fix": fix, "source": source}


def _status_from_margin(margin: float, *, pass_at: float, warn_at: float,
                        high_is_good: bool = True) -> str:
    if high_is_good:
        if margin >= pass_at:
            return "PASS"
        if margin >= warn_at:
            return "WARNING"
        return "FAIL"
    if margin <= pass_at:
        return "PASS"
    if margin <= warn_at:
        return "WARNING"
    return "FAIL"


def _overall(checks: list[dict]) -> str:
    statuses = {check["status"] for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses or "UNKNOWN" in statuses:
        return "WARNING"
    return "PASS"


def compatibility(spec: Spec, record: dict) -> dict:
    """Compare one movement record with the current design.

    Returns PASS/WARNING/FAIL/UNKNOWN per check, plus suggested spec changes.
    Nothing is applied and no other field is mutated.  UNKNOWN is used when the
    movement record does not publish the datum the check needs; it is not
    silently treated as a pass.
    """
    stack = spec.get("derived.stack") or {}
    case_d = _number(spec.get("case.diameter_mm"))
    case_t = _number(spec.get("case.thickness_mm"))
    dial_d = _number(spec.get("dial.diameter_mm"))
    current_h = _number(spec.get("movement.height_mm"), 4.6)
    movement_d = _number(record.get("overall_diameter_mm") or record.get("diameter_mm"))
    movement_t = _number(record.get("thickness_mm"))
    checks: list[dict] = []
    fixes: list[dict] = []

    def add_fix(path: str, value: Any, reason: str) -> dict:
        change = {"path": path, "to": value, "from": spec.get(path),
                  "reason": reason, "kind": "fit"}
        if change not in fixes:
            fixes.append(change)
        return change

    # 1. Case internal diameter -------------------------------------------
    if case_d is None or movement_d is None:
        checks.append(_result(
            "case.internal_diameter", "Case internal diameter", "UNKNOWN",
            "Case diameter or movement diameter is not available.",
            measured=movement_d, limit=case_d, source="unknown"))
    else:
        bore = case_d - 2 * CASE_WALL_MM
        radial = (bore - movement_d) / 2.0
        status = _status_from_margin(radial, pass_at=TARGET_RADIAL_CLEARANCE_MM,
                                     warn_at=MIN_RADIAL_CLEARANCE_MM)
        fix = None
        if status == "FAIL":
            required_case = movement_d + 2 * CASE_WALL_MM + 2 * TARGET_RADIAL_CLEARANCE_MM
            fix = add_fix("case.diameter_mm", round(required_case + 0.1, 1),
                          "A larger case bore is required for this movement.")
        checks.append(_result(
            "case.internal_diameter", "Case internal diameter", status,
            f"Ø{bore:.2f} mm case bore (case Ø{case_d:.1f} mm minus "
            f"{CASE_WALL_MM:.1f} mm walls) against Ø{movement_d:.2f} mm movement: "
            f"{radial:+.2f} mm radial clearance. Rule: pass ≥ "
            f"{TARGET_RADIAL_CLEARANCE_MM:.2f} mm, fail < {MIN_RADIAL_CLEARANCE_MM:.2f} mm.",
            measured=round(radial, 3), limit=TARGET_RADIAL_CLEARANCE_MM, fix=fix))

    # 2. Case internal height ---------------------------------------------
    if not stack or movement_t is None:
        checks.append(_result(
            "case.internal_height", "Case internal height", "UNKNOWN",
            "A solved stack or the movement thickness is not available.",
            source="unknown"))
    else:
        required = _number(stack.get("required_case_thickness_mm"), 0.0) - current_h + movement_t
        have = case_t if case_t is not None else _number(stack.get("available_case_thickness_mm"), 0.0)
        margin = have - required
        status = _status_from_margin(margin, pass_at=MIN_STACK_MARGIN_MM,
                                     warn_at=0.0)
        fix = None
        if status in ("FAIL", "WARNING"):
            fix = add_fix("case.thickness_mm", round(required + 0.5, 1),
                          "The case must be taller to keep the stack margin.")
        checks.append(_result(
            "case.internal_height", "Case internal height", status,
            f"Stack with this movement needs {required:.2f} mm of the "
            f"{have:.2f} mm case: {margin:+.2f} mm margin. Rule: pass ≥ "
            f"{MIN_STACK_MARGIN_MM:.2f} mm, fail < 0.",
            measured=round(margin, 3), limit=MIN_STACK_MARGIN_MM, fix=fix))

    # 3. Mounting ----------------------------------------------------------
    mounting = record.get("mounting") or {}
    case_mounting = str(spec.get("case.movement_mounting") or "unknown")
    movement_mounting = str(mounting.get("method") or "unknown")
    caseback_need = str(mounting.get("caseback") or "")
    mounting_problems = []
    mounting_fix = None
    caseback = str(spec.get("case.caseback") or "")
    if caseback_need == "screw-down" and caseback == "snap":
        mounting_problems.append("the movement requires a screw-type caseback, "
                                 "but this design uses a snap caseback")
        mounting_fix = add_fix("case.caseback", "screw-down",
                               "The manufacturer requires a screw-type caseback.")
    if movement_mounting == "unknown" or case_mounting == "unknown":
        status = "UNKNOWN" if not mounting_problems else "FAIL"
        detail = ("Movement casing method is not published; the case does not "
                  "declare one either." if not mounting_problems else
                  "; ".join(mounting_problems))
    elif movement_mounting == case_mounting:
        status = "PASS" if not mounting_problems else "FAIL"
        detail = f"Both use {case_mounting}."
    elif case_mounting == "spacer" and movement_mounting in ("case clamps", "tabs"):
        status = "WARNING" if not mounting_problems else "FAIL"
        detail = (f"Movement uses {movement_mounting}; the case declares only a "
                  "spacer. A clamp ledge or movement holder is needed.")
    else:
        status = "FAIL"
        detail = f"Movement uses {movement_mounting}, case provides {case_mounting}."
    if mounting_problems and detail == "Movement casing method is not published; the case does not declare one either.":
        detail = "; ".join(mounting_problems)
    checks.append(_result(
        "case.mounting", "Mounting and caseback requirement", status,
        detail + ("" if not caseback_need else
                  f" Documented requirement: {caseback_need} caseback."),
        measured=movement_mounting, limit=case_mounting, unit="",
        fix=mounting_fix, source="documented requirement + design declaration"))

    # 4. Stem / crown alignment -------------------------------------------
    stem_h = _number((record.get("stem") or {}).get("position_height_mm"))
    crown_z = _number(spec.get("case.crown_height_mm"))
    if crown_z is None and case_t is not None:
        crown_z = case_t * 0.45
    caseback_top = _number(stack.get("z_caseback_top"), 1.2)
    if stem_h is None or crown_z is None:
        checks.append(_result(
            "stem.crown_alignment", "Stem and crown alignment", "UNKNOWN",
            "The movement record does not publish a stem height above its "
            "seating plane, so axial crown alignment cannot be checked.",
            unit="mm", source="unknown"))
    else:
        movement_axis = caseback_top + stem_h
        delta = crown_z - movement_axis
        status = _status_from_margin(abs(delta), pass_at=STEM_TOLERANCE_MM,
                                     warn_at=STEM_WARNING_MM, high_is_good=False)
        fix = None
        if status in ("FAIL", "WARNING"):
            fix = add_fix("case.crown_height_mm", round(movement_axis, 2),
                          "Move the crown axis to the movement stem height.")
        checks.append(_result(
            "stem.crown_alignment", "Stem and crown alignment", status,
            f"Movement stem axis is {movement_axis:.2f} mm above the caseback "
            f"face; crown axis is {crown_z:.2f} mm: {delta:+.2f} mm. Rule: pass "
            f"≤ {STEM_TOLERANCE_MM:.2f} mm, fail > {STEM_WARNING_MM:.2f} mm.",
            measured=round(delta, 3), limit=STEM_TOLERANCE_MM, fix=fix))

    # 5. Dial fit ----------------------------------------------------------
    if case_d is None or dial_d is None:
        checks.append(_result(
            "dial.fit", "Dial fit", "UNKNOWN",
            "Case or dial diameter is not available.", source="unknown"))
    else:
        opening = case_d - 2 * CASE_WALL_MM - 0.2
        margin = opening - dial_d
        status = _status_from_margin(margin, pass_at=0.0, warn_at=-0.2)
        fix = None
        if status == "FAIL":
            fix = add_fix("case.diameter_mm", round(dial_d + 2 * CASE_WALL_MM + 0.4, 1),
                          "The dial does not fit the modelled case opening.")
        checks.append(_result(
            "dial.fit", "Dial fit", status,
            f"Ø{dial_d:.2f} mm dial against a Ø{opening:.2f} mm modelled opening: "
            f"{margin:+.2f} mm. The rehaut and dial feet are not modelled.",
            measured=round(margin, 3), limit=0.0, fix=fix))

    # 6. Dial date opening / movement support ------------------------------
    supports_date = (record.get("support") or {}).get("date")
    dial_date = bool(spec.get("dial.date_window"))
    if supports_date is None:
        checks.append(_result(
            "dial.date_opening", "Dial date opening", "UNKNOWN",
            "The movement record does not state whether a date display is fitted.",
            unit="", source="unknown"))
    elif dial_date and not supports_date:
        fix = add_fix("dial.date_window", False,
                      "This movement has no date display; the dial opening has no function.")
        checks.append(_result(
            "dial.date_opening", "Dial date opening", "FAIL",
            "The dial has a date window but the movement is recorded without a date display.",
            unit="", fix=fix))
    elif dial_date and supports_date:
        checks.append(_result(
            "dial.date_opening", "Dial date opening", "PASS",
            "Dial date window and movement date display agree.", unit=""))
    else:
        checks.append(_result(
            "dial.date_opening", "Dial date opening", "PASS",
            "The movement's date display is hidden by a no-date dial; this is a design choice, not a fit error.",
            unit=""))

    # 7. Hand clearance ----------------------------------------------------
    fittings = {key: _number((record.get("hand_fitting") or {}).get(f"{key}_mm"))
                for key in ("hour", "minute", "second")}
    known = {key: value for key, value in fittings.items() if value is not None}
    thickness = _number(spec.get("hands.thickness_mm"), 0.25)
    available = None
    if stack:
        crystal_bottom = _number(stack.get("z_crystal_bottom"))
        dial_top = _number(stack.get("z_dial_top"))
        if crystal_bottom is not None and dial_top is not None:
            available = crystal_bottom - dial_top
    if not known or available is None:
        checks.append(_result(
            "hands.clearance", "Hand clearance", "UNKNOWN",
            "Per-hand fitting heights or the solved crystal clearance are not available."
            + (" The source publishes a minimum hand clearance note, not fitting heights."
               if record["id"] == "miyota-9015" else ""),
            measured=None, limit=None, source="unknown"))
    else:
        highest = max(known.values())
        required = highest + thickness + MIN_HAND_CLEARANCE_MM
        margin = available - required
        status = _status_from_margin(margin, pass_at=MIN_HAND_CLEARANCE_MM,
                                     warn_at=0.0)
        fix = None
        if status in ("FAIL", "WARNING"):
            current_stack = _number(spec.get("hands.stack_height_mm"), 0.9)
            fix = add_fix("hands.stack_height_mm",
                          round(current_stack + max(0.0, -margin) + 0.2, 2),
                          "Allocate vertical space for the movement's real hand-fitting heights.")
        fittings_text = ", ".join(f"{key} {value:.2f}" for key, value in sorted(known.items()))
        checks.append(_result(
            "hands.clearance", "Hand clearance", status,
            f"Fitting heights above the dial seat: {fittings_text} mm. Highest "
            f"{highest:.2f} mm + {thickness:.2f} mm hand thickness + "
            f"{MIN_HAND_CLEARANCE_MM:.2f} mm assembly clearance needs "
            f"{required:.2f} mm; the solved dial-to-crystal space is "
            f"{available:.2f} mm: {margin:+.2f} mm.",
            measured=round(margin, 3), limit=MIN_HAND_CLEARANCE_MM, fix=fix))

    # 8. Complications alignment ------------------------------------------
    comps = list((spec.get("movement.complications") or {}).keys())
    unsupported = []
    unknown_support = []
    for comp in comps:
        support = (record.get("support") or {}).get(comp)
        if support is False:
            unsupported.append(comp)
        elif support is None:
            unknown_support.append(comp)
    date_position = (record.get("dial") or {}).get("date_position")
    dial_position = str(spec.get("dial.date_window_position") or "")
    position_problem = (bool(dial_date) and date_position is not None
                        and dial_position and str(date_position) != dial_position)
    if unsupported:
        fix = {"path": "movement.complications", "kind": "fit",
               "to": {key: value for key, value in (spec.get("movement.complications") or {}).items()
                      if key not in unsupported},
               "from": spec.get("movement.complications"),
               "reason": "This calibre does not provide: " + ", ".join(unsupported)}
        fixes.append(fix)
        checks.append(_result(
            "complication.alignment", "Complication alignment", "FAIL",
            "The design asks for " + ", ".join(unsupported) +
            ", which this base calibre does not provide. A module would need its own sourced specification.",
            unit="", fix=fix))
    elif position_problem:
        fix = add_fix("dial.date_window_position", str(date_position),
                      "Match the documented date position.")
        checks.append(_result(
            "complication.alignment", "Complication alignment", "FAIL",
            f"Dial date position {dial_position} does not match the movement's "
            f"documented position {date_position}.", unit="", fix=fix))
    elif unknown_support:
        checks.append(_result(
            "complication.alignment", "Complication alignment", "UNKNOWN",
            "Support is not published for: " + ", ".join(unknown_support) +
            ". These are not assumed to work.", unit="", source="unknown"))
    elif date_position is None and dial_date:
        checks.append(_result(
            "complication.alignment", "Complication alignment", "UNKNOWN",
            "Date support is documented, but the date position is not published; "
            "radial date alignment cannot be checked.", unit="", source="unknown"))
    else:
        checks.append(_result(
            "complication.alignment", "Complication alignment", "PASS",
            "Every requested complication is listed as supported by the movement record.",
            unit=""))

    # 9. Caseback clearance ------------------------------------------------
    if movement_t is None:
        checks.append(_result(
            "caseback.clearance", "Caseback clearance", "UNKNOWN",
            "Movement thickness is not available.", source="unknown"))
    else:
        caseback_h = _number(stack.get("caseback_mm"), 1.2)
        required = caseback_h + movement_t + CASEBACK_CLEARANCE_MM
        have = case_t if case_t is not None else 0.0
        margin = have - required
        status = _status_from_margin(margin, pass_at=0.0, warn_at=-0.5)
        fix = None
        if status == "FAIL":
            fix = add_fix("case.thickness_mm", round(required + 1.0, 1),
                          "The movement would pass through the caseback.")
        checks.append(_result(
            "caseback.clearance", "Caseback clearance", status,
            f"Caseback {caseback_h:.2f} mm + movement {movement_t:.2f} mm + "
            f"{CASEBACK_CLEARANCE_MM:.2f} mm clearance = {required:.2f} mm of "
            f"{have:.2f} mm case: {margin:+.2f} mm.",
            measured=round(margin, 3), limit=0.0, fix=fix))

    overall = _overall(checks)
    counts = {status: sum(1 for check in checks if check["status"] == status)
              for status in ("PASS", "WARNING", "FAIL", "UNKNOWN")}
    return {
        "overall": overall,
        "counts": counts,
        "checks": checks,
        "suggested_changes": fixes,
        "assumptions": {
            "case_wall_mm": CASE_WALL_MM,
            "min_radial_clearance_mm": MIN_RADIAL_CLEARANCE_MM,
            "target_radial_clearance_mm": TARGET_RADIAL_CLEARANCE_MM,
            "min_stack_margin_mm": MIN_STACK_MARGIN_MM,
            "min_hand_clearance_mm": MIN_HAND_CLEARANCE_MM,
            "stem_tolerance_mm": STEM_TOLERANCE_MM,
            "stem_warning_mm": STEM_WARNING_MM,
            "caseback_clearance_mm": CASEBACK_CLEARANCE_MM,
            "note": ("Fit rules are explicit design assumptions, not a tolerance "
                     "stack or a solid interference check. Unknown means the "
                     "manufacturer does not publish the datum."),
        },
        "provenance": {
            "movement": record.get("id"),
            "movement_status": record.get("status"),
            "movement_verified_at": record.get("verified_at"),
            "geometry": "solved spec + movement record",
            "physically_verified": False,
        },
    }


# ---------------------------------------------------------------------------
# Envelope preview
# ---------------------------------------------------------------------------
def envelope(spec: Spec, record: dict) -> dict:
    case_d = _number(spec.get("case.diameter_mm"), 0.0)
    case_t = _number(spec.get("case.thickness_mm"), 0.0)
    movement_d = _number(record.get("overall_diameter_mm") or record.get("diameter_mm"), 0.0)
    movement_t = _number(record.get("thickness_mm"), 0.0)
    bore = max(0.0, case_d - 2 * CASE_WALL_MM)
    return {
        "case_diameter_mm": round(case_d, 2),
        "case_thickness_mm": round(case_t, 2),
        "case_bore_mm": round(bore, 2),
        "movement_diameter_mm": round(movement_d, 2),
        "movement_thickness_mm": round(movement_t, 2),
        "radial_clearance_mm": round((bore - movement_d) / 2, 3) if bore and movement_d else None,
        "caseback_mm": round(_number((spec.get("derived.stack") or {}).get("caseback_mm"), 1.2), 2),
        "scale": "case and movement shown to the same millimetre scale",
        "note": "2D envelope only; the movement is not meshed as a solid.",
    }


# ---------------------------------------------------------------------------
# Spec proposal (never applied here)
# ---------------------------------------------------------------------------
def propose(spec: Spec, movement_id: str) -> dict:
    """Build a validated change set for adopting a movement record.

    Returns ``variant.changes`` for the movement identity and
    ``suggested_changes`` for the fit corrections separately.  Applying either
    set goes through the normal solver/verifier pipeline after the user
    confirms; this function does not mutate ``spec``.
    """
    record = get_record(movement_id)
    if record is None:
        raise KeyError(f"unknown movement {movement_id!r}")
    highest = max((value for value in (record.get("hand_fitting") or {}).values()
                   if isinstance(value, (int, float))), default=None)
    target = {
        "movement.kind": "mechanical",
        "movement.manufacturer": record.get("manufacturer"),
        "movement.base_calibre": record.get("calibre"),
        "movement.calibre_id": record.get("id"),
        "movement.width_mm": record.get("overall_diameter_mm") or record.get("diameter_mm"),
        "movement.height_mm": record.get("thickness_mm"),
        "movement.beat_rate_bph": record.get("frequency_bph"),
        "movement.power_reserve_h": record.get("power_reserve_h"),
        "movement.jewels": record.get("jewels"),
        "movement.winding": record.get("winding") if record.get("winding") in ("automatic", "manual") else None,
        "movement.hand_height_mm": round(highest, 2) if highest is not None else None,
    }
    changes: list[dict] = []
    locked: list[dict] = []
    notes: list[str] = []
    for path, value in target.items():
        if value is None:
            continue
        if path in spec.locks:
            locked.append({"path": path, "reason": "field is locked"})
            continue
        before = spec.get(path)
        if before != value:
            changes.append({"path": path, "from": before, "to": value,
                            "kind": "movement",
                            "note": f"{record['name']} specification"})
    # Complications: keep only what the record documents as supported.  The
    # user can still add a module later, but it would need its own sourced
    # specification.
    current_comps = dict(spec.get("movement.complications") or {})
    kept = {key: value for key, value in current_comps.items()
            if (record.get("support") or {}).get(key) is not False}
    removed = sorted(set(current_comps) - set(kept))
    if kept != current_comps and "movement.complications" not in spec.locks:
        changes.append({"path": "movement.complications",
                        "from": current_comps, "to": kept, "kind": "movement",
                        "note": "remove complications this calibre does not provide"})
        notes.append("This calibre does not provide: " + ", ".join(removed) +
                     ". They are removed in the proposed change set.")
    if "movement.complications" in spec.locks and kept != current_comps:
        locked.append({"path": "movement.complications", "reason": "field is locked"})
    if spec.get("dial.date_window") and (record.get("support") or {}).get("date") is False:
        if "dial.date_window" not in spec.locks:
            changes.append({"path": "dial.date_window", "from": True, "to": False,
                            "kind": "movement",
                            "note": "this movement has no date display"})
    date_position = (record.get("dial") or {}).get("date_position")
    if date_position is not None and spec.get("dial.date_window"):
        if str(spec.get("dial.date_window_position")) != str(date_position) \
                and "dial.date_window_position" not in spec.locks:
            changes.append({"path": "dial.date_window_position",
                            "from": spec.get("dial.date_window_position"),
                            "to": str(date_position), "kind": "movement",
                            "note": "match the movement's documented date position"})

    fit = compatibility(spec, record)
    identity = {"label": f"Fit {record['name']}",
                "rationale": (f"{record['manufacturer']} {record['calibre']} "
                              f"({record['type']}, {record.get('diameter_mm')} × "
                              f"{record.get('thickness_mm')} mm)."),
                "changes": changes}
    diff = []
    for change in changes + fit["suggested_changes"]:
        diff.append({"path": change["path"], "from": change.get("from"),
                     "to": change.get("to"), "kind": change.get("kind"),
                     "reason": change.get("reason") or change.get("note")})
    return {
        "movement": record,
        "provenance": field_provenance(record),
        "missing": missing_fields(record),
        "compatibility": fit,
        "envelope": envelope(spec, record),
        "variant": identity,
        "suggested_changes": fit["suggested_changes"],
        "spec_diff": diff,
        "locked": locked,
        "notes": notes,
        "provenance_note": ("The proposal is a diff only. Nothing is applied until "
                            "the change set is confirmed and run through the solver, "
                            "mesh builder and verifier."),
    }
