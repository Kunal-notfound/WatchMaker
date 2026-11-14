"""Build orchestrator.

One call turns a spec into a complete package directory:

    spec.json / spec.md          source of truth and a readable copy
    model.stl/.obj/.gltf/.glb    assembly meshes (glTF 2.0 with normals)
    parts/<part>.stl             individual parts
    drawings/*.svg .dxf .pdf     front, section, train, notes
    bom.csv / bom.md             bill of materials
    assembly.md, test_plan.md    shop documents
    dfm.md, verification.md      honest engineering reports
    manifest.json                sha256 of every file

The build is deterministic: the output depends only on the solved spec.  A
matching checksum in an existing manifest short-circuits the rebuild.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field

from . import exporters, mesh as M
from .drawing import write_drawings
from .parts import build_scene
from .schema import Spec
from .solver import solve
from .verify import summary, verify


@dataclass
class BuildResult:
    root: str
    spec: Spec
    scene: dict
    checks: list[dict]
    summary: dict
    files: list[str] = field(default_factory=list)
    reused: bool = False
    manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "name": self.spec.get("meta.name"),
            "checksum": self.spec.get("derived.checksum"),
            "reused": self.reused,
            "files": [os.path.relpath(f, self.root) for f in self.files if os.path.isfile(f)],
            "summary": self.summary,
            "geometry": {
                "parts": len(self.scene.get("parts", [])),
                "triangles": self.scene.get("triangles", 0),
                "mass_g": self.scene.get("mass_g", 0),
                "bounds": self.scene.get("bounds", {}),
            },
            "manifest": self.manifest,
        }


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "watch").lower()).strip("-")
    return slug or "watch"


def step_status() -> dict:
    """Report whether a real STEP backend is available, without pretending."""
    available = []
    for module in ("build123d", "cadquery", "OCP"):
        try:
            __import__(module)
            available.append(module)
        except Exception:
            pass
    if available:
        return {
            "status": "available",
            "backend": available[0],
            "note": "install the STEP adapter in watchcad/step.py to emit B-rep files",
            "formats": ["STL", "OBJ", "glTF 2.0"],
        }
    return {
        "status": "unavailable",
        "reason": "running the zero-dependency kernel; no OpenCascade/B-rep backend installed",
        "install": "pip install build123d  (optional, needs ~500 MB)",
        "formats": ["STL", "OBJ", "glTF 2.0"],
        "consequence": "the package contains meshes, not STEP; drawing tolerances still apply",
    }


def prepare(spec: Spec) -> tuple[Spec, dict, list[dict]]:
    solved, _ = solve(spec)
    scene = build_scene(solved)
    checks = verify(solved, scene)
    return solved, scene, checks


def build(spec: Spec, out_dir: str | None = None, name: str | None = None,
          per_part: bool = True, force: bool = False,
          scene: dict | None = None, checks: list[dict] | None = None) -> BuildResult:
    solved = spec
    if not solved.get("derived.stack"):
        solved, _ = solve(solved)
    if scene is None:
        scene = build_scene(solved)
    if checks is None:
        checks = verify(solved, scene)
    check_summary = summary(checks)
    checksum = solved.get("derived.checksum", "")
    slug = slugify(name or solved.get("meta.name") or "watch")
    root = os.path.abspath(out_dir or os.path.join("out", slug))

    if not force and os.path.isfile(os.path.join(root, "manifest.json")):
        try:
            with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
                existing = json.load(fh)
            if existing.get("checksum") == checksum and check_summary["fail"] == 0:
                files = [os.path.join(root, f["path"]) for f in existing.get("files", [])]
                if files and all(os.path.isfile(f) for f in files):
                    return BuildResult(root, solved, scene, checks, check_summary,
                                       files=files, reused=True, manifest=existing)
        except Exception:
            pass

    os.makedirs(root, exist_ok=True)
    files: list[str] = []

    def add(path: str) -> str:
        files.append(path)
        return path

    # --- source of truth --------------------------------------------------
    add(exporters.write_text(os.path.join(root, "spec.json"), solved.to_json()))
    add(exporters.write_text(os.path.join(root, "spec.md"), exporters.spec_markdown(solved)))

    # --- meshes -----------------------------------------------------------
    parts = scene["parts"]
    merged = M.merge(parts, name=slug)
    add(exporters.write_bytes(os.path.join(root, "model.stl"), M.to_stl(parts)))
    add(exporters.write_text(os.path.join(root, "model.obj"), M.to_obj(parts)))
    gltf = M.to_gltf(parts, slug)
    add(exporters.write_text(os.path.join(root, "model.gltf"), json.dumps(gltf)))
    add(exporters.write_bytes(os.path.join(root, "model.glb"), M.to_glb(parts, slug)))
    if per_part:
        part_dir = os.path.join(root, "parts")
        os.makedirs(part_dir, exist_ok=True)
        written = set()
        for p in parts:
            fname = slugify(p.name) + ".stl"
            if fname in written:
                continue
            written.add(fname)
            add(exporters.write_bytes(os.path.join(part_dir, fname), M.to_stl([p])))

    # --- drawings ---------------------------------------------------------
    for key, path in write_drawings(solved, root).items():
        add(path)

    # --- documents --------------------------------------------------------
    add(exporters.write_bom(solved, scene, root)[0])
    add(exporters.write_bom(solved, scene, root)[1])
    add(exporters.write_text(os.path.join(root, "assembly.md"),
                             exporters.assembly_markdown(solved, scene, checks)))
    add(exporters.write_text(os.path.join(root, "test_plan.md"),
                             exporters.test_plan_markdown(solved, checks)))
    add(exporters.write_text(os.path.join(root, "dfm.md"),
                             exporters.dfm_markdown(solved, checks)))
    add(exporters.write_text(os.path.join(root, "verification.md"),
                             exporters.verification_markdown(solved, checks, check_summary)))
    add(exporters.write_text(os.path.join(root, "checks.json"),
                             json.dumps({"checksum": checksum, "summary": check_summary,
                                         "checks": checks}, indent=2)))
    step = step_status()
    add(exporters.write_text(os.path.join(root, "step_status.json"), json.dumps(step, indent=2)))
    add(exporters.write_text(os.path.join(root, "README.md"), exporters.package_readme(solved)))

    # --- manifest (last, so every file is covered) ------------------------
    manifest = exporters.manifest(solved, files, check_summary, scene, root, step)
    add(exporters.write_text(os.path.join(root, "manifest.json"),
                             json.dumps(manifest, indent=2)))

    return BuildResult(root, solved, scene, checks, check_summary,
                       files=files, reused=False, manifest=manifest)


def build_json(spec_json: str, out_dir: str | None = None, **kw) -> BuildResult:
    return build(Spec.from_json(spec_json), out_dir=out_dir, **kw)
