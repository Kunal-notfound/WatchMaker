"""Versioned, patch-based watch specification.

The spec is the single source of truth.  Every scalar lives in a nested dict so
that it can be addressed by a dotted path, tagged with provenance, locked by
the user, and changed by a JSON-patch.  There is no hidden state: geometry is a
pure function of this document plus the solver.

Provenance tags
---------------
user      the user typed it (prompt or form)
derived   filled in by the solver, reproducible from other fields
sourced   copied from an external source (URL kept, timestamped)
not_found looked for, not published (kept as null)
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from .modules import COMPLICATIONS

# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------
# kind: num | int | str | bool | enum | dict
# Every field has a unit ("" when unitless), a range for numbers and a default.
# "essential" means a prompt may not silently invent it: the chat asks instead.

F = lambda kind, default, unit="", **kw: dict(kind=kind, default=default, unit=unit, **kw)

CASE = {
    "diameter_mm": F("num", 40.0, "mm", min=30, max=46, essential=True),
    "lug_to_lug_mm": F("num", 48.0, "mm", min=36, max=56),
    "thickness_mm": F("num", 11.0, "mm", min=7, max=18, essential=True),
    "lug_width_mm": F("num", 20.0, "mm", min=16, max=24),
    "material": F("enum", "316L stainless steel",
                  choices=["316L stainless steel", "titanium", "bronze", "ceramic", "carbon"]),
    "finish": F("enum", "brushed", choices=["brushed", "polished", "bead-blasted", "PVD black", "two-tone"]),
    "water_resistance_m": F("int", 100, "m", min=0, max=1200),
    "crystal": F("enum", "sapphire", choices=["sapphire", "mineral", "acrylic"]),
    "crystal_type": F("enum", "domed", choices=["flat", "domed"]),
    "crystal_thickness_mm": F("num", 1.2, "mm", min=0.6, max=3.0),
    "caseback": F("enum", "screw-down", choices=["screw-down", "snap", "screw-down exhibition"]),
    "crown": F("enum", "push", choices=["push", "screw-down"]),
    "bezel": F("enum", "plain", choices=["plain", "dive", "coin-edge", "fluted"]),
    "crown_diameter_mm": F("num", 5.5, "mm", min=3.5, max=9),
    "lug_count": F("int", 4, "", min=2, max=4),
}

DIAL = {
    "diameter_mm": F("num", 32.0, "mm", min=20, max=42),
    "thickness_mm": F("num", 0.4, "mm", min=0.2, max=1.2),
    "color": F("str", "black", essential=True),
    "index_style": F("enum", "applied batons",
                     choices=["printed", "applied batons", "applied numerals", "roman numerals", "no indices"]),
    "lume": F("bool", True),
    "date_window": F("bool", False),
    "small_seconds": F("bool", False),
    "logo_text": F("str", ""),
    "finish": F("enum", "sunburst", choices=["matte", "sunburst", "guilloche", "lacquered", "sandblasted"]),
    "index_material": F("enum", "polished steel", choices=["polished steel", "gold", "white gold", "printed"]),
}

HANDS = {
    "style": F("enum", "baton", choices=["baton", "leaf", "dauphine", "syringe", "skeleton"]),
    "hour_length_mm": F("num", 19.0, "mm", min=8, max=24),
    "minute_length_mm": F("num", 27.0, "mm", min=12, max=32),
    "second_length_mm": F("num", 27.5, "mm", min=12, max=32),
    "thickness_mm": F("num", 0.25, "mm", min=0.1, max=0.6),
    "material": F("enum", "polished steel", choices=["polished steel", "gold", "blued steel", "lume-filled"]),
    "stack_height_mm": F("num", 0.9, "mm", min=0.4, max=2.0),
}

MOVEMENT = {
    "kind": F("enum", "mechanical", choices=["mechanical", "quartz"], essential=True),
    "base_calibre": F("str", ""),
    "width_mm": F("num", 28.0, "mm", min=15, max=38),
    "height_mm": F("num", 4.6, "mm", min=1.5, max=9),
    "beat_rate_bph": F("int", 28800, "bph", min=14400, max=36000, essential=True),
    "power_reserve_h": F("num", 42.0, "h", min=8, max=200, essential=True),
    "jewels": F("int", 21, "", min=0, max=80),
    "winding": F("enum", "automatic", choices=["automatic", "manual"]),
    "escape_wheel_teeth": F("int", 15, "", min=10, max=20),
    "usable_barrel_turns": F("num", 6.5, "turns", min=3, max=12),
    "hand_height_mm": F("num", 1.2, "mm", min=0.4, max=3.0),
    "complications": F("dict", {}),
}

QUARTZ = {
    "battery_mAh": F("num", 32.0, "mAh", min=5, max=90),
    "current_uA": F("num", 1.2, "uA", min=0.2, max=5),
    "step_hz": F("num", 1.0, "Hz", min=0.5, max=8),
    "module": F("str", "generic 3-hand"),
}

DERIVED = {
    "train": F("dict", {}),
    "barrel": F("dict", {}),
    "stack": F("dict", {}),
    "torque": F("dict", {}),
    "mass_g": F("num", 0.0, "g"),
    "quartz_train": F("dict", {}),
    "battery_life_years": F("num", 0.0, "years"),
    "checksum": F("str", ""),
}

META = {
    "name": F("str", "Untitled"),
    "version": F("int", 1, ""),
    "created_at": F("str", ""),
    "notes": F("str", ""),
    "mode": F("enum", "prompt-to-cad", choices=["prompt-to-cad", "existing-watch"]),
    "source_url": F("str", ""),
}

SCHEMA = {"case": CASE, "dial": DIAL, "hands": HANDS,
          "movement": MOVEMENT, "quartz": QUARTZ,
          "derived": DERIVED, "meta": META}

DEFAULTS: dict[str, Any] = {
    group: {name: f["default"] for name, f in fields.items()}
    for group, fields in SCHEMA.items()
}

FIELDS: dict[str, dict] = {
    f"{group}.{name}": f for group, fields in SCHEMA.items() for name, f in fields.items()
}

ESSENTIAL = [p for p, f in FIELDS.items() if f.get("essential")]


# ---------------------------------------------------------------------------
# Spec document
# ---------------------------------------------------------------------------

@dataclass
class Spec:
    data: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    provenance: dict = field(default_factory=dict)
    locks: set = field(default_factory=set)
    history: list = field(default_factory=list)

    # -- addressing -------------------------------------------------------
    @staticmethod
    def split(path: str) -> list[str]:
        return [p for p in path.split(".") if p]

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in self.split(path):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def exists(self, path: str) -> bool:
        sentinel = object()
        return self.get(path, sentinel) is not sentinel

    def _parent(self, path: str) -> tuple[dict, str]:
        parts = self.split(path)
        node = self.data
        for key in parts[:-1]:
            node = node.setdefault(key, {})
        return node, parts[-1]

    # -- writing ----------------------------------------------------------
    def set(self, path: str, value: Any, tag: str = "user",
            source: str | None = None, note: str = "", force: bool = False) -> bool:
        """Write a value.  Locked fields reject writes unless force=True."""
        if path in self.locks and not force:
            return False
        parent, leaf = self._parent(path)
        parent[leaf] = value
        prov = {"tag": tag}
        if source:
            prov["source"] = source
        if note:
            prov["note"] = note
        self.provenance[path] = prov
        self.data["meta"]["version"] = int(self.data["meta"].get("version", 1)) + 1
        return True

    def lock(self, path: str, locked: bool = True) -> None:
        if locked:
            self.locks.add(path)
        else:
            self.locks.discard(path)

    def tag(self, path: str) -> str:
        return self.provenance.get(path, {}).get("tag", "default")

    # -- patch ------------------------------------------------------------
    def patch(self, ops: list[dict], tag: str = "user", source: str | None = None) -> "Spec":
        """Apply RFC-6902-like ops to a copy: add, replace, remove, lock, unlock."""
        new = self.copy()
        for op in ops:
            kind = op.get("op", "replace")
            path = op.get("path", "")
            if not path:
                continue
            if kind in ("add", "replace"):
                new.set(path, op.get("value"), tag=tag, source=source, note=op.get("note", ""))
            elif kind == "remove":
                parent, leaf = new._parent(path)
                parent.pop(leaf, None)
                new.provenance.pop(path, None)
            elif kind == "lock":
                new.lock(path, True)
            elif kind == "unlock":
                new.lock(path, False)
        new.data["meta"]["version"] = int(self.data["meta"].get("version", 1)) + 1
        return new

    def diff(self, other: "Spec") -> list[dict]:
        out = []
        for path in sorted(set(flatten(self.data)) | set(flatten(other.data))):
            a = get_path(self.data, path)
            b = get_path(other.data, path)
            if a != b:
                out.append({"path": path, "from": a, "to": b})
        return out

    # -- validation -------------------------------------------------------
    def validate(self) -> list[dict]:
        issues: list[dict] = []
        for path, meta in FIELDS.items():
            if meta.get("kind") == "dict":
                continue
            value = self.get(path)
            if value is None:
                continue
            kind, unit = meta["kind"], meta.get("unit", "")
            if kind in ("num", "int") and (isinstance(value, bool) or not isinstance(value, (int, float))):
                issues.append(issue("error", path, f"expected number, got {type(value).__name__}"))
                continue
            if kind == "num" and meta.get("min") is not None and value < meta["min"]:
                issues.append(issue("error", path, f"{value}{unit} below minimum {meta['min']}{unit}"))
            if kind == "num" and meta.get("max") is not None and value > meta["max"]:
                issues.append(issue("error", path, f"{value}{unit} above maximum {meta['max']}{unit}"))
            if kind == "int" and isinstance(value, float) and value != int(value):
                issues.append(issue("error", path, "expected integer"))
            if kind == "enum" and value not in meta.get("choices", []):
                issues.append(issue("error", path, f"'{value}' is not one of {meta['choices']}"))
        issues += self._validate_cross_field()
        return issues

    def _validate_cross_field(self) -> list[dict]:
        issues = []
        kind = self.get("movement.kind")
        diameter = self.get("case.diameter_mm") or 40
        movement_w = self.get("movement.width_mm") or 28
        if movement_w > diameter - 4:
            issues.append(issue("error", "movement.width_mm",
                                f"movement {movement_w:.1f} mm does not fit a {diameter:.0f} mm case"))
        if kind == "mechanical":
            if self.get("movement.beat_rate_bph") and self.get("movement.beat_rate_bph") % 1800:
                issues.append(issue("warning", "movement.beat_rate_bph",
                                    "beat rate is not a multiple of 1800 bph (common frequencies: 18000/21600/25200/28800/36000)"))
        if self.get("dial.diameter_mm") and self.get("dial.diameter_mm") > diameter - 2:
            issues.append(issue("warning", "dial.diameter_mm", "dial is wider than the case opening"))
        w = self.get("case.water_resistance_m") or 0
        if w >= 100 and self.get("case.caseback") == "snap":
            issues.append(issue("warning", "case.caseback", "snap caseback is unusual above 50 m; use screw-down"))
        if self.get("dial.date_window") and "date" not in (self.get("movement.complications") or {}):
            issues.append(issue("info", "dial.date_window", "date window without a date module"))
        return issues

    # -- copying / io -----------------------------------------------------
    def copy(self) -> "Spec":
        return Spec(copy.deepcopy(self.data), copy.deepcopy(self.provenance), set(self.locks),
                    copy.deepcopy(self.history))

    def to_dict(self) -> dict:
        return {"data": self.data, "provenance": self.provenance,
                "locks": sorted(self.locks), "schema_version": 1}

    @classmethod
    def from_dict(cls, obj: dict) -> "Spec":
        data = copy.deepcopy(DEFAULTS)
        deep_update(data, obj.get("data", obj))
        prov = obj.get("provenance", {})
        for path in flatten(obj.get("data", {})):
            prov.setdefault(path, {"tag": "user"})
        return cls(data, prov, set(obj.get("locks", [])), obj.get("history", []))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_json(cls, text: str) -> "Spec":
        return cls.from_dict(json.loads(text))

    # -- reporting --------------------------------------------------------
    def summary(self) -> str:
        c = self.data["case"]
        m = self.data["movement"]
        comps = ", ".join(m.get("complications", {}).keys()) or "none"
        if m["kind"] == "quartz":
            life = self.get("derived.battery_life_years") or 0
            energy = f"{life:.1f} year battery" if life else "battery life not solved"
        else:
            energy = f"{m['power_reserve_h']:.0f} h reserve"
        return (f"{c['diameter_mm']:.0f} mm {m['kind']} watch, {c['thickness_mm']:.1f} mm thick, "
                f"dial {self.data['dial']['color']}, {energy}, complications: {comps}")


def issue(level: str, path: str, message: str) -> dict:
    return {"level": level, "path": path, "message": message}


def get_path(data: dict, path: str, default=None):
    node = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def flatten(node: Any, prefix: str = "") -> list[str]:
    out = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and value and not _is_leaf_dict(path):
                out += flatten(value, path)
            else:
                out.append(path)
    return out


def _is_leaf_dict(path: str) -> bool:
    """Derived/parameter dicts are addressed as a whole, not field by field."""
    return path.split(".")[-1] in ("train", "barrel", "stack", "torque", "quartz_train",
                                   "complications", "stages", "meshes")


def deep_update(base: dict, other: dict) -> None:
    for key, value in other.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value


def new_spec(name: str = "Untitled", **values) -> Spec:
    spec = Spec()
    spec.data["meta"]["name"] = name
    for path, value in values.items():
        spec.set(path.replace("__", "."), value, tag="user")
    for module_id in (spec.get("movement.complications") or {}):
        if module_id not in COMPLICATIONS:
            spec.data["movement"]["complications"].pop(module_id, None)
    return spec
