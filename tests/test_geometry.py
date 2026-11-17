"""Phase 1 geometry, material and render-preset tests.

These tests pin the contract of the parametric layer: quality presets change
triangle counts only, material properties come from one library, the generated
scene keeps its layer order, and the scene-level validator catches missing,
orphaned or colliding parts.  They do not certify manufacturability.
"""

from __future__ import annotations

import copy
import json
import struct
import unittest

from watchcad import materials, mesh as M
from watchcad.nlp import parse
from watchcad.parts import build_scene, dial_color
from watchcad.schema import FIELDS, Spec, new_spec
from watchcad.solver import solve
from watchcad.verify import summary, verify

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")


def solved(text: str = DIVER) -> Spec:
    spec, _ = solve(parse(text, name="GeometryTest").spec)
    return spec


class MaterialTests(unittest.TestCase):
    def test_density_and_case_mapping_are_central(self):
        self.assertAlmostEqual(materials.density("steel"), 8.0)
        self.assertGreater(materials.density("gold"), materials.density("titanium"))
        self.assertEqual(materials.case_key("titanium"), "titanium")
        self.assertEqual(materials.case_key("18k rose gold"), "rose_gold")
        # An unknown label must fall back explicitly, never invent a material.
        self.assertEqual(materials.case_key("unobtainium"), "steel")

    def test_pbr_uses_finish_and_material(self):
        part = M.Mesh("case_mid", [(0, 0, 0)], [])
        part.color = materials.CASE_COLOR["titanium"]
        part.material = "titanium"
        part.role = "case"
        part.meta = {"material_key": "titanium", "finish": "bead-blasted"}
        surface = materials.pbr(part)
        self.assertEqual(surface["material_key"], "titanium")
        self.assertEqual(surface["finish"], "bead-blasted")
        self.assertEqual(surface["texture"], "grain")
        self.assertGreater(surface["roughness"], 0.5)
        self.assertIn("presentation material", surface["provenance"])
        self.assertIn("nominal density", surface["provenance"])

    def test_crystal_is_transmissive(self):
        part = M.Mesh("crystal", [(0, 0, 0)], [])
        part.material = "crystal"
        part.role = "crystal"
        part.meta = {"material_key": "crystal"}
        surface = materials.pbr(part)
        self.assertGreater(surface["transmission"], 0.5)
        self.assertLess(surface["opacity"], 0.8)
        self.assertGreater(surface["ior"], 1.6)

    def test_schema_exposes_new_geometry_and_finish_fields(self):
        for path in ("case.crown_guards", "case.lug_style",
                     "case.crystal_dome_height_mm", "case.finish",
                     "dial.lume_color", "dial.date_window_position",
                     "dial.index_material"):
            self.assertIn(path, FIELDS)
        self.assertIn("18k yellow gold", FIELDS["case.material"]["choices"])
        self.assertIn("satin", FIELDS["case.finish"]["choices"])


class MeshPrimitiveTests(unittest.TestCase):
    def test_lens_has_positive_volume_and_flat_base(self):
        lens = M.lens(10.0, 1.0, 1.2, edge=0.15)
        self.assertGreater(lens.volume(), 0)
        self.assertAlmostEqual(lens.bounds()["min"][2], 1.0, places=6)
        self.assertAlmostEqual(lens.bounds()["max"][2], 2.2, places=3)

    def test_chamfered_disc_and_arc_ring_are_closed(self):
        disc = M.chamfered_disc(8.0, 0.0, 0.5, 0.1)
        self.assertGreater(disc.volume(), 0)
        arc = M.arc_ring(3.0, 5.0, 0.0, 0.2, start_deg=-35, end_deg=215)
        self.assertGreater(arc.volume(), 0)
        knurl = M.knurled_disc(3.0, 0.0, 2.0, teeth=30)
        self.assertGreater(knurl.volume(), 0)


class RenderPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = solved()
        cls.scenes = {q: build_scene(cls.spec, q) for q in ("low", "medium", "high")}

    def test_quality_reduces_triangles_without_changing_the_spec(self):
        before = self.spec.get("derived.checksum")
        self.assertLess(self.scenes["low"]["triangles"], self.scenes["high"]["triangles"])
        self.assertLess(self.scenes["medium"]["triangles"], self.scenes["high"]["triangles"])
        self.assertEqual(self.spec.get("derived.checksum"), before)
        for scene in self.scenes.values():
            self.assertEqual(scene["bounds"]["size"][2],
                             self.scenes["high"]["bounds"]["size"][2])

    def test_quality_keeps_the_layer_set(self):
        expected = {"case_mid", "bezel", "caseback", "crystal", "dial",
                    "hour_hand", "minute_hand", "second_hand", "mainplate"}
        for scene in self.scenes.values():
            names = {part.name for part in scene["parts"]}
            self.assertTrue(expected <= names, expected - names)

    def test_low_quality_mass_is_close(self):
        low = self.scenes["low"]["mass_g"]
        high = self.scenes["high"]["mass_g"]
        self.assertLess(abs(low - high) / high, 0.02,
                        "tessellation must not move the mass estimate materially")

    def test_verification_is_identical_across_presets(self):
        checks = {q: verify(self.spec, scene) for q, scene in self.scenes.items()}
        ids = {q: [(c["id"], c["passed"]) for c in items] for q, items in checks.items()}
        self.assertEqual(ids["low"], ids["high"])
        self.assertEqual(summary(checks["low"])["fail"], 0)

    def test_glb_carries_uvs_and_pbr_extras(self):
        data = M.to_glb(self.scenes["low"]["parts"])
        self.assertEqual(data[:4], b"glTF")
        json_length = struct.unpack("<I", data[12:16])[0]
        doc = json.loads(data[20:20 + json_length])
        attributes = set()
        for item in doc["meshes"]:
            attributes |= set(item["primitives"][0]["attributes"])
        self.assertIn("TEXCOORD_0", attributes)
        dial = next(m for m in doc["materials"] if m["name"] == "dial")
        self.assertIn("texture", dial["extras"])
        self.assertIn("provenance", dial["extras"])
        crystal = next(m for m in doc["materials"] if m["name"] == "crystal")
        self.assertEqual(crystal["alphaMode"], "BLEND")
        self.assertIn("transmission", crystal["extras"])


class GeometryFeatureTests(unittest.TestCase):
    def test_case_features_are_spec_driven(self):
        spec = solved()
        spec.set("case.crown_guards", True)
        spec.set("case.lug_style", "drilled")
        spec.set("case.bezel", "dive")
        spec.set("case.caseback", "screw-down exhibition")
        spec, _ = solve(spec)
        parts = build_scene(spec)
        names = [part.name for part in parts["parts"]]
        self.assertIn("crown_guard", names)
        self.assertIn("lug_pin", names)
        self.assertIn("bezel_insert", names)
        self.assertIn("caseback_glass", names)
        self.assertEqual(summary(verify(spec, parts))["fail"], 0)

    def test_dial_styles_build_real_furniture(self):
        spec = solved()
        spec.set("dial.index_style", "roman numerals")
        spec.set("dial.date_window", True)
        spec.set("dial.date_window_position", "6")
        spec.set("dial.small_seconds", True)
        spec, _ = solve(spec)
        parts = build_scene(spec)
        names = [part.name for part in parts["parts"]]
        self.assertIn("numeral", names)
        self.assertIn("date_frame", names)
        self.assertIn("small_seconds", names)
        self.assertEqual(summary(verify(spec, parts))["fail"], 0)

    def test_flat_and_domed_crystals_have_different_profiles(self):
        spec = solved()
        spec.set("case.crystal_type", "flat")
        spec, _ = solve(spec)
        flat = [p for p in build_scene(spec)["parts"] if p.name == "crystal"][0]
        spec.set("case.crystal_type", "domed")
        spec, _ = solve(spec)
        domed = [p for p in build_scene(spec)["parts"] if p.name == "crystal"][0]
        self.assertAlmostEqual(flat.bounds()["max"][2], flat.bounds()["min"][2]
                               + float(spec.get("case.crystal_thickness_mm")), places=2)
        self.assertAlmostEqual(domed.bounds()["max"][2], domed.bounds()["min"][2]
                               + float(spec.get("case.crystal_thickness_mm")), places=2)

    def test_missing_component_is_reported(self):
        spec = solved()
        scene = build_scene(spec)
        scene["parts"] = [p for p in scene["parts"] if p.name != "dial"]
        checks = verify(spec, scene)
        failed = {c["id"]: c for c in checks if c["passed"] is False}
        self.assertIn("geometry.components_present", failed)
        self.assertIn("dial", failed["geometry.components_present"]["detail"])

    def test_orphan_part_and_reversed_layer_are_reported(self):
        spec = solved()
        scene = build_scene(spec)
        orphan = M.box(200, 200, 0, 201, 201, 1)
        orphan.name, orphan.role = "stray", "case"
        scene["parts"].append(orphan)
        checks = verify(spec, scene)
        failed = {c["id"] for c in checks if c["passed"] is False}
        self.assertIn("geometry.part_envelope", failed)

        shifted = build_scene(spec)
        dial = next(p for p in shifted["parts"] if p.name == "dial")
        dial.translated(0, 0, 20)
        checks = verify(spec, shifted)
        failed = {c["id"] for c in checks if c["passed"] is False}
        self.assertIn("geometry.layer_order", failed)
        layer = next(c for c in checks if c["id"] == "geometry.layer_order")
        self.assertIsInstance(layer["fix"], dict)

    def test_thin_case_still_reports_a_fix(self):
        spec = new_spec("Thin")
        spec.set("case.thickness_mm", 5.0)
        spec, _ = solve(spec)
        checks = verify(spec, build_scene(spec))
        failures = [c for c in checks if c["passed"] is False]
        self.assertTrue(failures)
        for item in failures:
            self.assertIsInstance(item.get("fix"), dict, item["id"])


if __name__ == "__main__":
    unittest.main()
