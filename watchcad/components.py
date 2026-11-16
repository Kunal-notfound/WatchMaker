"""Component-aware view of the watch specification.

The 3-D viewer selects a *part* (``case_mid``, ``crown``, ``hour_hand`` …).  A
conversational edit, however, has to be scoped to something the user can reason
about: a component.  This module is the single registry that connects

* scene part names and viewer groups/roles,
* writable specification fields,
* deterministic verification checks, and
* a stable component id used by the API and the chat runtime.

It is deliberately data-only.  Resolution never mutates a spec and never invents
geometry; it only maps names to an explicit field/check scope.  A focused edit
that leaves that scope is refused by the tool layer with a message that names
the owning component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .schema import FIELDS


@dataclass(frozen=True)
class Component:
    """A user-facing component and the spec surface it owns."""

    id: str
    label: str
    group: str
    role: str
    description: str
    fields: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    parts: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    dimension_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "role": self.role,
            "description": self.description,
            "fields": list(self.fields),
            "checks": list(self.checks),
            "parts": list(self.parts),
            "aliases": list(self.aliases),
            "dimension_fields": list(self.dimension_fields),
        }


# The registry order also resolves shared paths: the first component that owns a
# path is reported as its owner.  Specific components therefore precede the
# broad "case" entry, which owns the remaining envelope fields.
COMPONENTS: dict[str, Component] = {
    "case": Component(
        "case", "Case", "Case", "case",
        "The case envelope: diameter, length, thickness, material, finish and "
        "the water-resistance target. Crystal, crown, caseback and bezel are "
        "separate components with their own construction fields.",
        fields=(
            "case.diameter_mm", "case.lug_to_lug_mm", "case.thickness_mm",
            "case.lug_width_mm", "case.lug_count", "case.material",
            "case.finish", "case.water_resistance_m",
        ),
        checks=(
            "geometry.movement_fit", "geometry.dial_fit", "geometry.stack",
            "geometry.hand_clearance", "geometry.complication_stack",
            "tolerance.stackup", "dfm.water_resistance", "dfm.lug_width",
        ),
        parts=("case_mid", "movement_ring"),
        aliases=("case", "midcase", "mid_case", "case_mid", "envelope"),
        dimension_fields=("case.diameter_mm", "case.lug_to_lug_mm",
                          "case.thickness_mm", "case.lug_width_mm"),
    ),
    "crystal": Component(
        "crystal", "Crystal", "Crystal", "crystal",
        "The crystal and its gasket: material, profile and thickness. Crystal "
        "thickness is part of the vertical stack and the hand-clearance check.",
        fields=("case.crystal", "case.crystal_type", "case.crystal_thickness_mm"),
        checks=("geometry.hand_clearance", "geometry.stack", "tolerance.stackup"),
        parts=("crystal", "caseback_glass"),
        aliases=("crystal", "glass", "sapphire", "crystal_gasket", "gasket",
                 "crystal gasket", "windshield"),
        dimension_fields=("case.crystal_thickness_mm",),
    ),
    "crown": Component(
        "crown", "Crown", "Case", "case",
        "The crown, tube and pushers. Screw-down construction is required by the "
        "manufacturability check at high water resistance; crown diameter is an "
        "ergonomic variable.",
        fields=("case.crown", "case.crown_diameter_mm"),
        checks=("dfm.water_resistance",),
        parts=("crown", "crown_tube", "pusher"),
        aliases=("crown", "crown_tube", "tube", "pusher", "pushers",
                 "winding crown"),
        dimension_fields=("case.crown_diameter_mm",),
    ),
    "caseback": Component(
        "caseback", "Caseback", "Case", "case",
        "The caseback construction. A screw-down back is part of the "
        "water-resistance check; the exhibition variant is still a case part.",
        fields=("case.caseback",),
        checks=("dfm.water_resistance", "geometry.stack", "tolerance.stackup"),
        parts=("caseback", "caseback_glass"),
        aliases=("caseback", "case_back", "back", "caseback glass",
                 "exhibition caseback"),
    ),
    "bezel": Component(
        "bezel", "Bezel", "Case", "case",
        "The bezel style. It is a visible design decision; a dive bezel on a "
        "watch rated below 100 m is flagged as a component-consistency risk.",
        fields=("case.bezel",),
        checks=("geometry.dial_fit",),
        parts=("bezel",),
        aliases=("bezel", "dive bezel", "fluted bezel", "coin edge", "coin-edge"),
    ),
    "dial": Component(
        "dial", "Dial", "Dial", "dial",
        "The dial plate, indices, minute track and dial furniture. Hand lengths "
        "are a separate component even though they are fractions of the dial.",
        fields=(
            "dial.diameter_mm", "dial.thickness_mm", "dial.color",
            "dial.index_style", "dial.lume", "dial.date_window",
            "dial.small_seconds", "dial.logo_text", "dial.finish",
            "dial.index_material",
        ),
        checks=(
            "geometry.dial_fit", "geometry.minute_length_mm",
            "geometry.second_length_mm", "geometry.complication_stack",
        ),
        parts=("dial", "rehaut", "index", "minute_tick", "date_frame",
               "date_patch", "box"),
        aliases=("dial", "dial plate", "face", "indices", "index",
                 "minute track", "rehaut", "date window", "date_frame",
                 "date frame", "logo"),
        dimension_fields=("dial.diameter_mm", "dial.thickness_mm"),
    ),
    "hands": Component(
        "hands", "Hands", "Hands", "hand",
        "The hour, minute and second hands. Lengths are checked against the "
        "dial radius and the stack is checked against crystal clearance.",
        fields=(
            "hands.style", "hands.hour_length_mm", "hands.minute_length_mm",
            "hands.second_length_mm", "hands.thickness_mm", "hands.material",
            "hands.stack_height_mm",
        ),
        checks=(
            "geometry.hand_clearance", "geometry.hour_length_mm",
            "geometry.minute_length_mm", "geometry.second_length_mm",
            "geometry.hand_lengths", "tolerance.stackup",
        ),
        parts=("hour_hand", "minute_hand", "second_hand",
               "second_counterweight", "hand_boss"),
        aliases=("hands", "hand", "hour hand", "minute hand", "second hand",
                 "hour_hand", "minute_hand", "second_hand", "hand set",
                 "counterweight", "hand boss"),
        dimension_fields=("hands.hour_length_mm", "hands.minute_length_mm",
                          "hands.second_length_mm", "hands.thickness_mm",
                          "hands.stack_height_mm"),
    ),
    "movement": Component(
        "movement", "Movement", "Movement", "movement",
        "The movement and its going train. The model covers the plate, barrel, "
        "train, escapement, bridges and (for quartz) the electronic module. "
        "Complications are a separate component.",
        fields=(
            "movement.kind", "movement.base_calibre", "movement.width_mm",
            "movement.height_mm", "movement.beat_rate_bph",
            "movement.power_reserve_h", "movement.jewels", "movement.winding",
            "movement.escape_wheel_teeth", "movement.usable_barrel_turns",
            "movement.hand_height_mm",
            "quartz.battery_mAh", "quartz.current_uA", "quartz.step_hz",
            "quartz.module",
        ),
        checks=(
            "geometry.movement_fit", "geometry.wheels_inside",
            "geometry.gear_clearance", "geometry.gear_interference",
            "geometry.centre_distance", "geometry.tooth_counts",
            "kinematics.fourth_wheel", "kinematics.hour_hand",
            "kinematics.beat_rate", "kinematics.reverser",
            "energy.power_budget", "energy.escape_torque",
            "energy.power_reserve", "energy.mainspring",
            "tolerance.gear_backlash", "tolerance.jewel_fit",
            "dfm.module", "dfm.pinions",
        ),
        parts=(
            "mainplate", "barrel", "barrel_wheel", "barrel_lid", "arbor",
            "centre_pinion", "centre_wheel", "third_pinion", "third_wheel",
            "fourth_pinion", "fourth_wheel", "escape_pinion", "escape_wheel",
            "pallet_fork", "pallet_stones", "balance_wheel", "balance_spoke",
            "hairspring", "balance_cock", "ratchet_wheel", "crown_wheel",
            "click", "train_bridge", "barrel_bridge", "jewel", "barrel_arbor",
            "pcb", "battery", "stepper_coil",
        ),
        aliases=("movement", "calibre", "caliber", "train", "going train",
                 "mainplate", "main plate", "barrel", "balance", "balance wheel",
                 "escapement", "pallet fork", "hairspring", "bridge", "arbor",
                 "jewel", "jewels", "gear", "wheel", "pinion", "quartz module",
                 "movement_ring"),
        dimension_fields=("movement.width_mm", "movement.height_mm",
                          "movement.hand_height_mm"),
    ),
    "complications": Component(
        "complications", "Complications", "Complications", "complication",
        "The plug-in complication set. Each module adds height, torque draw and "
        "dial furniture; conflicts are resolved by the module registry.",
        fields=("movement.complications",),
        checks=(
            "spec.complication_conflicts", "spec.complications_known",
            "physical.complications", "geometry.complication_stack",
            "energy.power_budget",
        ),
        parts=("date_ring", "day_ring", "moon_disc", "moon", "gmt_hand",
               "tourbillon_cage", "chrono_counter", "power_reserve_indicator"),
        aliases=("complication", "complications", "module", "modules", "date",
                 "day date", "day-date", "gmt", "moonphase", "moon phase",
                 "chronograph", "chrono", "tourbillon", "annual calendar",
                 "perpetual calendar", "minute repeater", "power reserve",
                 "date_ring", "date ring", "moon_disc", "moon disc"),
        dimension_fields=(),
    ),
    "lugs": Component(
        "lugs", "Lugs & strap", "Case", "case",
        "The lug geometry and the strap interface. Lug width and lug-to-lug are "
        "shared with the case envelope; the strap maker only supports standard "
        "widths.",
        fields=("case.lug_width_mm", "case.lug_to_lug_mm", "case.lug_count"),
        checks=("dfm.lug_width",),
        parts=("lug", "spring_bar"),
        aliases=("lugs", "lug", "spring bar", "spring_bar", "strap", "bracelet",
                 "strap interface", "lug width"),
        dimension_fields=("case.lug_to_lug_mm", "case.lug_width_mm"),
    ),
    "gasket": Component(
        "gasket", "Gaskets", "Case", "gasket",
        "The crystal gasket is generated from the crystal and caseback "
        "construction; it has no independent writable field. Use Crystal, "
        "Caseback or Case for the sealing dimensions.",
        fields=(),
        checks=("dfm.water_resistance", "geometry.stack"),
        parts=("crystal_gasket",),
        aliases=("gasket", "gaskets", "seal", "seals", "o ring", "o-ring"),
    ),
}

# Scene roles and viewer groups are valid selections too.
_ROLE_COMPONENT = {
    "case": "case", "dial": "dial", "hand": "hands", "movement": "movement",
    "complication": "complications", "crystal": "crystal", "gasket": "gasket",
}
_GROUP_COMPONENT = {
    "case": "case", "dial": "dial", "hands": "hands", "movement": "movement",
    "complications": "complications", "crystal": "crystal", "other": "case",
}

_PART_INDEX: dict[str, str] = {}
for _component in COMPONENTS.values():
    for _part in _component.parts:
        _PART_INDEX.setdefault(_part.lower(), _component.id)
    _PART_INDEX.setdefault(_component.id, _component.id)
    _PART_INDEX.setdefault(_component.label.lower(), _component.id)


class UnknownComponent(ValueError):
    """The selection does not name a component or a scene part."""


def _normalise(value: Any) -> str:
    text = str(value or "").strip().lower()
    for char in ("-", "/", "\\"):
        text = text.replace(char, " ")
    return "_".join(text.split())


def _aliases() -> dict[str, str]:
    table: dict[str, str] = {}
    for component in COMPONENTS.values():
        names = (component.id, component.label, component.group, component.role,
                 *component.aliases)
        for name in names:
            key = _normalise(name)
            if key:
                table.setdefault(key, component.id)
    return table


_ALIAS_INDEX = _aliases()


def resolve_component(selection: Any) -> Component | None:
    """Resolve a component id, label, alias, scene part, role or viewer group.

    ``None`` and an empty selection mean "no focus" and return ``None``.  A
    non-empty unknown selection raises :class:`UnknownComponent` so the API can
    return a useful 400 instead of silently editing the whole design.
    """
    if selection is None or selection == "":
        return None
    if isinstance(selection, Component):
        return selection
    if isinstance(selection, dict):
        for key in ("component", "id", "name", "part", "role", "group"):
            value = selection.get(key)
            if value:
                try:
                    return resolve_component(value)
                except UnknownComponent:
                    continue
        raise UnknownComponent(_unknown_message(selection))

    key = _normalise(selection)
    if not key:
        return None
    if key in COMPONENTS:
        return COMPONENTS[key]
    if key in _ALIAS_INDEX:
        return COMPONENTS[_ALIAS_INDEX[key]]
    if key in _PART_INDEX:
        return COMPONENTS[_PART_INDEX[key]]
    if key in _ROLE_COMPONENT:
        return COMPONENTS[_ROLE_COMPONENT[key]]
    if key in _GROUP_COMPONENT:
        return COMPONENTS[_GROUP_COMPONENT[key]]
    # A dotted field path is a useful focus too: "dial.color" selects Dial.
    if "." in key:
        owner = component_for_path(key)
        if owner is not None:
            return owner
        prefix = key.split(".", 1)[0]
        if prefix in COMPONENTS:
            return COMPONENTS[prefix]
    # Last resort: a unique substring match ("mid case" -> case_mid, "arbor").
    matches = {
        component.id for name, component in
        ((_normalise(alias), COMPONENTS[cid]) for alias, cid in _ALIAS_INDEX.items())
        if key in name or name in key
    }
    if len(matches) == 1:
        return COMPONENTS[matches.pop()]
    part_matches = {
        cid for part, cid in _PART_INDEX.items()
        if key in part or part in key
    }
    if len(part_matches) == 1:
        return COMPONENTS[part_matches.pop()]
    raise UnknownComponent(_unknown_message(selection))


def _unknown_message(selection: Any) -> str:
    labels = ", ".join(component.label for component in COMPONENTS.values())
    return (f"unknown component {selection!r}; choose one of {labels}, or select "
            "a part in the 3-D viewer")


def allows_path(component: Component | None, path: str) -> bool:
    """True when a focused component may write ``path``."""
    if component is None:
        return True
    if not isinstance(path, str) or not path:
        return False
    return any(path == owned or path.startswith(owned + ".")
               for owned in component.fields)


def component_for_path(path: str) -> Component | None:
    """The component that owns a writable dotted path, if any."""
    if not isinstance(path, str) or not path:
        return None
    best: tuple[int, Component] | None = None
    order = {cid: index for index, cid in enumerate(COMPONENTS)}
    for component in COMPONENTS.values():
        for owned in component.fields:
            if path == owned or path.startswith(owned + "."):
                # Longest field wins; registry order breaks ties so the broad
                # case entry is preferred for shared envelope fields.
                key = (len(owned), -order[component.id])
                if best is None or key > (best[0], -order[best[1].id]):
                    best = (len(owned), component)
    return best[1] if best else None


def component_for_check(check_id: str) -> Component | None:
    """The first component that declares a verification check."""
    for component in COMPONENTS.values():
        if check_id in component.checks:
            return component
    return None


def component_scope(component: Component | None) -> dict:
    """A compact scope description used in prompts and API payloads."""
    if component is None:
        return {"id": None, "label": "Whole design", "fields": [],
                "checks": [], "parts": []}
    return {
        "id": component.id,
        "label": component.label,
        "fields": list(component.fields),
        "checks": list(component.checks),
        "parts": list(component.parts),
    }


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------

def _field_label(path: str) -> str:
    return path.split(".")[-1].replace("_", " ")


def _field_meta(path: str) -> dict:
    return FIELDS.get(path, {})


def _rounded(value: Any, digits: int = 3) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value), digits)


def _union_bounds(boxes: Iterable[dict]) -> dict:
    boxes = [box for box in boxes if isinstance(box, dict)
             and isinstance(box.get("min"), list)
             and isinstance(box.get("max"), list)]
    if not boxes:
        return {}
    mins = [min(box["min"][i] for box in boxes) for i in range(3)]
    maxs = [max(box["max"][i] for box in boxes) for i in range(3)]
    return {"min": [_rounded(v, 2) for v in mins],
            "max": [_rounded(v, 2) for v in maxs],
            "size": [_rounded(maxs[i] - mins[i], 2) for i in range(3)]}


def component_context(studio, selection: Any = None) -> dict:
    """Read-only component context: fields, locks, checks, parts and bounds.

    ``studio`` is the same object used by the API/agent (``solved`` spec,
    ``checks`` and ``scene`` attributes).  Missing attributes degrade to an
    empty context rather than raising, which keeps the endpoint useful in tests
    and during a partial rebuild.
    """
    component = resolve_component(selection)
    if component is None:
        raise UnknownComponent("a component is required for component context")
    spec = getattr(studio, "solved", None) or getattr(studio, "spec", None)
    checks = list(getattr(studio, "checks", []) or [])
    scene = getattr(studio, "scene", {}) or {}
    locks = set(getattr(spec, "locks", set()) or set()) if spec is not None else set()

    fields = []
    for path in component.fields:
        meta = _field_meta(path)
        if not meta:
            continue
        value = spec.get(path) if spec is not None else None
        fields.append({
            "path": path,
            "label": _field_label(path),
            "kind": meta.get("kind", "str"),
            "unit": meta.get("unit", ""),
            "value": _rounded(value),
            "choices": list(meta.get("choices") or []),
            "min": meta.get("min"),
            "max": meta.get("max"),
            "locked": path in locks,
            "provenance": (spec.provenance.get(path, {}) if spec is not None else {}),
        })

    related = []
    field_set = set(component.fields)
    for check in checks:
        fix_path = (check.get("fix") or {}).get("path")
        if check.get("id") in component.checks or fix_path in field_set \
                or any(fix_path and fix_path.startswith(path + ".")
                       for path in component.fields):
            related.append({
                "id": check.get("id"),
                "layer": check.get("layer"),
                "title": check.get("title"),
                "passed": check.get("passed"),
                "verdict": check.get("verdict"),
                "detail": check.get("detail"),
                "fix": check.get("fix"),
            })

    part_rows = []
    all_bounds = []
    wanted = {name.lower() for name in component.parts}
    for name in component.parts:
        rows = [part for part in scene.get("parts", [])
                if str(getattr(part, "name", "")).lower() == name.lower()]
        if not rows:
            continue
        boxes = [part.bounds() for part in rows if hasattr(part, "bounds")]
        bounds = _union_bounds(boxes)
        if bounds:
            all_bounds.append(bounds)
        part_rows.append({
            "name": name,
            "count": len(rows),
            "role": getattr(rows[0], "role", ""),
            "material": getattr(rows[0], "material", ""),
            "color": getattr(rows[0], "color", ""),
            "triangles": sum(len(getattr(part, "faces", [])) for part in rows),
            "bounds": bounds,
        })
    # A selection may be a part name that is not in the registry (for example a
    # solver-specific arbor).  Include it explicitly so the context never hides
    # what the user clicked.
    if isinstance(selection, dict):
        selected_name = str(selection.get("name") or "").lower()
        if selected_name and selected_name not in wanted:
            for part in scene.get("parts", []):
                if str(getattr(part, "name", "")).lower() == selected_name:
                    part_rows.append({
                        "name": getattr(part, "name", ""),
                        "count": 1,
                        "role": getattr(part, "role", ""),
                        "material": getattr(part, "material", ""),
                        "color": getattr(part, "color", ""),
                        "triangles": len(getattr(part, "faces", [])),
                        "bounds": part.bounds(),
                    })
                    break

    return {
        "component": component.to_dict(),
        "selection": selection,
        "fields": fields,
        "locks": sorted(path for path in component.fields if path in locks),
        "checks": related,
        "failing_checks": [row["id"] for row in related if row.get("passed") is False],
        "parts": part_rows,
        "bounds": _union_bounds(all_bounds),
        "writable": bool(component.fields),
        "notes": _context_notes(component),
    }


def _context_notes(component: Component) -> list[str]:
    notes = []
    if component.id == "gasket":
        notes.append("The gasket is generated from the crystal and caseback; it "
                     "has no independent writable field.")
    if component.id == "complications":
        notes.append("Changing the module set changes height, torque draw and "
                     "dial furniture; the solver and verifier run after the edit.")
    if component.id == "movement":
        notes.append("Movement dimensions are user inputs, not a manufacturer "
                     "datasheet. The solver derives the train from them.")
    if component.id in ("case", "lugs"):
        notes.append("Lug width and lug-to-lug also belong to the strap interface.")
    return notes
