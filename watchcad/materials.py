"""Material and finish library for the mesh, exporter and viewer.

The geometry kernel carries a *material key* per part.  This module is the one
place that maps a user-facing material/finish to render properties and a
density.  It contains no geometry and no dimensions.

Honesty note: these are presentation properties (colour, metalness, roughness,
texture pattern), not certified engineering data.  Density is a nominal value
for mass *estimation*; it is not a material certificate.  Structural, wear,
corrosion and biocompatibility claims still require real material data and
physical testing.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Material:
    key: str
    label: str
    density_g_cm3: float
    color: str
    metalness: float
    roughness: float
    transmission: float = 0.0
    opacity: float = 1.0
    ior: float = 1.5
    clearcoat: float = 0.0
    # texture: none | brushed | radial | guilloche | grain | weave
    texture: str = "none"
    note: str = "presentation property; not a material certificate"

    def to_dict(self) -> dict:
        return asdict(self)


# Values are nominal presentation values.  Densities follow common handbook
# figures (e.g. 316L ~8.0 g/cm3, Ti-6Al-4V ~4.43, 18k gold ~15.6).
MATERIALS: dict[str, Material] = {
    "steel": Material("steel", "316L stainless steel", 8.00, "#c9ced6", 0.92, 0.24,
                      texture="brushed"),
    "titanium": Material("titanium", "titanium", 4.43, "#b4bcc2", 0.86, 0.34,
                         texture="brushed"),
    "bronze": Material("bronze", "bronze", 8.80, "#b08d57", 0.88, 0.32,
                       texture="brushed"),
    "ceramic": Material("ceramic", "ceramic", 3.20, "#2b3037", 0.08, 0.22,
                        clearcoat=0.8, texture="none"),
    "carbon": Material("carbon", "carbon composite", 1.60, "#3a3f45", 0.18, 0.42,
                       texture="weave"),
    "gold": Material("gold", "18k yellow gold", 15.60, "#e0c068", 0.95, 0.20,
                     texture="polished"),
    "rose_gold": Material("rose_gold", "18k rose gold", 15.20, "#d9a08a", 0.95, 0.20,
                          texture="polished"),
    "brass": Material("brass", "brass", 8.50, "#c8a24a", 0.86, 0.30),
    "crystal": Material("crystal", "sapphire crystal", 3.98, "#b9dcea", 0.0, 0.04,
                        transmission=0.72, opacity=0.46, ior=1.77,
                        clearcoat=1.0, texture="none"),
    "mineral": Material("mineral", "mineral glass", 2.50, "#c7e2ea", 0.0, 0.10,
                        transmission=0.55, opacity=0.58, ior=1.52, texture="none"),
    "acrylic": Material("acrylic", "acrylic crystal", 1.18, "#d8eef2", 0.0, 0.16,
                        transmission=0.45, opacity=0.64, ior=1.49, texture="none"),
    "dial": Material("dial", "dial", 2.70, "#14171c", 0.20, 0.52, texture="radial"),
    "lume": Material("lume", "luminous compound", 3.60, "#d8f0b0", 0.0, 0.62,
                     texture="grain"),
    "jewel": Material("jewel", "synthetic ruby jewel", 3.98, "#b0304a", 0.05, 0.18,
                      transmission=0.18, opacity=0.9, texture="none"),
    "spring": Material("spring", "spring steel", 7.85, "#9fb4c8", 0.9, 0.28),
    "rubber": Material("rubber", "rubber", 1.20, "#20242a", 0.0, 0.78,
                       texture="grain"),
    "leather": Material("leather", "leather", 0.90, "#3b2a20", 0.0, 0.72,
                        texture="grain"),
    "nato": Material("nato", "woven nylon", 1.15, "#3f4b3a", 0.0, 0.86,
                     texture="weave"),
    "pcb": Material("pcb", "printed circuit board", 1.85, "#2f7a52", 0.05, 0.60,
                    texture="none"),
    "copper": Material("copper", "copper", 8.96, "#b7793f", 0.92, 0.28),
}

# Case material enum -> material key.  Keep this mapping in one place so the
# spec, BOM, mass estimate and viewer cannot disagree.
CASE_MATERIAL_KEY = {
    "316L stainless steel": "steel",
    "titanium": "titanium",
    "bronze": "bronze",
    "ceramic": "ceramic",
    "carbon": "carbon",
    "18k yellow gold": "gold",
    "18k rose gold": "rose_gold",
}

CASE_COLOR = {label: MATERIALS[key].color for label, key in CASE_MATERIAL_KEY.items()}

# Finish modifiers.  `texture` names a procedural pattern the viewer can build
# without external image assets; `roughness`/`metalness` are multipliers.
FINISHES: dict[str, dict] = {
    "brushed": {"roughness": 0.52, "metalness": 0.94, "texture": "brushed",
                "label": "brushed"},
    "polished": {"roughness": 0.10, "metalness": 0.98, "texture": "polished",
                 "label": "polished"},
    "satin": {"roughness": 0.40, "metalness": 0.92, "texture": "brushed",
              "label": "satin"},
    "bead-blasted": {"roughness": 0.68, "metalness": 0.86, "texture": "grain",
                     "label": "bead-blasted"},
    "PVD black": {"roughness": 0.30, "metalness": 0.72, "texture": "brushed",
                  "label": "PVD black"},
    "two-tone": {"roughness": 0.24, "metalness": 0.95, "texture": "brushed",
                 "label": "two-tone"},
    "matte": {"roughness": 0.78, "metalness": 0.05, "texture": "grain",
              "label": "matte"},
    "sunburst": {"roughness": 0.34, "metalness": 0.62, "texture": "radial",
                 "label": "sunburst"},
    "guilloche": {"roughness": 0.38, "metalness": 0.45, "texture": "guilloche",
                  "label": "guilloché"},
    "lacquered": {"roughness": 0.20, "metalness": 0.30, "texture": "none",
                  "label": "lacquered"},
    "sandblasted": {"roughness": 0.72, "metalness": 0.12, "texture": "grain",
                    "label": "sandblasted"},
}

DIAL_TEXTURE = {"matte": "grain", "sunburst": "radial", "guilloche": "guilloche",
                "lacquered": "none", "sandblasted": "grain"}


def case_key(label: str) -> str:
    return CASE_MATERIAL_KEY.get(label, "steel")


def material(key: str) -> Material:
    return MATERIALS.get(key, MATERIALS["steel"])


def density(key: str) -> float:
    return material(key).density_g_cm3


def finish(name: str) -> dict:
    return FINISHES.get(name, FINISHES["brushed"])


def dial_material(finish_name: str) -> Material:
    base = MATERIALS["dial"]
    tex = DIAL_TEXTURE.get(finish_name, base.texture)
    mod = FINISHES.get(finish_name, {})
    return Material(base.key, base.label, base.density_g_cm3, base.color,
                    mod.get("metalness", base.metalness),
                    mod.get("roughness", base.roughness), texture=tex)


def pbr(part) -> dict:
    """PBR payload for a mesh, from its material key and finish metadata.

    ``part`` only has to expose ``material``, ``meta`` and ``color`` attributes;
    this keeps the mesh kernel free of a dependency on the spec.
    """
    meta = getattr(part, "meta", {}) or {}
    key = str(meta.get("material_key") or getattr(part, "material", "steel"))
    base = material(key)
    finish_name = str(meta.get("finish") or "")
    mod = FINISHES.get(finish_name, {})
    role = str(getattr(part, "role", ""))
    color = str(getattr(part, "color", "") or base.color)
    texture = str(meta.get("texture") or mod.get("texture") or base.texture)
    if role == "crystal":
        texture = "none"
    return {
        "baseColor": color,
        "metalness": float(mod.get("metalness", base.metalness)),
        "roughness": float(mod.get("roughness", base.roughness)),
        "transmission": float(base.transmission),
        "opacity": float(base.opacity),
        "ior": float(base.ior),
        "clearcoat": float(base.clearcoat),
        "texture": texture,
        "material_key": key,
        "finish": finish_name or None,
        "provenance": "presentation material; nominal density used for mass estimate",
    }


def catalog() -> dict:
    return {
        "materials": {key: value.to_dict() for key, value in MATERIALS.items()},
        "case_materials": dict(CASE_MATERIAL_KEY),
        "finishes": FINISHES,
        "note": "Rendering properties and nominal densities are not material certification.",
    }
