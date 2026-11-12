"""Prompt to spec, without letting a model touch geometry.

The default parser is deterministic and offline: regular expressions and a
vocabulary of watch terms produce RFC-6902-style patches against the spec.  An
optional OpenAI-compatible endpoint can propose patches too, but every patch is
validated against the field registry before it is applied.  The LLM is never
asked for coordinates, meshes or formulas.

`parse()` returns the patched spec, the patches, the questions the UI should ask
for essentials the prompt did not settle, and notes about anything it ignored.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .modules import MODULES, compose
from .schema import ESSENTIAL, FIELDS, Spec

COLORS = {
    "black": "black", "blue": "blue", "navy": "navy blue", "green": "green",
    "forest": "forest green", "white": "white", "silver": "silver", "grey": "grey",
    "gray": "grey", "salmon": "salmon", "champagne": "champagne", "brown": "brown",
    "red": "red", "burgundy": "burgundy", "panda": "panda", "tan": "tan",
    "cream": "cream", "ivory": "ivory", "teal": "teal", "purple": "purple",
    "orange": "orange", "tiffany": "tiffany blue", "slate": "slate",
}

FINISHES = {
    "sunburst": "sunburst", "matte": "matte", "matt": "matte",
    "guilloche": "guilloche", "lacquered": "lacquered", "sandblasted": "sandblasted",
    "fume": "lacquered", "smoked": "lacquered",
}

CASE_MATERIALS = [
    ("stainless", "316L stainless steel"), ("steel", "316L stainless steel"),
    ("316l", "316L stainless steel"), ("titanium", "titanium"),
    ("bronze", "bronze"), ("ceramic", "ceramic"), ("carbon", "carbon"),
    ("forged carbon", "carbon"),
]

CASE_FINISHES = [
    ("bead blasted", "bead-blasted"), ("bead-blasted", "bead-blasted"),
    ("brushed", "brushed"), ("polished", "polished"), ("two-tone", "two-tone"),
    ("two tone", "two-tone"), ("pvd", "PVD black"), ("dlc", "PVD black"),
]

INDEX_STYLES = [
    ("roman", "roman numerals"), ("applied numerals", "applied numerals"),
    ("applied baton", "applied batons"), ("applied indices", "applied batons"),
    ("applied index", "applied batons"), ("printed indices", "printed"),
    ("printed", "printed"), ("no indices", "no indices"),
    ("no markers", "no indices"), ("baton", "applied batons"),
]

HAND_STYLES = [
    ("leaf", "leaf"), ("dauphine", "dauphine"), ("syringe", "syringe"),
    ("skeleton", "skeleton"), ("baton", "baton"), ("stick", "baton"),
    ("sword", "baton"),
]

COMPLICATION_WORDS = {
    "day-date": ["day-date", "day date", "day and date"],
    "annual-calendar": ["annual calendar"],
    "perpetual-calendar": ["perpetual calendar", "perpetual"],
    "minute-repeater": ["minute repeater", "repeater"],
    "power-reserve": ["power reserve indicator", "power reserve display",
                      "power reserve complication"],
    "chronograph": ["chronograph", "chrono", "stopwatch"],
    "moonphase": ["moon phase", "moonphase", "moon-phase"],
    "tourbillon": ["tourbillon"],
    "gmt": ["gmt", "second time zone", "dual time"],
    "date": ["date"],
}

PRESETS = {
    "diver": dict(case__diameter_mm=42.0, case__thickness_mm=13.0, case__water_resistance_m=300,
                  case__bezel="dive", case__crystal="sapphire", case__caseback="screw-down",
                  case__crown="screw-down",
                  dial__index_style="applied batons", dial__lume=True,
                  dial__finish="matte", hands__style="baton", movement__kind="mechanical",
                  movement__winding="automatic"),
    "dress": dict(case__diameter_mm=38.0, case__thickness_mm=10.0, case__bezel="plain",
                  case__material="316L stainless steel", case__finish="polished",
                  dial__color="silver", dial__finish="sunburst",
                  dial__index_style="applied batons", hands__style="dauphine",
                  dial__lume=False, movement__kind="mechanical", movement__winding="manual",
                  movement__power_reserve_h=48.0),
    "field": dict(case__diameter_mm=38.0, case__thickness_mm=10.0, case__finish="bead-blasted",
                  dial__color="black", dial__finish="matte", dial__index_style="applied numerals",
                  dial__lume=True, hands__style="syringe", case__water_resistance_m=100),
    "pilot": dict(case__diameter_mm=43.0, case__thickness_mm=12.0, case__crown_diameter_mm=7.5,
                  dial__color="black", dial__index_style="applied numerals", dial__lume=True,
                  hands__style="leaf", case__water_resistance_m=100),
    "chronograph": dict(case__diameter_mm=40.0, case__thickness_mm=13.5,
                        dial__index_style="applied batons", hands__style="baton",
                        movement__kind="mechanical", movement__winding="automatic",
                        movement__usable_barrel_turns=8.0),
    "quartz": dict(movement__kind="quartz", case__thickness_mm=8.0, dial__lume=True),
}

PRESET_ALIASES = {
    "diver": ("diver", "dive watch", "skin diver"),
    "dress": ("dress", "dress watch", "dressy", "formal"),
    "field": ("field", "field watch"),
    "pilot": ("pilot", "pilot watch", "flieger"),
    "chronograph": ("chronograph", "chrono", "stopwatch"),
    "quartz": ("quartz",),
}

ESSENTIAL_QUESTION = {
    "movement.kind": "Should the movement be mechanical or quartz?",
    "case.diameter_mm": "What case diameter do you want, in mm?",
    "case.thickness_mm": "What case thickness do you want, in mm?",
    "movement.beat_rate_bph": "What beat rate should the movement run at (vph)?",
    "movement.power_reserve_h": "How much power reserve should it have, in hours?",
    "dial.color": "What colour is the dial?",
}


@dataclass
class ParseResult:
    spec: Spec
    patches: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    engine: str = "rules"
    applied: int = 0
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "spec": self.spec.to_dict(),
            "patches": self.patches,
            "questions": self.questions,
            "notes": self.notes,
            "engine": self.engine,
            "applied": self.applied,
            "skipped": self.skipped,
            "summary": self.spec.summary(),
        }


# ---------------------------------------------------------------------------
# Rule parser
# ---------------------------------------------------------------------------

def parse(prompt: str, base: Spec | None = None, use_llm: bool = True,
          name: str | None = None) -> ParseResult:
    text = (prompt or "").strip()
    spec = (base.copy() if base else Spec())
    patches: list[dict] = []
    notes: list[str] = []
    seen: dict[str, int] = {}          # path -> priority of the winning patch
    explicit: set[str] = set()          # stated by the prompt, not by a preset

    def add(path: str, value, note: str = "", priority: int = 2):
        """Later does not always win: an explicit mention beats a preset."""
        if path in seen and seen[path] > priority:
            return
        if path in seen:
            patches[:] = [p for p in patches if p["path"] != path]
        seen[path] = priority
        if priority >= 2:
            explicit.add(path)
        patches.append({"op": "replace", "path": path, "value": value, "note": note})

    low = " " + re.sub(r"\s+", " ", text.lower()) + " "

    # -- named preset -----------------------------------------------------
    for key, values in PRESETS.items():
        aliases = PRESET_ALIASES.get(key, (key,))
        hit = next((a for a in aliases if re.search(rf"\b{re.escape(a)}\b", low)
                    and not _negated(low, a)), None)
        if hit:
            for path, value in values.items():
                add(path.replace("__", "."), value, f"{key} preset", priority=1)
            notes.append(f"applied the {key} preset")

    # -- meta -------------------------------------------------------------
    if name:
        add("meta.name", name, "provided name")
    else:
        m = re.search(r"\b(?:called|named|name it|model)\s+[\"']?([A-Z][\w -]{1,28})", text)
        if m:
            add("meta.name", m.group(1).strip().rstrip("."), "name from prompt")
    if re.search(r"existing watch|reference watch|copy of|homage", low):
        add("meta.mode", "existing-watch", "built from a reference")

    # -- case size --------------------------------------------------------
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*mm\s*(?:case|diameter|wide)", "case.diameter_mm"),
        (r"(?:case|diameter|width)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*mm", "case.diameter_mm"),
        (r"(\d+(?:\.\d+)?)\s*mm\s*(?:thick|thickness|tall)", "case.thickness_mm"),
        (r"(?:thickness|thick)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*mm", "case.thickness_mm"),
        (r"(\d+(?:\.\d+)?)\s*mm\s*(?:lug[- ]to[- ]lug|l2l)", "case.lug_to_lug_mm"),
        (r"(?:lug[- ]to[- ]lug|l2l)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*mm", "case.lug_to_lug_mm"),
        (r"(\d+(?:\.\d+)?)\s*mm\s*(?:lugs|lug width|strap|between the lugs)", "case.lug_width_mm"),
        (r"(\d+(?:\.\d+)?)\s*mm\s*water", "case.water_resistance_m"),
        (r"water\s*resistance\s*(?:of|is|to|:)?\s*(\d+)\s*m", "case.water_resistance_m"),
        (r"(\d+)\s*m\s*(?:water resistant|water resistance|water)", "case.water_resistance_m"),
        (r"\b(\d{2,3})\s*m\b", "case.water_resistance_m"),
    ]
    used_spans: list[tuple[int, int]] = []
    for pattern, path in patterns:
        for m in re.finditer(pattern, low):
            if any(a <= m.start() < b for a, b in used_spans):
                continue
            raw = m.group(1)
            value = float(raw)
            if path == "case.water_resistance_m":
                if value < 30 or value > 1200:
                    continue
                value = int(value)
                add(path, value, "water resistance from prompt")
            else:
                if value < 3 or value > 90:
                    continue
                add(path, round(value, 2), "case dimension from prompt")
            used_spans.append((m.start(), m.end()))
            break
    # a bare size is the case diameter
    if "case.diameter_mm" not in explicit:
        for m in re.finditer(r"\b(3[0-9]|4[0-6])(?:\.\d)?\s*mm\b", low):
            if not any(a <= m.start() < b for a, b in used_spans):
                add("case.diameter_mm", float(m.group(1)), "first bare size is the case diameter")
                break

    if re.search(r"\b(?:diver|dive watch|skin diver)\b", low):
        add("case.bezel", "dive", "diver preset", priority=0)
        add("case.water_resistance_m", 300, "diver preset", priority=0)
        add("dial.lume", True, "diver preset", priority=0)
        add("case.crystal", "sapphire", "diver preset", priority=0)
        add("case.caseback", "screw-down", "diver preset", priority=0)
        add("case.crown", "screw-down", "diver preset", priority=0)
    if re.search(r"\b(?:dress watch|dressy|formal)\b", low):
        add("case.bezel", "plain", "dress preset", priority=0)
        add("dial.lume", False, "dress preset", priority=0)
    if re.search(r"ultra[- ]thin|ultra thin", low):
        add("case.thickness_mm", 8.0, "ultra-thin preset", priority=1)
    elif re.search(r"\bthin\b", low) and "case.thickness_mm" not in explicit:
        add("case.thickness_mm", 9.0, "thin case", priority=1)

    # -- materials and finishes ------------------------------------------
    for word, value in CASE_MATERIALS:
        if re.search(rf"\b{re.escape(word)}\b", low):
            add("case.material", value, "case material from prompt")
            break
    for word, value in CASE_FINISHES:
        if re.search(rf"\b{re.escape(word)}\b", low):
            add("case.finish", value, "case finish from prompt")
            break

    # -- crystal, caseback, bezel ----------------------------------------
    for word, value in (("sapphire", "sapphire"), ("mineral", "mineral"), ("acrylic", "acrylic")):
        if re.search(rf"\b{word}\b", low):
            add("case.crystal", value, "crystal from prompt")
    if re.search(r"screw[- ]?down crown|screw[- ]?lock crown", low):
        add("case.crown", "screw-down", "screw-down crown")
    if re.search(r"push[- ]pull crown|push crown", low):
        add("case.crown", "push", "push crown")
    if re.search(r"\bdomed\b", low):
        add("case.crystal_type", "domed", "crystal shape")
    elif re.search(r"\bflat\b", low) and "crystal" in low:
        add("case.crystal_type", "flat", "crystal shape")
    if re.search(r"exhibition|display back|see[- ]through", low):
        add("case.caseback", "screw-down exhibition", "exhibition caseback")
    elif re.search(r"screw[- ]?down|screw[- ]?back|screwed", low):
        add("case.caseback", "screw-down", "caseback from prompt")
    elif re.search(r"\bsnap\b|press[- ]fit back", low):
        add("case.caseback", "snap", "caseback from prompt")
    if re.search(r"dive bezel|rotating bezel|unidirectional", low):
        add("case.bezel", "dive", "bezel from prompt")
    elif re.search(r"fluted", low):
        add("case.bezel", "fluted", "bezel from prompt")
    elif re.search(r"coin[- ]edge", low):
        add("case.bezel", "coin-edge", "bezel from prompt")
    elif re.search(r"plain bezel|smooth bezel", low):
        add("case.bezel", "plain", "bezel from prompt")

    # -- dial -------------------------------------------------------------
    finish_hit = None
    for word, value in FINISHES.items():
        if re.search(rf"\b{word}\b", low):
            finish_hit = value
            add("dial.finish", value, "dial finish from prompt")
            break
    color_hit = None
    # "blue sunburst dial": take the colour nearest to the word dial
    best = None
    for word, value in COLORS.items():
        for m in re.finditer(rf"\b{word}\b", low):
            distance = abs(m.start() - low.find(" dial")) if " dial" in low else 100
            if best is None or distance < best[0]:
                best = (distance, value)
    if best:
        color_hit = best[1]
        add("dial.color", color_hit, "dial colour from prompt")
    if re.search(r"\bpanda\b", low):
        add("dial.color", "panda", "panda dial")
    if re.search(r"\bno date\b|date[- ]less|without date", low):
        add("dial.date_window", False, "no date window")
        add("movement.complications", _without(spec, "date", "day-date"), "date removed")
    if re.search(r"date window|with date|date complication|\bdated\b", low) and not re.search(r"\bno date\b", low):
        add("dial.date_window", True, "date window")
    if re.search(r"small seconds|sub[- ]seconds|subdial seconds", low):
        add("dial.small_seconds", True, "small seconds")
    if re.search(r"no lume|without lume", low):
        add("dial.lume", False, "no lume")
    elif re.search(r"\blume|luminous|night visibility", low):
        add("dial.lume", True, "lume from prompt")
    for word, value in INDEX_STYLES:
        if re.search(rf"\b{re.escape(word)}\b", low):
            add("dial.index_style", value, "index style from prompt")
            break
    if re.search(r"no logo|unbranded|sterile dial", low):
        add("dial.logo_text", "", "sterile dial")

    # -- hands ------------------------------------------------------------
    for word, value in HAND_STYLES:
        if re.search(rf"\b{re.escape(word)}\b", low):
            add("hands.style", value, "hand style from prompt")
            break
    if re.search(r"\b(?:blued|blue) hands\b", low):
        add("hands.material", "blued steel", "blued hands")

    # -- movement ---------------------------------------------------------
    if re.search(r"\bquartz\b", low):
        add("movement.kind", "quartz", "quartz movement")
    elif re.search(r"\bmechanical\b|automatic|hand[- ]wound|manual wind|self[- ]winding|sweeping", low):
        add("movement.kind", "mechanical", "mechanical movement")
    if re.search(r"\bautomatic\b|self[- ]winding", low):
        add("movement.winding", "automatic", "automatic winding")
    elif re.search(r"hand[- ]wound|manual wind|manual", low):
        add("movement.winding", "manual", "manual winding")

    for regex, value in ((r"\b(18000|21600|25200|28800|36000)\s*(?:bph|vph|a/h)?\b", None),
                         (r"\bhigh[- ]beat\b", 28800), (r"\blow[- ]beat\b", 21600)):
        m = re.search(regex, low)
        if m:
            add("movement.beat_rate_bph", int(m.group(1)) if value is None else value,
                "beat rate from prompt")
    m = re.search(r"power reserve\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(?:h|hours?|hrs?)", low)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hours?|hrs?)\s*(?:power reserve|reserve)", low)
    if m:
        add("movement.power_reserve_h", float(m.group(1)), "power reserve from prompt")
    m = re.search(r"\b(\d{1,2})\s*jewels?\b", low)
    if m:
        add("movement.jewels", int(m.group(1)), "jewel count from prompt")
    m = re.search(r"\b(?:caliber|calibre|movement)\s+([A-Z0-9][\w .-]{1,18})", text)
    if m and not re.search(r"quartz|mechanical|automatic", m.group(1), re.I):
        candidate = m.group(1).strip().rstrip(".")
        if len(candidate) <= 18:
            add("movement.base_calibre", candidate, "calibre from prompt")

    # -- complications ----------------------------------------------------
    # Start from the winning complication patch if the brief already touched
    # them (e.g. "no date"), otherwise from the base spec.
    base_comps = spec.get("movement.complications") or {}
    for patch in reversed(patches):
        if patch.get("path") == "movement.complications":
            base_comps = patch.get("value") or {}
            break
    chosen = list(base_comps.keys())
    first_mention: dict[str, int] = {}
    for module_id, words in COMPLICATION_WORDS.items():
        for word in words:
            m = re.search(rf"\b{re.escape(word)}\b", low)
            if m:
                if module_id == "date" and re.search(r"\bno date\b", low):
                    break
                if module_id == "date" and re.search(r"day[- ]date", low):
                    break
                if module_id not in chosen:
                    chosen.append(module_id)
                first_mention.setdefault(module_id, m.start())
                break
    chosen.sort(key=lambda c: first_mention.get(c, 999))
    if re.search(r"\bno complications?\b|\bthree[- ]hander\b|\btime[- ]only\b", low):
        chosen = []
        add("dial.date_window", False, "time-only")
    if re.search(r"\bgmt\b|second time zone|dual time", low):
        add("dial.date_window", spec.get("dial.date_window") or False, "gmt keeps the dial layout")
    if re.search(r"\bdate\b", low) and not re.search(r"\bno date\b", low):
        add("dial.date_window", True, "date module needs a window")
    comp = compose(chosen)
    if comp["conflicts"]:
        for a, b in comp["conflicts"]:
            notes.append(f"{a} and {b} conflict; kept {a} and dropped {b}")
            if b in chosen:
                chosen.remove(b)
    for unknown in comp["unknown"]:
        notes.append(f"unknown complication '{unknown}' was ignored")
    add("movement.complications", {m: {} for m in chosen}, "complication set from prompt")

    # -- explicit name and notes -----------------------------------------
    if re.search(r"\bgift\b|anniversary|wedding", low):
        notes.append("occasion noted; no engineering field was changed")
    if re.search(r"\b(?:budget|cost|price)\b", low):
        notes.append("cost target noted; this build does not price labour or tooling")

    # -- optional LLM patches --------------------------------------------
    # Precedence (highest first): user locks and constraints, an explicit
    # statement in this prompt, the model's interpretation, heuristics and
    # presets, then schema defaults.  The model is useful for wording the rule
    # parser missed, but it must never override a value the user stated.
    engine = "rules"
    if use_llm and os.environ.get("WATCHCAD_LLM_URL") and text:
        llm_patches, err = _llm_parse(text, spec)
        if llm_patches is not None:
            engine = "rules+llm"
            for op in llm_patches:
                path = op.get("path", "")
                if not path or not _path_allowed(path):
                    continue
                if path in explicit:
                    notes.append(f"kept the user's stated value for {path} "
                                 "(the model patch was not applied)")
                    continue
                if path in seen:
                    patches[:] = [p for p in patches if p["path"] != path]
                seen[path] = 2
                patches.append(op)
            if err:
                notes.append(err)

    applied_spec, applied, skipped = _apply(spec, patches)
    if skipped:
        notes.append("locked fields were left unchanged: " + ", ".join(skipped))

    questions = _questions(applied_spec, explicit)
    return ParseResult(applied_spec, patches, questions, notes, engine, applied, skipped)


def _negated(text: str, word: str) -> bool:
    return bool(re.search(rf"\b(?:no|not|without)\s+{re.escape(word)}\b", text))


def _without(spec: Spec, *ids: str) -> dict:
    return {k: v for k, v in (spec.get("movement.complications") or {}).items() if k not in ids}


def _path_allowed(path: str) -> bool:
    if path in FIELDS or path in ("movement.complications", "meta.name", "meta.notes"):
        return True
    return path.startswith("movement.complications.")


def _apply(spec: Spec, patches: list[dict]) -> tuple[Spec, int, list[str]]:
    out = spec.copy()
    applied = 0
    skipped = []
    for op in patches:
        path = op.get("path")
        if not path:
            continue
        if path in out.locks:
            skipped.append(path)
            continue
        if out.set(path, op.get("value"), tag="user", note=op.get("note", ""), source=op.get("source")):
            applied += 1
    return out, applied, skipped


def _questions(spec: Spec, explicit: set[str]) -> list[str]:
    """Ask only for essentials the prompt did not settle.

    A default value is not an answer: the UI shows the question and leaves the
    field editable instead of pretending the prompt specified it.
    """
    quartz = spec.get("movement.kind") == "quartz"
    order = ["movement.kind", "case.diameter_mm", "case.thickness_mm", "dial.color",
             "movement.power_reserve_h", "movement.beat_rate_bph"]
    questions = []
    for path in order:
        if path in explicit:
            continue
        if quartz and path.startswith("movement.") and path != "movement.kind":
            continue
        if path in spec.locks:
            continue
        questions.append(ESSENTIAL_QUESTION.get(path, f"What value for {path}?"))
    return questions[:4]


# ---------------------------------------------------------------------------
# Optional LLM (OpenAI-compatible)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You convert a watch design request into spec patches.
You never output geometry, coordinates, meshes or formulas. You only output JSON.

Schema (dotted paths, values must match the type):
case.diameter_mm number 30-46; case.thickness_mm number 7-18;
case.lug_to_lug_mm number 36-56; case.lug_width_mm number 16-24;
case.material one of 316L stainless steel|titanium|bronze|ceramic|carbon;
case.finish one of brushed|polished|bead-blasted|PVD black|two-tone;
case.water_resistance_m integer 0-1200; case.crystal sapphire|mineral|acrylic;
case.crystal_type flat|domed; case.caseback screw-down|snap|screw-down exhibition;
case.bezel plain|dive|coin-edge|fluted; case.crown_diameter_mm 3.5-9;
dial.diameter_mm 20-42; dial.color free string; dial.finish matte|sunburst|guilloche|lacquered|sandblasted;
dial.index_style printed|applied batons|applied numerals|roman numerals|no indices;
dial.lume boolean; dial.date_window boolean; dial.small_seconds boolean; dial.logo_text string;
hands.style baton|leaf|dauphine|syringe|skeleton; hands.material polished steel|gold|blued steel|lume-filled;
movement.kind mechanical|quartz; movement.winding automatic|manual;
movement.beat_rate_bph 14400-36000; movement.power_reserve_h 8-200;
movement.jewels 0-80; movement.base_calibre string; movement.complications object;
meta.name string.
Complications: date, day-date, power-reserve, gmt, moonphase, chronograph,
annual-calendar, perpetual-calendar, tourbillon, minute-repeater.

Return exactly: {"patches":[{"op":"replace","path":"...","value":...,"note":"..."}]}
Only include fields the request actually determines. Never invent a diameter or a colour."""


def _llm_parse(prompt: str, spec: Spec) -> tuple[list[dict] | None, str | None]:
    url = os.environ.get("WATCHCAD_LLM_URL", "").rstrip("/")
    key = os.environ.get("WATCHCAD_LLM_KEY", "")
    model = os.environ.get("WATCHCAD_LLM_MODEL", "gpt-4o-mini")
    if not url:
        return None, None
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    body = {
        "model": model,
        "temperature": 0,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Current spec:\n{spec.to_json()}\n\nRequest:\n{prompt}"},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode())
        content = payload["choices"][0]["message"]["content"]
        obj = json.loads(content)
        patches = obj.get("patches") if isinstance(obj, dict) else None
        if not isinstance(patches, list):
            return None, "LLM returned no patch list; used the rule parser"
        clean = []
        for op in patches:
            if not isinstance(op, dict):
                continue
            path = op.get("path")
            if _path_allowed(path) and "value" in op:
                clean.append({"op": "replace", "path": path, "value": op["value"],
                              "note": "llm: " + str(op.get("note", ""))[:80]})
        return clean, None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return None, f"LLM unavailable ({type(exc).__name__}); used the rule parser"


# ---------------------------------------------------------------------------
# Chat-friendly helpers
# ---------------------------------------------------------------------------

def describe(spec: Spec) -> str:
    comps = compose(spec.get("movement.complications") or {})
    bits = [spec.summary()]
    if spec.get("movement.kind") == "mechanical":
        torque = spec.get("derived.torque") or {}
        train = spec.get("derived.train") or {}
        if train:
            bits.append(f"The train uses {len(train.get('stages', []))} meshes with exact tooth "
                        f"counts, {train.get('effective_power_reserve_h', 0):.1f} h effective reserve.")
        if torque:
            bits.append(f"Power budget: {torque.get('available_power_uw', 0)} uW available, "
                        f"{torque.get('required_power_uw', 0)} uW required.")
    if comps["ids"]:
        bits.append("Complications: " + ", ".join(MODULES[m]["name"] for m in comps["ids"]) + ".")
    return " ".join(bits)


def preset_prompt(preset: str) -> str | None:
    for item in ideas():
        if item["id"] == preset:
            return item["prompt"]
    return None


def ideas() -> list[dict]:
    return [
        {"id": key, "name": key.title(), "prompt": prompt}
        for key, prompt in {
            "diver": "A 42 mm steel dive watch, 300 m water resistance, black matte dial with "
                     "applied lume indices, baton hands, sapphire crystal, screw-down caseback, date.",
            "dress": "A 38 mm polished steel dress watch, silver sunburst dial with applied batons, "
                     "dauphine hands, manual wind movement, 48 hour power reserve, no date.",
            "field": "A 38 mm bead-blasted field watch, black dial with Arabic numerals and lume, "
                     "syringe hands, 100 m water resistance.",
            "pilot": "A 43 mm steel pilot watch with a large crown, black dial, white numerals, "
                     "leaf hands and lume, automatic movement.",
            "chronograph": "A 40 mm steel chronograph with a panda dial, applied batons, "
                           "tachymeter bezel, automatic movement and date.",
            "moonphase": "A 40 mm steel dress watch with a blue sunburst dial, moon phase at six, "
                         "applied batons, automatic movement.",
            "quartz": "A 36 mm titanium quartz watch with a white dial, printed indices, "
                      "sapphire crystal and 100 m water resistance.",
        }.items()
    ]
