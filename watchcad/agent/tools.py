"""Structured tools exposed to the reasoning layer.

The agent can inspect the deterministic design and request a small, explicit set
of mutations.  It cannot run Python, read the filesystem, or write arbitrary
mesh data.  Every mutating tool validates its arguments and operates on a
working copy; committing that copy to the live studio is the orchestrator's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from typing import Any, Callable

from ..build import build, slugify
from ..modules import MODULES
from ..nlp import describe
from ..schema import FIELDS, Spec
from ..verify import summary as verify_summary
from ..verify import verify


class ToolError(RuntimeError):
    """A tool rejected the request; the message is safe to show the user."""


class ToolDenied(ToolError):
    """Permission or policy refused the tool call."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    mutating: bool = False
    read_only: bool = True

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters, "mutating": self.mutating,
                "read_only": self.read_only}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


_STRING = {"type": "string"}
_BOOL = {"type": "boolean"}
_NUMBER = {"type": "number"}
_INT = {"type": "integer"}
_ANY = {}


TOOL_SPECS: dict[str, ToolSpec] = {
    "get_current_spec": ToolSpec(
        "get_current_spec",
        "Read the complete current specification. Use full=true only when every "
        "field is needed; the compact form includes the user-facing design plus locks.",
        _obj({"full": _BOOL}),
    ),
    "get_verification_report": ToolSpec(
        "get_verification_report",
        "Read all deterministic verification checks, their verdicts, details and "
        "suggested fixes. Optionally filter by layer or to failures only.",
        _obj({"layer": _STRING, "failures_only": _BOOL}),
    ),
    "inspect_component": ToolSpec(
        "inspect_component",
        "Inspect one component or train arbor: dimensions, bounds, mass, material, "
        "role, tooth counts and related stages. Name matching is case-insensitive.",
        _obj({"name": _STRING}, ["name"]),
    ),
    "get_field_dependencies": ToolSpec(
        "get_field_dependencies",
        "Explain which specification fields and verification checks depend on a "
        "given dotted path before changing it.",
        _obj({"path": _STRING}, ["path"]),
    ),
    "compare_specs": ToolSpec(
        "compare_specs",
        "Compare two design versions from history, or the previous version with "
        "the current one. Returns a path-level before/after diff.",
        _obj({"from_version": {"type": ["string", "integer"]},
              "to_version": {"type": ["string", "integer"]}}),
    ),
    "get_design_history": ToolSpec(
        "get_design_history",
        "Read recent versions, the design intent, locked fields and applied changes.",
        _obj({"limit": _INT}),
    ),
    "inspect_geometry": ToolSpec(
        "inspect_geometry",
        "Read the generated geometry tree, part bounds/masses, train layout, stack "
        "levels and assembly bounds. Optionally focus on one part name.",
        _obj({"part": _STRING, "include_bounds": _BOOL}),
    ),
    "update_spec": ToolSpec(
        "update_spec",
        "Set one validated specification field. Rejects derived fields, locked "
        "fields, unknown paths and values outside the field registry.",
        _obj({"path": _STRING, "value": _ANY, "note": _STRING}, ["path", "value"]),
        mutating=True, read_only=False,
    ),
    "apply_patch": ToolSpec(
        "apply_patch",
        "Apply a small RFC-6902-like patch list to the specification. Allowed ops: "
        "add, replace, remove, lock, unlock. Every path is validated before anything "
        "is written, so a rejected patch leaves the spec untouched.",
        _obj({"ops": {"type": "array", "items": {"type": "object"}},
              "note": _STRING}, ["ops"]),
        mutating=True, read_only=False,
    ),
    "run_solver": ToolSpec(
        "run_solver",
        "Recompute every derived dimension, the movement train, stack, mass and "
        "the geometry scene from the current specification.",
        _obj({}),
        mutating=False, read_only=False,
    ),
    "run_verification": ToolSpec(
        "run_verification",
        "Run the complete deterministic verification suite against the current "
        "solved specification and geometry.",
        _obj({}),
        mutating=False, read_only=False,
    ),
    "build_package": ToolSpec(
        "build_package",
        "Generate the production package: meshes, drawings, BOM, assembly sheet, "
        "test plan, DFM, verification report and checksummed manifest.",
        _obj({"per_part": _BOOL, "force": _BOOL}),
        mutating=False, read_only=False,
    ),
    "lookup_reference": ToolSpec(
        "lookup_reference",
        "Look up reference material, crystal, movement, tolerance or process data. "
        "Results state whether a value is verified reference or an estimate.",
        _obj({"query": _STRING, "category": _STRING}, ["query"]),
    ),
}


READ_ONLY = {name for name, spec in TOOL_SPECS.items() if spec.read_only}


@dataclass
class ToolContext:
    studio: Any
    session: Any = None
    build_root: str | None = None
    emit: Callable[[dict], None] | None = None
    control: Any = None
    max_calls: int = 80

    def check(self) -> None:
        if self.control is not None:
            self.control.check()


def openai_tools() -> list[dict]:
    return [spec.to_openai() for spec in TOOL_SPECS.values()]


def tool_catalog() -> list[dict]:
    return [spec.to_dict() for spec in TOOL_SPECS.values()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _allowed_path(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if path.startswith("derived."):
        return False
    if path in ("movement.complications", "meta.name", "meta.notes"):
        return True
    if path.startswith("movement.complications."):
        return True
    return path in FIELDS


def _check_lock(studio, path: str) -> None:
    if path in studio.spec.locks:
        raise ToolDenied(f"{path} is locked by the user and cannot be changed")


def _validate_value(studio, path: str, value) -> None:
    if not _allowed_path(path):
        raise ToolError(f"{path} is not a writable specification field")
    if path == "movement.complications":
        if not isinstance(value, dict):
            raise ToolError("movement.complications must be an object of module ids")
        unknown = [key for key in value if key not in MODULES]
        if unknown:
            raise ToolError("unknown complication(s): " + ", ".join(sorted(unknown)))
    candidate = studio.spec.copy()
    try:
        candidate.set(path, value, tag="agent", force=True)
    except Exception as exc:  # noqa: BLE001 - report the reason to the agent
        raise ToolError(f"could not set {path}: {exc}") from exc
    issues = candidate.validate()
    errors = [item for item in issues if item.get("level") == "error"
              and (item.get("path") == path or str(item.get("path", "")).startswith(path + "."))]
    if errors:
        raise ToolError(f"{path}: " + "; ".join(item["message"] for item in errors))


def _change_rows(before: Spec, after: Spec) -> list[dict]:
    rows = []
    for change in before.diff(after):
        path = change["path"]
        # Derived values follow from the spec; report only what the caller set.
        if path.startswith("derived.") or path == "meta.version":
            continue
        rows.append({"path": path, "from": change.get("from"),
                     "to": change.get("to")})
    return rows


def _summary_payload(studio) -> dict:
    return {
        "checksum": studio.solved.get("derived.checksum"),
        "summary": dict(studio.summary or {}),
        "failures": [c["id"] for c in (studio.checks or []) if c.get("passed") is False],
        "parts": len(studio.scene.get("parts", [])),
        "triangles": studio.scene.get("triangles", 0),
        "mass_g": studio.scene.get("mass_g", 0),
        "validation": studio.solved.validate(),
    }


def _clean_json(value):
    if isinstance(value, dict):
        return {str(k): _clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(v) for v in value]
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

def get_current_spec(ctx: ToolContext, full: bool = False, **_) -> dict:
    ctx.check()
    spec = ctx.studio.solved
    data = spec.to_dict().get("data", {})
    if not full:
        data = {key: data.get(key, {}) for key in
                ("case", "dial", "hands", "movement", "quartz", "meta")}
        data["derived"] = {
            key: (spec.get("derived." + key) or {})
            for key in ("stack", "barrel", "torque", "checksum",
                        "effective_power_reserve_h")
        }
        data["derived"]["train"] = _compact_train(spec.get("derived.train") or {})
    return {
        "spec": _clean_json(data),
        "locks": sorted(spec.locks),
        "summary": describe(spec),
        "version": spec.get("meta.version"),
        "checksum": spec.get("derived.checksum"),
        "intent": (getattr(ctx.session, "memory", None).intent
                   if ctx.session is not None else ""),
        "constraints": (list(getattr(ctx.session, "memory", None).constraints)
                        if ctx.session is not None else []),
    }


def _compact_train(train: dict) -> dict:
    if not isinstance(train, dict):
        return {}
    return {
        "module_mm": train.get("module_mm"),
        "stages": train.get("stages", []),
        "layout": train.get("layout", []),
        "barrel_to_centre": train.get("barrel_to_centre"),
        "centre_to_fourth": train.get("centre_to_fourth"),
        "fourth_to_escape": train.get("fourth_to_escape"),
        "effective_power_reserve_h": train.get("effective_power_reserve_h"),
        "total_barrel_to_escape": train.get("total_barrel_to_escape"),
    }


def get_verification_report(ctx: ToolContext, layer: str = "",
                            failures_only: bool = False, **_) -> dict:
    ctx.check()
    checks = list(ctx.studio.checks or [])
    if layer:
        checks = [c for c in checks if c.get("layer") == layer]
    if failures_only:
        checks = [c for c in checks if c.get("passed") is False]
    summary = verify_summary(ctx.studio.checks or [])
    return {
        "checks": _clean_json(checks),
        "summary": summary,
        "failures": [c for c in ctx.studio.checks if c.get("passed") is False],
        "warnings": [c for c in ctx.studio.checks
                     if c.get("verdict") in ("estimated", "needs_test")],
        "validation": ctx.studio.solved.validate(),
    }


def inspect_component(ctx: ToolContext, name: str, **_) -> dict:
    ctx.check()
    needle = str(name or "").strip().lower()
    if not needle:
        raise ToolError("component name is required")
    scene = ctx.studio.scene or {}
    parts = []
    for part in scene.get("parts", []):
        if needle in part.name.lower() or part.name.lower() in needle:
            parts.append({
                "name": part.name,
                "role": part.role,
                "material": getattr(part, "material", ""),
                "color": part.color,
                "bounds": part.bounds(),
                "volume_mm3": round(abs(part.volume()), 3),
                "triangles": len(part.faces),
                "meta": getattr(part, "meta", {}),
            })
    train = ctx.studio.solved.get("derived.train") or {}
    layout = [node for node in train.get("layout", [])
              if needle in str(node.get("name", "")).lower()
              or str(node.get("name", "")).lower() in needle]
    stages = [stage for stage in train.get("stages", [])
              if needle in str(stage.get("driver", "")).lower()
              or needle in str(stage.get("driven", "")).lower()]
    if not parts and not layout and not stages:
        available = sorted({p.name for p in scene.get("parts", [])})[:60]
        raise ToolError("no component matched '" + name + "'. Available: "
                        + ", ".join(available))
    result = {"query": name, "parts": parts[:24], "train_nodes": layout,
              "stages": stages}
    if parts:
        result["bounds"] = parts[0].get("bounds")
        result["mass_g"] = round(parts[0]["volume_mm3"]
                                 * _density(parts[0].get("material")), 3)
    return _clean_json(result)


def _density(material: str | None) -> float:
    return {"steel": 8.0, "brass": 8.5, "gold": 19.3, "jewel": 3.6,
            "crystal": 3.98, "dial": 2.7, "spring": 7.9,
            "titanium": 4.5, "rubber": 1.2}.get((material or "").lower(), 7.85)


DEPENDENCIES: dict[str, dict] = {
    "case.diameter_mm": {
        "affects": ["movement.width_mm", "dial.diameter_mm", "case.lug_to_lug_mm",
                    "hands.hour_length_mm", "hands.minute_length_mm",
                    "hands.second_length_mm", "derived.mass_g"],
        "checks": ["geometry.movement_fit", "geometry.dial_fit"],
        "why": "The bore is derived from the case diameter; movement, dial and hands follow it.",
    },
    "case.thickness_mm": {
        "affects": ["derived.stack.margin_mm", "derived.stack.z_case_top",
                    "derived.stack.z_crystal_bottom", "derived.mass_g"],
        "checks": ["geometry.stack", "geometry.hand_clearance",
                   "tolerance.stackup", "geometry.complication_stack"],
        "why": "The case must contain the caseback, movement, complications, dial, hands and crystal.",
    },
    "movement.height_mm": {
        "affects": ["derived.stack.required_case_thickness_mm",
                    "derived.stack.margin_mm"],
        "checks": ["geometry.stack", "tolerance.stackup"],
        "why": "Movement height is the largest fixed layer in the vertical stack.",
    },
    "movement.beat_rate_bph": {
        "affects": ["derived.train.fourth_to_escape", "derived.train.layout"],
        "checks": ["kinematics.fourth_wheel", "kinematics.beat_rate"],
        "why": "Escape wheel speed sets the fourth-to-escape tooth ratio.",
    },
    "movement.power_reserve_h": {
        "affects": ["derived.train.barrel_to_centre",
                    "derived.train.effective_power_reserve_h"],
        "checks": ["energy.power_reserve", "energy.power_budget"],
        "why": "Reserve divided by usable barrel turns sets the barrel-to-centre ratio.",
    },
    "movement.usable_barrel_turns": {
        "affects": ["derived.barrel", "derived.torque",
                    "derived.train.effective_power_reserve_h"],
        "checks": ["energy.power_budget", "energy.power_reserve", "energy.mainspring"],
        "why": "Turns change the mainspring energy budget and the first reduction.",
    },
    "movement.complications": {
        "affects": ["derived.stack.complications_mm",
                    "derived.stack.required_case_thickness_mm", "derived.torque",
                    "derived.mass_g"],
        "checks": ["spec.complication_conflicts", "physical.complications",
                   "geometry.complication_stack", "energy.power_budget"],
        "why": "Every module adds height, torque draw and geometry to the stack.",
    },
    "dial.diameter_mm": {
        "affects": ["hands.hour_length_mm", "hands.minute_length_mm",
                    "hands.second_length_mm"],
        "checks": ["geometry.dial_fit", "geometry.minute_length_mm",
                   "geometry.second_length_mm"],
        "why": "Hand lengths are fractions of the solved dial radius.",
    },
    "case.water_resistance_m": {
        "affects": ["case.caseback", "case.crown"],
        "checks": ["dfm.water_resistance"],
        "why": "Deep water resistance needs a screw-down caseback and crown.",
    },
    "case.lug_width_mm": {
        "affects": [],
        "checks": ["dfm.lug_width"],
        "why": "Strap makers support a small set of standard lug widths.",
    },
}


def get_field_dependencies(ctx: ToolContext, path: str, **_) -> dict:
    ctx.check()
    path = str(path or "")
    if not path:
        raise ToolError("path is required")
    info = DEPENDENCIES.get(path)
    if info is None:
        prefix = path.split(".")[0]
        related = [p for p in FIELDS if p.startswith(prefix + ".")]
        info = {"affects": [], "checks": [],
                "why": f"No explicit dependency edge is registered for {path}. "
                       f"It belongs to the {prefix} group; related fields are listed."}
        related = related[:40]
    else:
        related = []
    checks = [c for c in (ctx.studio.checks or [])
              if path == c["id"] or (c.get("fix") or {}).get("path") == path
              or c["id"] in info.get("checks", [])]
    return {
        "path": path,
        "depends_on": _depends_on(path),
        "affects": info.get("affects", []),
        "related_fields": related,
        "checks": [{"id": c["id"], "passed": c.get("passed"),
                    "detail": c.get("detail"), "fix": c.get("fix")}
                   for c in checks],
        "why": info.get("why", ""),
        "locked": path in ctx.studio.spec.locks,
    }


def _depends_on(path: str) -> list[str]:
    out = []
    for source, info in DEPENDENCIES.items():
        if path in info.get("affects", []):
            out.append(source)
    return out


def compare_specs(ctx: ToolContext, from_version=None, to_version=None, **_) -> dict:
    ctx.check()
    session = ctx.session
    if session is None:
        raise ToolError("compare_specs needs a design session with history")
    versions = list(session.versions)
    current = ctx.studio.spec
    if from_version in (None, "") and len(versions) >= 2:
        left = versions[-2]
    elif from_version in ("current", "now"):
        left = None
    elif from_version is None:
        left = versions[-1] if versions else None
    else:
        left = _find_version(versions, from_version)
    if to_version in (None, "", "current", "now"):
        right = None
    else:
        right = _find_version(versions, to_version)
    left_spec = Spec.from_dict(left.spec) if left else current.copy()
    right_spec = Spec.from_dict(right.spec) if right else current.copy()
    return {
        "from": left.to_dict() if left else {"label": "current", "checksum": None},
        "to": right.to_dict() if right else {"label": "current", "checksum": None},
        "changes": _clean_json(left_spec.diff(right_spec)),
    }


def _find_version(versions, key):
    for version in versions:
        if str(version.version_id) == str(key) or str(version.index) == str(key):
            return version
    raise ToolError(f"unknown version {key}")


def get_design_history(ctx: ToolContext, limit: int = 20, **_) -> dict:
    ctx.check()
    session = ctx.session
    if session is None:
        return {"history": [], "memory": {}, "can_undo": False, "can_redo": False}
    limit = max(1, min(50, int(limit or 20)))
    return {
        "history": session.history(limit),
        "memory": session.memory.to_dict(),
        "can_undo": session.can_undo(),
        "can_redo": session.can_redo(),
        "pending": bool(session.pending),
        "conversation": [m.to_dict() for m in session.conversation.recent(8)],
    }


def inspect_geometry(ctx: ToolContext, part: str = "", include_bounds: bool = True,
                     **_) -> dict:
    ctx.check()
    scene = ctx.studio.scene or {}
    spec = ctx.studio.solved
    tree = scene.get("tree", {})
    parts = []
    need = str(part or "").strip().lower()
    for item in scene.get("parts", []):
        if need and need not in item.name.lower():
            continue
        row = {"name": item.name, "role": item.role,
               "material": getattr(item, "material", ""),
               "color": item.color, "triangles": len(item.faces),
               "meta": _clean_json(getattr(item, "meta", {}))}
        if include_bounds:
            row["bounds"] = item.bounds()
        parts.append(row)
    train = spec.get("derived.train") or {}
    return {
        "tree": tree,
        "parts": parts[:120],
        "part_count": len(scene.get("parts", [])),
        "triangles": scene.get("triangles", 0),
        "mass_g": scene.get("mass_g", 0),
        "bounds": scene.get("bounds", {}),
        "train_layout": train.get("layout", []),
        "stages": train.get("stages", []),
        "stack": spec.get("derived.stack") or {},
        "summary": spec.summary(),
    }


# ---------------------------------------------------------------------------
# Action tools
# ---------------------------------------------------------------------------

def update_spec(ctx: ToolContext, path: str, value, note: str = "", **_) -> dict:
    ctx.check()
    path = str(path or "")
    if not path:
        raise ToolError("path is required")
    _check_lock(ctx.studio, path)
    _validate_value(ctx.studio, path, value)
    before = ctx.studio.spec.copy()
    if not ctx.studio.set(path, value, force=False):
        raise ToolDenied(f"{path} could not be written; it may be locked")
    changes = _change_rows(before, ctx.studio.spec)
    if not changes:
        return {"path": path, "value": value, "changes": [],
                "message": f"{path} already had that value", **_summary_payload(ctx.studio)}
    return {"path": path, "value": value, "changes": changes,
            "message": f"set {path}", **_summary_payload(ctx.studio)}


def apply_patch(ctx: ToolContext, ops: list[dict], note: str = "", **_) -> dict:
    ctx.check()
    if not isinstance(ops, list) or not ops:
        raise ToolError("ops must be a non-empty list")
    if len(ops) > 40:
        raise ToolError("a single patch may contain at most 40 operations")
    # Validate the whole batch before writing anything.
    cleaned: dict[str, dict] = {}
    lock_ops: list[dict] = []
    skipped: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            raise ToolError("every operation must be an object")
        kind = str(op.get("op", "replace"))
        path = str(op.get("path", ""))
        if not path:
            raise ToolError("every operation needs a path")
        if kind in ("lock", "unlock"):
            lock_ops.append({"op": kind, "path": path})
            continue
        if kind in ("add", "replace"):
            if path in ctx.studio.spec.locks:
                skipped.append(path)
                continue
            _validate_value(ctx.studio, path, op.get("value"))
            cleaned[path] = {"op": "replace", "path": path,
                             "value": op.get("value"),
                             "note": str(op.get("note") or note or "agent patch")}
        elif kind == "remove":
            if path in ctx.studio.spec.locks:
                skipped.append(path)
                continue
            cleaned[path] = {"op": "remove", "path": path,
                             "note": str(op.get("note") or note or "agent patch")}
        else:
            raise ToolError(f"unsupported patch op '{kind}'")
    patch_ops = list(cleaned.values()) + lock_ops
    if not patch_ops:
        return {"changes": [], "skipped_locked": sorted(set(skipped)),
                "message": "nothing to apply", **_summary_payload(ctx.studio)}
    before = ctx.studio.spec.copy()
    candidate = before.copy()
    for op in patch_ops:
        if op["op"] == "replace":
            candidate.set(op["path"], op.get("value"), tag="agent",
                          note=op.get("note", ""))
        elif op["op"] == "remove":
            parent, leaf = candidate._parent(op["path"])
            parent.pop(leaf, None)
            candidate.provenance.pop(op["path"], None)
        elif op["op"] == "lock":
            candidate.lock(op["path"], True)
        elif op["op"] == "unlock":
            candidate.lock(op["path"], False)
    ctx.studio.spec = candidate
    ctx.studio.last_build = None
    ctx.studio.rebuild()
    changes = _change_rows(before, ctx.studio.spec)
    return {"changes": changes, "ops": patch_ops,
            "skipped_locked": sorted(set(skipped)),
            "message": f"applied {len(changes)} change(s)",
            **_summary_payload(ctx.studio)}


def run_solver(ctx: ToolContext, **_) -> dict:
    ctx.check()
    ctx.studio.last_build = None
    ctx.studio.rebuild()
    train = ctx.studio.solved.get("derived.train") or {}
    stack = ctx.studio.solved.get("derived.stack") or {}
    return {
        "message": "solver completed",
        "checksum": ctx.studio.solved.get("derived.checksum"),
        "report": ctx.studio.report,
        "train": _compact_train(train),
        "stack": stack,
        "summary": dict(ctx.studio.summary or {}),
        "geometry": {"parts": len(ctx.studio.scene.get("parts", [])),
                     "triangles": ctx.studio.scene.get("triangles", 0),
                     "mass_g": ctx.studio.scene.get("mass_g", 0)},
    }


def run_verification(ctx: ToolContext, **_) -> dict:
    ctx.check()
    checks = verify(ctx.studio.solved, ctx.studio.scene)
    ctx.studio.checks = checks
    ctx.studio.summary = verify_summary(checks)
    failures = [c for c in checks if c.get("passed") is False]
    return {
        "message": f"{len(failures)} failing check(s)",
        "checks": _clean_json(checks),
        "failures": _clean_json(failures),
        "summary": dict(ctx.studio.summary),
        "validation": ctx.studio.solved.validate(),
    }


def build_package(ctx: ToolContext, per_part: bool = True, force: bool = False,
                  **_) -> dict:
    ctx.check()
    spec = ctx.studio.solved
    slug = slugify(spec.get("meta.name") or "watch")
    checksum = (spec.get("derived.checksum") or "")[:8]
    root = os.path.join(ctx.build_root or os.path.join("out"), f"{slug}-{checksum}")
    result = build(spec, out_dir=root, per_part=bool(per_part), force=bool(force),
                   scene=ctx.studio.scene, checks=ctx.studio.checks)
    result.files = [f for f in result.files if os.path.isfile(f)]
    return {
        "message": f"package generated with {len(result.files)} files",
        "build": result.to_dict(),
        "root": result.root,
        "files": [os.path.relpath(f, result.root) for f in result.files],
        "reused": result.reused,
    }


def lookup_reference(ctx: ToolContext, query: str, category: str = "", **_) -> dict:
    ctx.check()
    from .knowledge import lookup

    return lookup(query, category=category or None)


HANDLERS: dict[str, Callable[..., dict]] = {
    "get_current_spec": get_current_spec,
    "get_verification_report": get_verification_report,
    "inspect_component": inspect_component,
    "get_field_dependencies": get_field_dependencies,
    "compare_specs": compare_specs,
    "get_design_history": get_design_history,
    "inspect_geometry": inspect_geometry,
    "update_spec": update_spec,
    "apply_patch": apply_patch,
    "run_solver": run_solver,
    "run_verification": run_verification,
    "build_package": build_package,
    "lookup_reference": lookup_reference,
}
