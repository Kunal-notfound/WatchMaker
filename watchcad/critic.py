"""Deterministic five-axis design critic.

The critic is a rubric, not an oracle.  Every metric is computed from the solved
specification, the generated scene and the verification checks; no language
model supplies a score, and nothing here mutates the design.  Two axes are
explicitly estimates, and every axis states its basis so the UI can keep
calculated, estimated and tested results separate.

Axes
----
proportions          calculated ratio envelopes and stack margins
dial legibility      estimated contrast / index / lume heuristic
aesthetics           estimated coherence heuristic (not a claim about taste)
ergonomics           calculated fit, mass and handling envelope
component consistency calculated cross-field and verification consistency
"""

from __future__ import annotations

import json
import math

from .components import (
    component_for_check, component_for_path, component_scope, resolve_component,
)
from .modules import MODULES, compose
from .parts import DIAL_COLORS, dial_color
from .schema import FIELDS

AXIS_ORDER = ("proportions", "dial_legibility", "aesthetics", "ergonomics",
              "component_consistency")

STANDARD_LUG_WIDTHS = (16, 18, 19, 20, 21, 22, 24)

_HANDS_FOR_INDEX = {
    "applied batons": ("baton", "dauphine", "syringe"),
    "applied numerals": ("leaf", "dauphine", "baton"),
    "roman numerals": ("leaf", "baton"),
    "printed": ("baton", "dauphine", "syringe", "leaf"),
    "no indices": ("baton", "dauphine", "skeleton"),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _num(value, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value, digits: int = 2):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _band(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "good"
    if score >= 55:
        return "fair"
    return "weak"


def _metric(label: str, value, unit: str = "", provenance: str = "calculated",
            note: str = "") -> dict:
    return {"label": label, "value": _round(value), "unit": unit,
            "provenance": provenance, "note": note}


def _finding(kind: str, text: str, *, check_id: str = "",
             confidence: str = "calculated", path: str = "") -> dict:
    return {"kind": kind, "text": text, "check_id": check_id or None,
            "confidence": confidence, "path": path or None}


def _safe_fix(path: str, value, label: str, reason: str, *,
              current=None, confidence: str = "calculated") -> dict | None:
    """Return a schema-valid, non-locked fix, or None."""
    meta = FIELDS.get(path)
    if not meta:
        return None
    kind = meta.get("kind")
    if kind in ("num", "int"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if kind == "int":
            number = int(round(number))
        low, high = meta.get("min"), meta.get("max")
        if low is not None:
            number = max(float(low), number)
        if high is not None:
            number = min(float(high), number)
        if kind == "int":
            number = int(number)
        else:
            number = round(number, 2)
        value = number
    elif kind == "enum":
        if value not in (meta.get("choices") or []):
            return None
    elif kind == "bool":
        value = bool(value)
    elif kind == "dict":
        if not isinstance(value, dict):
            return None
    elif kind == "str":
        value = str(value)
    if current is not None and value == current:
        return None
    owner = component_for_path(path)
    return {
        "path": path,
        "value": value,
        "label": label,
        "reason": reason,
        "current": _round(current),
        "confidence": confidence,
        "component": owner.id if owner else None,
    }


def _check_map(checks: list[dict]) -> dict[str, dict]:
    return {str(check.get("id")): check for check in checks
            if check.get("id")}


def _check_finding(check: dict) -> dict:
    owner = component_for_check(str(check.get("id") or ""))
    return _finding("risk", str(check.get("detail") or check.get("title") or ""),
                    check_id=str(check.get("id") or ""),
                    confidence="calculated",
                    path=(check.get("fix") or {}).get("path") or "")


def _check_fix(check: dict) -> dict | None:
    fix = check.get("fix") or {}
    path = fix.get("path")
    if not path or "value" not in fix:
        return None
    return _safe_fix(path, fix.get("value"),
                     f"Apply the verified fix for {check.get('id')}",
                     str(check.get("detail") or check.get("title") or ""),
                     current=None, confidence="calculated")


def _scene_color(scene: dict, part_name: str, fallback: str) -> str:
    for part in (scene or {}).get("parts", []):
        if str(getattr(part, "name", "")).lower() == part_name.lower():
            return str(getattr(part, "color", "") or fallback)
    return fallback


def _hex_rgb(text: str) -> tuple[int, int, int] | None:
    value = str(text or "").strip()
    if not value.startswith("#"):
        return None
    body = value[1:]
    if len(body) == 3:
        body = "".join(char * 2 for char in body)
    if len(body) != 6:
        return None
    try:
        return tuple(int(body[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def _luminance(text: str) -> float | None:
    rgb = _hex_rgb(text)
    if rgb is None:
        return None
    channels = []
    for value in rgb:
        channel = value / 255.0
        channels.append(channel / 12.92 if channel <= 0.04045
                        else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float | None:
    """WCAG relative-luminance contrast, or None for an unparseable colour."""
    a, b = _luminance(first), _luminance(second)
    if a is None or b is None:
        return None
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def _axis(axis_id: str, label: str, confidence: str, score: float,
          summary: str, metrics: list[dict], findings: list[dict],
          fixes: list[dict]) -> dict:
    return {
        "id": axis_id,
        "label": label,
        "confidence": confidence,
        "score": int(round(_clamp(score))),
        "band": _band(score),
        "summary": summary,
        "metrics": metrics,
        "findings": findings,
        "fixes": fixes,
    }


# ---------------------------------------------------------------------------
# Axis 1 — proportions (calculated)
# ---------------------------------------------------------------------------

def _proportions(spec, checks, scene) -> dict:
    d = _num(spec.get("case.diameter_mm"), 40.0)
    t = _num(spec.get("case.thickness_mm"), 11.0)
    l2l = _num(spec.get("case.lug_to_lug_mm"), 48.0)
    lug_w = _num(spec.get("case.lug_width_mm"), 20.0)
    dial_d = _num(spec.get("dial.diameter_mm"), 32.0)
    stack = spec.get("derived.stack") or {}
    margin = _num(stack.get("margin_mm"), 0.0)
    required = _num(stack.get("required_case_thickness_mm"), 0.0)
    dial_r = dial_d / 2 if dial_d else 0.0
    hour = _num(spec.get("hands.hour_length_mm"))
    minute = _num(spec.get("hands.minute_length_mm"))
    second = _num(spec.get("hands.second_length_mm"))

    diameter_to_thickness = d / t if t else 0.0
    l2l_ratio = l2l / d if d else 0.0
    dial_ratio = dial_d / d if d else 0.0
    lug_ratio = lug_w / d if d else 0.0
    hour_ratio = hour / dial_r if dial_r else 0.0
    minute_ratio = minute / dial_r if dial_r else 0.0
    second_ratio = second / dial_r if dial_r else 0.0

    metrics = [
        _metric("Case diameter", d, "mm"),
        _metric("Case thickness", t, "mm"),
        _metric("Lug-to-lug", l2l, "mm"),
        _metric("Diameter / thickness", diameter_to_thickness, "ratio"),
        _metric("Lug-to-lug / diameter", l2l_ratio, "ratio"),
        _metric("Dial / case", dial_ratio, "ratio"),
        _metric("Lug width / diameter", lug_ratio, "ratio"),
        _metric("Hour hand / dial radius", hour_ratio, "ratio"),
        _metric("Minute hand / dial radius", minute_ratio, "ratio"),
        _metric("Second hand / dial radius", second_ratio, "ratio"),
        _metric("Stack margin", margin, "mm",
                note="available case thickness minus the required stack"),
    ]
    findings: list[dict] = []
    fixes: list[dict] = []
    score = 100.0

    if diameter_to_thickness and diameter_to_thickness < 2.8:
        score -= 10
        findings.append(_finding(
            "risk", f"The case is thick for its diameter ({diameter_to_thickness:.2f}:1); "
                    "check the wrist presence and side profile."))
    elif diameter_to_thickness > 6.0:
        score -= 6
        findings.append(_finding(
            "note", f"The case is very thin for its diameter ({diameter_to_thickness:.2f}:1); "
                    "make sure the stack and crystal still close."))
    else:
        findings.append(_finding(
            "strength", f"Diameter-to-thickness is {diameter_to_thickness:.2f}:1, inside "
                        "the usual engineering envelope."))

    if l2l_ratio > 1.30:
        score -= 10
        findings.append(_finding(
            "risk", f"Lug-to-lug is {l2l_ratio:.2f}x the diameter; it will overhang "
                    "many wrists."))
    elif l2l_ratio > 1.24:
        score -= 4
        findings.append(_finding(
            "note", f"Lug-to-lug is {l2l_ratio:.2f}x the diameter; check the wrist fit."))
    elif l2l_ratio < 1.02:
        score -= 8
        findings.append(_finding(
            "risk", f"Lug-to-lug is only {l2l_ratio:.2f}x the diameter; the strap may "
                    "not clear the case."))
    else:
        findings.append(_finding(
            "strength", f"Lug-to-lug is {l2l_ratio:.2f}x the diameter."))

    if dial_ratio < 0.82:
        score -= 8
        findings.append(_finding(
            "note", f"The dial fills {dial_ratio:.0%} of the case; the bezel/rehaut is "
                    "visually dominant."))
    elif dial_ratio > 0.94:
        score -= 6
        findings.append(_finding(
            "risk", f"The dial fills {dial_ratio:.0%} of the case; the opening and "
                    "rehaut clearance are tight."))
    else:
        findings.append(_finding(
            "strength", f"The dial fills {dial_ratio:.0%} of the case, a balanced opening."))

    if lug_ratio < 0.40 or lug_ratio > 0.58:
        score -= 6
        findings.append(_finding(
            "note", f"Lug width is {lug_ratio:.2f}x the diameter; it sits outside the "
                    "usual strap-proportion range."))

    if margin < 0:
        score -= 20
        findings.append(_finding(
            "risk", f"The vertical stack is {abs(margin):.2f} mm too tall for the case."))
        fix = _safe_fix("case.thickness_mm", round(required + 0.8, 1),
                        "Make the case contain the stack",
                        f"Required {required:.2f} mm plus 0.80 mm clearance.",
                        current=t)
        if fix:
            fixes.append(fix)
    elif margin < 0.5:
        score -= 10
        findings.append(_finding(
            "risk", f"Only {margin:.2f} mm of stack margin remains; tolerance and "
                    "assembly variation can consume it."))
        fix = _safe_fix("case.thickness_mm", round(required + 0.8, 1),
                        "Restore a stack margin",
                        f"Required {required:.2f} mm plus 0.80 mm clearance.",
                        current=t)
        if fix:
            fixes.append(fix)
    elif margin > 5.0:
        score -= 4
        findings.append(_finding(
            "note", f"{margin:.2f} mm of unused internal height may be adding visual "
                    "thickness without a functional benefit."))
    else:
        findings.append(_finding(
            "strength", f"The stack closes with {margin:.2f} mm of margin."))

    for label, value, ratio, low, high in (
            ("hour", hour, hour_ratio, 0.50, 0.72),
            ("minute", minute, minute_ratio, 0.82, 1.00),
            ("second", second, second_ratio, 0.86, 1.05)):
        if ratio < low or ratio > high:
            score -= 8 if label != "second" else 5
            findings.append(_finding(
                "note", f"The {label} hand reaches {ratio:.2f}x the dial radius "
                        f"(reference range {low:.2f}-{high:.2f})."))

    return _axis(
        "proportions", "Proportions", "calculated", score,
        "Calculated ratio envelopes and the vertical stack; it does not judge taste.",
        metrics, findings, fixes)


# ---------------------------------------------------------------------------
# Axis 2 — dial legibility (estimated)
# ---------------------------------------------------------------------------

def _legibility(spec, checks, scene) -> dict:
    dial_d = _num(spec.get("dial.diameter_mm"), 32.0)
    style = str(spec.get("dial.index_style") or "")
    has_indices = style != "no indices"
    lume = bool(spec.get("dial.lume"))
    date_window = bool(spec.get("dial.date_window"))
    dial_hex = _scene_color(scene, "dial", dial_color(spec.get("dial.color")))
    if style in ("printed", "roman numerals", "applied numerals"):
        fallback_index = "#d8d3c4"
    else:
        fallback_index = "#e9edf2"
    index_hex = _scene_color(scene, "index", fallback_index) if has_indices else ""
    hand_hex = _scene_color(scene, "hour_hand", "#e9edf2")
    index_contrast = contrast_ratio(dial_hex, index_hex) if index_hex else None
    hand_contrast = contrast_ratio(dial_hex, hand_hex)
    effective = index_contrast if index_contrast is not None else hand_contrast

    metrics = [
        _metric("Dial diameter", dial_d, "mm"),
        _metric("Index style", style or "—", "", provenance="estimated"),
        _metric("Indices", 12 if has_indices else 0, "count", "estimated"),
        _metric("Dial colour", dial_hex, "", "estimated"),
        _metric("Index colour", index_hex or "—", "", "estimated"),
        _metric("Hand colour", hand_hex, "", "estimated"),
        _metric("Dial / index contrast", index_contrast, ":1",
                "estimated", "WCAG relative-luminance ratio; not a real-world lighting test"),
        _metric("Dial / hand contrast", hand_contrast, ":1", "estimated"),
        _metric("Lume", "yes" if lume else "no", "", "estimated"),
        _metric("Date window", "yes" if date_window else "no", "", "estimated"),
    ]
    findings: list[dict] = []
    fixes: list[dict] = []
    score = 100.0

    if not has_indices:
        score -= 25
        findings.append(_finding(
            "risk", "There are no hour indices; reading the time depends entirely on "
                    "the hands against the dial.", confidence="estimated"))
        fix = _safe_fix("dial.index_style", "applied batons",
                        "Add applied baton indices",
                        "Indices give fixed reference points for the hands.",
                        current=spec.get("dial.index_style"),
                        confidence="estimated")
        if fix:
            fixes.append(fix)
    elif effective is not None:
        if effective >= 4.5:
            findings.append(_finding(
                "strength", f"Dial-to-index contrast is {effective:.1f}:1, above the "
                            "usual 4.5:1 guideline.", confidence="estimated"))
        elif effective >= 3.0:
            score -= 10
            findings.append(_finding(
                "risk", f"Dial-to-index contrast is {effective:.1f}:1; legibility is "
                        "marginal in low light.", confidence="estimated"))
        else:
            score -= 25
            findings.append(_finding(
                "risk", f"Dial-to-index contrast is only {effective:.1f}:1; the indices "
                        "can disappear against the dial.", confidence="estimated"))
        if effective < 4.5:
            candidate = _contrast_dial_candidate(dial_hex, index_hex or hand_hex)
            if candidate:
                fix = _safe_fix("dial.color", candidate,
                                "Raise dial/indicator contrast",
                                "Choose the preset with the highest computed contrast "
                                "against the modelled indicators.",
                                current=spec.get("dial.color"),
                                confidence="estimated")
                if fix:
                    fixes.append(fix)

    if hand_contrast is not None and hand_contrast < 3.0:
        score -= 20
        findings.append(_finding(
            "risk", f"Dial-to-hand contrast is only {hand_contrast:.1f}:1.",
            confidence="estimated"))
        if effective is None or effective >= 3.0:
            candidate = _contrast_dial_candidate(dial_hex, hand_hex)
            if candidate:
                fix = _safe_fix("dial.color", candidate,
                                "Raise dial/hand contrast",
                                "Choose the preset with the highest computed contrast "
                                "against the modelled hands.",
                                current=spec.get("dial.color"),
                                confidence="estimated")
                if fix:
                    fixes.append(fix)

    if not lume:
        score -= 8
        findings.append(_finding(
            "note", "The dial has no lume; low-light legibility depends on ambient light.",
            confidence="estimated"))
        fix = _safe_fix("dial.lume", True, "Add lume",
                        "Lume improves low-light legibility if the design brief allows it.",
                        current=spec.get("dial.lume"), confidence="estimated")
        if fix:
            fixes.append(fix)

    if dial_d and dial_d < 30:
        score -= 8
        findings.append(_finding(
            "note", f"A {dial_d:.1f} mm dial is small; crowded indices or a date window "
                    "reduce legibility.", confidence="estimated"))

    if style == "printed":
        findings.append(_finding(
            "note", "Printed indices are flatter and usually less legible at an angle "
                    "than applied markers.", confidence="estimated"))

    return _axis(
        "dial_legibility", "Dial legibility", "estimated", score,
        "A heuristics-only estimate from modelled colours, indices and lume. It is "
        "not a lighting test and not a substitute for a physical sample.",
        metrics, findings, fixes)


def _contrast_dial_candidate(current_hex: str, marker_hex: str) -> str | None:
    best = None
    best_ratio = 0.0
    for name in DIAL_COLORS:
        candidate = dial_color(name)
        if candidate == current_hex:
            continue
        ratio = contrast_ratio(candidate, marker_hex)
        if ratio is None:
            continue
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    return best if best_ratio >= 4.5 else None


# ---------------------------------------------------------------------------
# Axis 3 — aesthetic coherence (estimated)
# ---------------------------------------------------------------------------

def _aesthetics(spec, checks, scene) -> dict:
    index_style = str(spec.get("dial.index_style") or "")
    hands_style = str(spec.get("hands.style") or "")
    bezel = str(spec.get("case.bezel") or "")
    wr = int(_num(spec.get("case.water_resistance_m")))
    comps = compose(spec.get("movement.complications") or {})
    case_material = str(spec.get("case.material") or "")
    case_finish = str(spec.get("case.finish") or "")
    dial_finish = str(spec.get("dial.finish") or "")
    dial_hex = _scene_color(scene, "dial", dial_color(spec.get("dial.color")))
    case_hex = _scene_color(scene, "case_mid", "#c9ced6")
    hand_hex = _scene_color(scene, "hour_hand", "#e9edf2")
    index_hex = _scene_color(scene, "index", "#e9edf2")
    palette = []
    for value in (case_hex, dial_hex, hand_hex, index_hex):
        if value and value.lower() not in [item.lower() for item in palette]:
            palette.append(value)

    metrics = [
        _metric("Index style", index_style or "—", "", "estimated"),
        _metric("Hands style", hands_style or "—", "", "estimated"),
        _metric("Case finish", case_finish or "—", "", "estimated"),
        _metric("Dial finish", dial_finish or "—", "", "estimated"),
        _metric("Bezel", bezel or "—", "", "estimated"),
        _metric("Palette colours", len(palette), "count", "estimated",
                note=", ".join(palette)),
        _metric("Complication modules", len(comps["ids"]), "count", "estimated"),
    ]
    findings: list[dict] = []
    fixes: list[dict] = []
    score = 100.0

    compatible = _HANDS_FOR_INDEX.get(index_style)
    if compatible:
        if hands_style in compatible:
            findings.append(_finding(
                "strength", f"{hands_style.title()} hands are coherent with "
                            f"{index_style} indices.", confidence="estimated"))
        else:
            score -= 8
            findings.append(_finding(
                "risk", f"{hands_style.title()} hands do not share a visual language "
                        f"with {index_style} indices.", confidence="estimated"))
            fix = _safe_fix("hands.style", compatible[0],
                            "Match the hands to the indices",
                            f"The curated {index_style} pairing includes "
                            f"{', '.join(compatible)}.",
                            current=spec.get("hands.style"), confidence="estimated")
            if fix:
                fixes.append(fix)

    if len(palette) > 3:
        score -= 6
        findings.append(_finding(
            "note", f"The rendered palette uses {len(palette)} distinct colours; more "
                    "than three can read as busy.", confidence="estimated"))
    else:
        findings.append(_finding(
            "strength", f"The rendered palette uses {len(palette)} distinct "
                        f"colour{'s' if len(palette) != 1 else ''}.", confidence="estimated"))

    if len(comps["ids"]) > 2:
        score -= 6
        findings.append(_finding(
            "note", f"{len(comps['ids'])} complication modules add visual furniture; "
                    "check that the dial still has a clear hierarchy.",
            confidence="estimated"))

    if bezel == "dive" and wr < 100:
        score -= 8
        findings.append(_finding(
            "risk", "A dive bezel is fitted to a watch rated below 100 m; the visual "
                    "promise and the construction disagree.", confidence="estimated"))
        fix = _safe_fix("case.bezel", "plain", "Match the bezel to the water resistance",
                        "A plain bezel does not promise dive capability.",
                        current=spec.get("case.bezel"), confidence="estimated")
        if fix:
            fixes.append(fix)

    if case_finish == "two-tone" and case_material == "titanium":
        score -= 4
        findings.append(_finding(
            "note", "Two-tone finishing on a titanium case is unusual; confirm the "
                    "colour contrast is intentional.", confidence="estimated"))
    if case_finish == "PVD black" and case_material == "bronze":
        score -= 4
        findings.append(_finding(
            "note", "A PVD-black finish over a bronze case hides the material's patina; "
                    "confirm the intent.", confidence="estimated"))

    if dial_finish == "guilloche" and index_style == "printed":
        findings.append(_finding(
            "note", "Printing over a guilloche dial can reduce pattern definition; this "
                    "is an estimate, not a manufacturing verdict.", confidence="estimated"))

    return _axis(
        "aesthetics", "Aesthetic coherence", "estimated", score,
        "A deterministic coherence heuristic. It is not an AI taste claim, a trend "
        "judgement, or evidence that people will prefer the design.",
        metrics, findings, fixes)


# ---------------------------------------------------------------------------
# Axis 4 — ergonomics (calculated)
# ---------------------------------------------------------------------------

def _ergonomics(spec, checks, scene) -> dict:
    d = _num(spec.get("case.diameter_mm"), 40.0)
    t = _num(spec.get("case.thickness_mm"), 11.0)
    l2l = _num(spec.get("case.lug_to_lug_mm"), 48.0)
    lug_w = _num(spec.get("case.lug_width_mm"), 20.0)
    crown_d = _num(spec.get("case.crown_diameter_mm"), 5.5)
    mass = _num(scene.get("mass_g") or spec.get("derived.mass_g"), 0.0)
    stack = spec.get("derived.stack") or {}
    margin = _num(stack.get("margin_mm"), 0.0)
    required = _num(stack.get("required_case_thickness_mm"), 0.0)
    material = str(spec.get("case.material") or "")

    metrics = [
        _metric("Case diameter", d, "mm"),
        _metric("Case thickness", t, "mm"),
        _metric("Lug-to-lug", l2l, "mm"),
        _metric("Lug width", lug_w, "mm"),
        _metric("Crown diameter", crown_d, "mm"),
        _metric("Estimated mass", mass, "g", "estimated",
                "Envelope estimate from volume and material density"),
    ]
    findings: list[dict] = []
    fixes: list[dict] = []
    score = 100.0

    if l2l > 55:
        score -= 20
        findings.append(_finding(
            "risk", f"A {l2l:.1f} mm lug-to-lug will overhang a typical wrist."))
        fix = _safe_fix("case.lug_to_lug_mm", 52.0, "Shorten the lug-to-lug",
                        "Keep the lugs closer to the wrist without changing the case bore.",
                        current=spec.get("case.lug_to_lug_mm"))
        if fix:
            fixes.append(fix)
    elif l2l > 52:
        score -= 10
        findings.append(_finding(
            "risk", f"A {l2l:.1f} mm lug-to-lug is large for daily wear."))
        fix = _safe_fix("case.lug_to_lug_mm", 50.0, "Shorten the lug-to-lug",
                        "Reduce overhang while keeping the design intent.",
                        current=spec.get("case.lug_to_lug_mm"))
        if fix:
            fixes.append(fix)
    elif l2l < 42:
        score -= 4
        findings.append(_finding(
            "note", f"A {l2l:.1f} mm lug-to-lug is short; check strap clearance."))
    else:
        findings.append(_finding(
            "strength", f"Lug-to-lug is {l2l:.1f} mm, within a usual daily-wear range."))

    if d > 46:
        score -= 10
        findings.append(_finding("risk", f"A {d:.1f} mm case is oversized for many wrists."))
    elif d > 44:
        score -= 5
        findings.append(_finding(
            "note", f"A {d:.1f} mm case is large; check the intended wearer."))
    elif d < 34:
        score -= 4
        findings.append(_finding("note", f"A {d:.1f} mm case is small for a sports watch."))
    else:
        findings.append(_finding("strength", f"The {d:.1f} mm case diameter is broadly wearable."))

    if t > 16:
        score -= 15
        findings.append(_finding("risk", f"A {t:.1f} mm case is very thick for daily wear."))
    elif t > 14:
        score -= 8
        findings.append(_finding("risk", f"A {t:.1f} mm case is thick for a shirt cuff."))
    elif t > 13:
        score -= 3
        findings.append(_finding("note", f"A {t:.1f} mm case is on the thicker side."))
    elif t < 8:
        score -= 4
        findings.append(_finding("note", f"A {t:.1f} mm case is very thin; check rigidity."))
    else:
        findings.append(_finding("strength", f"The {t:.1f} mm thickness is a practical envelope."))

    if t > 14 and margin > 0.8:
        target = round(max(required + 0.8, 8.0), 1)
        fix = _safe_fix("case.thickness_mm", target,
                        "Remove unused case height",
                        f"The stack needs {required:.2f} mm; keep 0.80 mm clearance.",
                        current=spec.get("case.thickness_mm"))
        if fix:
            fixes.append(fix)

    if mass > 120:
        score -= 20
        findings.append(_finding(
            "risk", f"The envelope estimate is {mass:.0f} g, heavy for a wristwatch.",
            confidence="estimated"))
    elif mass > 100:
        score -= 10
        findings.append(_finding(
            "risk", f"The envelope estimate is {mass:.0f} g; check the long-term comfort.",
            confidence="estimated"))
    elif mass > 85:
        score -= 4
        findings.append(_finding(
            "note", f"The envelope estimate is {mass:.0f} g.", confidence="estimated"))
    elif mass:
        findings.append(_finding(
            "strength", f"The envelope estimate is {mass:.0f} g.", confidence="estimated"))

    if mass > 100 and material == "316L stainless steel":
        fix = _safe_fix("case.material", "titanium",
                        "Consider a titanium case",
                        "Titanium reduces the envelope estimate; re-solve for the actual mass.",
                        current=spec.get("case.material"), confidence="estimated")
        if fix:
            fixes.append(fix)
    elif mass > 100 and material == "bronze":
        fix = _safe_fix("case.material", "titanium",
                        "Consider a lighter case material",
                        "Bronze is dense; titanium reduces the envelope estimate.",
                        current=spec.get("case.material"), confidence="estimated")
        if fix:
            fixes.append(fix)

    if crown_d < 4.5:
        score -= 8
        findings.append(_finding(
            "note", f"A {crown_d:.1f} mm crown is small and hard to grip."))
        fix = _safe_fix("case.crown_diameter_mm", 5.5, "Increase the crown diameter",
                        "A 5.5 mm crown is easier to grip without dominating the case.",
                        current=spec.get("case.crown_diameter_mm"))
        if fix:
            fixes.append(fix)
    elif crown_d > 8:
        score -= 6
        findings.append(_finding(
            "note", f"A {crown_d:.1f} mm crown may dig into the wrist."))
        fix = _safe_fix("case.crown_diameter_mm", 7.0, "Reduce the crown diameter",
                        "A 7.0 mm crown keeps the grip while reducing protrusion.",
                        current=spec.get("case.crown_diameter_mm"))
        if fix:
            fixes.append(fix)

    if lug_w not in STANDARD_LUG_WIDTHS:
        score -= 6
        findings.append(_finding(
            "note", f"A {lug_w:g} mm lug width is not a standard strap size."))
        target = min(STANDARD_LUG_WIDTHS, key=lambda value: abs(value - lug_w))
        fix = _safe_fix("case.lug_width_mm", target,
                        "Use a standard lug width",
                        f"Nearest supported strap width is {target} mm.",
                        current=spec.get("case.lug_width_mm"))
        if fix:
            fixes.append(fix)

    return _axis(
        "ergonomics", "Ergonomics", "calculated", score,
        "Calculated fit and handling envelopes. Mass is an envelope estimate, "
        "not a measured weight.",
        metrics, findings, fixes)


# ---------------------------------------------------------------------------
# Axis 5 — component consistency (calculated)
# ---------------------------------------------------------------------------

def _consistency(spec, checks, scene) -> dict:
    failures = [check for check in checks if check.get("passed") is False]
    comps = compose(spec.get("movement.complications") or {})
    date_modules = [module for module in
                    ("date", "day-date", "annual-calendar", "perpetual-calendar")
                    if module in comps["ids"]]
    date_window = bool(spec.get("dial.date_window"))
    small_seconds = bool(spec.get("dial.small_seconds"))
    kind = str(spec.get("movement.kind") or "")
    bezel = str(spec.get("case.bezel") or "")
    wr = int(_num(spec.get("case.water_resistance_m")))
    stack = spec.get("derived.stack") or {}
    margin = _num(stack.get("margin_mm"), 0.0)
    clearance = _num(stack.get("z_crystal_bottom")) - _num(stack.get("z_hands_top"))

    metrics = [
        _metric("Failing checks", len(failures), "count"),
        _metric("Complication modules", len(comps["ids"]), "count"),
        _metric("Date mechanism", "yes" if date_modules else "no", ""),
        _metric("Dial date window", "yes" if date_window else "no", ""),
        _metric("Stack margin", margin, "mm"),
        _metric("Hand/crystal clearance", clearance, "mm"),
        _metric("Movement kind", kind or "—", ""),
    ]
    findings: list[dict] = []
    fixes: list[dict] = []
    score = 100.0

    if failures:
        score -= min(60, 12 * len(failures))
        for check in failures[:6]:
            findings.append(_check_finding(check))
            fix = _check_fix(check)
            if fix:
                fixes.append(fix)
    else:
        findings.append(_finding(
            "strength", "Every deterministic verification check currently passes."))

    if date_window and not date_modules:
        score -= 12
        findings.append(_finding(
            "risk", "The dial has a date window but no date mechanism is selected.",
            confidence="calculated", path="dial.date_window"))
        current = dict(spec.get("movement.complications") or {})
        module_height = sum(MODULES[module]["height_mm"] for module in ("date",))
        if margin >= module_height + 0.3:
            candidate = dict(current)
            candidate["date"] = {}
            fix = _safe_fix("movement.complications", candidate,
                            "Add the date module",
                            "The stack has room for the 0.45 mm date module.",
                            current=current)
            if fix:
                fixes.append(fix)
        fix = _safe_fix("dial.date_window", False,
                        "Remove the date window",
                        "Hide the opening if no date mechanism is wanted.",
                        current=spec.get("dial.date_window"))
        if fix:
            fixes.append(fix)

    if date_modules and not date_window:
        score -= 8
        findings.append(_finding(
            "risk", f"The {', '.join(date_modules)} module has no dial opening to show it.",
            confidence="calculated", path="movement.complications"))
        fix = _safe_fix("dial.date_window", True, "Open the date window",
                        "The selected date mechanism needs a dial opening.",
                        current=spec.get("dial.date_window"))
        if fix:
            fixes.append(fix)

    if small_seconds:
        score -= 4
        findings.append(_finding(
            "note", "small_seconds is set, but the geometry builder still renders a "
                    "central second hand. This is a model limitation, not a hidden "
                    "pass.", confidence="calculated", path="dial.small_seconds"))

    if kind == "quartz" and comps["ids"]:
        score -= 12
        findings.append(_finding(
            "risk", "The movement is quartz but the complication set contains "
                    "mechanical modules.", confidence="calculated",
            path="movement.complications"))
        fix = _safe_fix("movement.complications", {},
                        "Remove mechanical complications from the quartz design",
                        "The module registry currently models these as mechanical.",
                        current=spec.get("movement.complications"))
        if fix:
            fixes.append(fix)

    if bezel == "dive" and wr < 100:
        score -= 6
        findings.append(_finding(
            "risk", "A dive bezel is paired with a water resistance below 100 m.",
            confidence="calculated", path="case.bezel"))
        fix = _safe_fix("case.bezel", "plain", "Replace the dive bezel",
                        "The construction does not support the dive-bezel promise.",
                        current=spec.get("case.bezel"))
        if fix:
            fixes.append(fix)

    sapphire = str(spec.get("case.crystal") or "") == "sapphire"
    if sapphire and wr >= 100:
        findings.append(_finding(
            "strength", "Sapphire crystal and a sealed case are coherent for the "
                        "stated water resistance.", confidence="calculated"))
    if str(spec.get("case.crown") or "") == "screw-down" and wr >= 200:
        findings.append(_finding(
            "strength", "A screw-down crown matches the deep water-resistance target.",
            confidence="calculated"))

    return _axis(
        "component_consistency", "Component consistency", "calculated", score,
        "Cross-field and verification consistency. It reports what the engine can "
        "prove about fit and construction, not physical performance.",
        metrics, findings, fixes)


# ---------------------------------------------------------------------------
# Public report
# ---------------------------------------------------------------------------

def critique(studio, focus=None) -> dict:
    """Build the deterministic five-axis report.

    ``focus`` is an optional component selection (id, alias, scene part or
    viewer selection).  It only annotates findings and fixes; every axis is
    always evaluated for the whole design so a focused user does not lose the
    context of a failing check elsewhere.
    """
    spec = getattr(studio, "solved", None) or getattr(studio, "spec", None)
    if spec is None:
        raise ValueError("the studio has no solved specification")
    checks = list(getattr(studio, "checks", []) or [])
    scene = getattr(studio, "scene", {}) or {}
    focus_component = resolve_component(focus) if focus else None

    axes = [
        _proportions(spec, checks, scene),
        _legibility(spec, checks, scene),
        _aesthetics(spec, checks, scene),
        _ergonomics(spec, checks, scene),
        _consistency(spec, checks, scene),
    ]

    # Deduplicate fixes by path so the UI cannot apply the same field twice
    # with contradictory values.  Locked fields are reported, never offered.
    seen: set[str] = set()
    locked: list[str] = []
    for axis in axes:
        kept = []
        for fix in axis["fixes"]:
            path = fix["path"]
            if path in spec.locks:
                locked.append(path)
                continue
            if path in seen:
                continue
            seen.add(path)
            owner = component_for_path(path)
            fix["component"] = owner.id if owner else None
            fix["in_focus"] = (None if focus_component is None
                               else owner is not None
                               and owner.id == focus_component.id)
            kept.append(fix)
        axis["fixes"] = kept

    for axis in axes:
        for finding in axis["findings"]:
            check_id = finding.get("check_id") or ""
            owner = component_for_check(check_id) if check_id else None
            if owner is None and finding.get("path"):
                owner = component_for_path(finding["path"])
            finding["component"] = owner.id if owner else None
            finding["in_focus"] = (None if focus_component is None
                                   else owner is not None
                                   and owner.id == focus_component.id)

    overall_score = sum(axis["score"] for axis in axes) / len(axes)
    risk_count = sum(1 for axis in axes for finding in axis["findings"]
                     if finding["kind"] == "risk")
    strength_count = sum(1 for axis in axes for finding in axis["findings"]
                         if finding["kind"] == "strength")
    fix_count = sum(len(axis["fixes"]) for axis in axes)
    notes = [
        "Scores are a deterministic rubric, not a measurement of quality.",
        "Dial legibility and aesthetic coherence are estimates; proportions, "
        "ergonomics and component consistency use calculated values.",
        "No score can replace a physical prototype, a bench test or user research.",
    ]
    if locked:
        notes.append("Locked fields were not offered as fixes: "
                     + ", ".join(sorted(set(locked))) + ".")

    return {
        "kind": "watchcad.deterministic-critic",
        "version": 1,
        "checksum": spec.get("derived.checksum"),
        "axes": axes,
        "overall": {
            "score": int(round(_clamp(overall_score))),
            "band": _band(overall_score),
            "confidence": "mixed",
            "summary": f"{strength_count} strengths, {risk_count} risks, "
                       f"{fix_count} safe fix(es) offered.",
            "note": "Equal-weight average of the five axes. It is not a "
                    "pass/fail badge and it is not a production release gate.",
        },
        "focus": component_scope(focus_component),
        "notes": notes,
    }
