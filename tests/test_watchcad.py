"""End-to-end tests for the zero-dependency watch engine.

Run:

    python3 -m unittest discover -s tests -v

The suite deliberately avoids third-party packages.  It checks the parts that
must stay honest: exact gear ratios, spec provenance and locks, layered
verification, the drawing exports and the built package manifest.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest

from watchcad import exporters, gears, mesh as M
from watchcad.build import build
from watchcad.drawing import drawing_set, to_dxf, to_svg, write_pdf
from watchcad.modules import compose
from watchcad.nlp import ideas, parse
from watchcad.parts import build_scene
from watchcad.schema import Spec, new_spec
from watchcad.solver import solve
from watchcad.verify import check, summary, verify

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")
DRESS = ("A 38 mm polished steel dress watch, silver sunburst dial with applied "
         "batons, dauphine hands, manual wind movement, 48 hour power reserve, no date.")


def solved(prompt: str = DIVER) -> Spec:
    spec, _ = solve(parse(prompt).spec)
    return spec


class SchemaTests(unittest.TestCase):
    def test_defaults_roundtrip(self):
        spec = new_spec("Roundtrip", case__diameter_mm=39.5)
        self.assertEqual(spec.get("case.diameter_mm"), 39.5)
        self.assertEqual(spec.get("meta.name"), "Roundtrip")
        clone = Spec.from_json(spec.to_json())
        self.assertEqual(clone.data, spec.data)
        self.assertEqual(clone.locks, spec.locks)

    def test_lock_refuses_write_and_patch(self):
        spec = new_spec()
        spec.set("case.diameter_mm", 40.0)
        spec.lock("case.diameter_mm", True)
        self.assertFalse(spec.set("case.diameter_mm", 44.0))
        patched = spec.patch([{"op": "replace", "path": "case.diameter_mm", "value": 44}])
        self.assertEqual(patched.get("case.diameter_mm"), 40.0)
        self.assertTrue(spec.set("case.diameter_mm", 44.0, force=True))
        self.assertEqual(spec.get("case.diameter_mm"), 44.0)

    def test_provenance_and_version(self):
        spec = new_spec()
        version = spec.get("meta.version")
        spec.set("dial.color", "blue", tag="user", note="typed")
        self.assertEqual(spec.tag("dial.color"), "user")
        self.assertEqual(spec.provenance["dial.color"]["note"], "typed")
        self.assertGreater(spec.get("meta.version"), version)

    def test_diff_and_patch(self):
        a = new_spec("A")
        b = a.copy()
        b.set("case.diameter_mm", 41.0)
        changes = a.diff(b)
        self.assertIn({"path": "case.diameter_mm", "from": 40.0, "to": 41.0}, changes)
        restored = a.patch([{"op": "replace", "path": "case.diameter_mm", "value": 41.0}])
        self.assertEqual(restored.get("case.diameter_mm"), 41.0)

    def test_validation_catches_bad_values(self):
        spec = new_spec()
        spec.set("case.diameter_mm", 400)
        spec.set("hands.style", "banana")
        messages = [item["message"] for item in spec.validate()]
        self.assertTrue(any("maximum" in m for m in messages))
        self.assertTrue(any("not one of" in m for m in messages))


class GearTests(unittest.TestCase):
    def test_exact_ratio_60_over_two_meshes(self):
        solution = gears.best_ratio(60, 2, 0.12, 12.0)
        self.assertIsNotNone(solution)
        self.assertAlmostEqual(solution.actual_ratio, 60.0, places=8)
        self.assertLess(solution.error_ppm, 0.01)
        self.assertEqual(len(solution.stages), 2)

    def test_ratio_is_exact_fraction(self):
        solution = gears.best_ratio(12, 1, 0.12, 12.0)
        self.assertIsNotNone(solution)
        self.assertEqual(solution.actual_ratio, 12.0)
        self.assertEqual(solution.error_ppm, 0.0)

    def test_tooth_profile_is_closed_and_bounded(self):
        wheel = gears.gear_profile(teeth=60, module=0.12)
        points = wheel["points"]
        radii = [math.hypot(x, y) for x, y in points]
        self.assertEqual(len(points), 60 * 20)
        self.assertAlmostEqual(min(radii), wheel["r_root"])
        self.assertAlmostEqual(max(radii), wheel["r_tip"])
        self.assertLess(wheel["r_root"], wheel["r_pitch"])
        self.assertLess(wheel["r_pitch"], wheel["r_tip"])

    def test_meshing_outlines_interleave_and_catch_bad_phase(self):
        wheel, pinion, contact = gears.meshing_outlines(
            60, 6, 0.12, (0.0, 0.0), (3.96, 0.0))
        window = 0.36
        self.assertFalse(gears.polygons_intersect(
            wheel, pinion, near=contact, window=window))
        # Same rotation with an even pinion puts tooth against tooth.
        unphased, _ = gears.profile_polygon(6, 0.12, 0.0, (3.96, 0.0))
        self.assertTrue(gears.polygons_intersect(
            wheel, unphased, near=contact, window=window))


class MeshTests(unittest.TestCase):
    def test_box_volume(self):
        box = M.box(0, 0, 0, 10, 8, 4)
        self.assertAlmostEqual(abs(box.volume()), 320.0, places=3)
        self.assertEqual(len(box.faces), 16)

    def test_tube_volume_and_winding(self):
        part = M.tube(4, 5, 0, 3, segments=64)
        volume = part.volume()
        self.assertGreater(volume, 0)
        self.assertAlmostEqual(volume, math.pi * (25 - 16) * 3, delta=0.2)

    def test_exporters(self):
        part = M.disc(5, 0, 2, segments=24)
        stl = M.to_stl([part])
        self.assertEqual(len(stl), 84 + len(part.faces) * 50)
        text = M.to_obj([part])
        self.assertIn("v ", text)
        self.assertIn("f ", text)
        gltf = M.to_gltf([part], "test")
        self.assertEqual(gltf["asset"]["version"], "2.0")
        self.assertGreater(len(gltf["meshes"]), 0)


class SolverTests(unittest.TestCase):
    def test_mechanical_is_solvable_and_ordered(self):
        spec = solved()
        self.assertEqual(spec.get("movement.kind"), "mechanical")
        checks = verify(spec, build_scene(spec))
        self.assertEqual(summary(checks)["fail"], 0)
        stack = spec.get("derived.stack")
        names = ["z_caseback_top", "z_plate_top", "z_movement_top",
                 "z_complications_top", "z_dial_bottom", "z_crystal_bottom", "z_case_top"]
        z = [stack[name] for name in names]
        self.assertEqual(z, sorted(z))
        train = spec.get("derived.train")
        self.assertGreaterEqual(len(train["stages"]), 4)
        self.assertIn("layout", train)

    def test_gears_and_centre_distances_agree(self):
        spec = solved()
        scene = build_scene(spec)
        stage_nodes = {}
        for stage in spec.get("derived.train")["stages"]:
            stage_nodes[(stage["driver"], stage["driven"])] = stage["centre_mm"]
        layout = {n["name"]: n for n in spec.get("derived.train")["layout"]}
        for (driver, driven), centre in stage_nodes.items():
            if driver in layout and driven in layout:
                actual = math.hypot(layout[driver]["x"] - layout[driven]["x"],
                                    layout[driver]["y"] - layout[driven]["y"])
                self.assertAlmostEqual(actual, centre, delta=0.02)
        self.assertGreater(scene["triangles"], 1000)

    def test_quartz_path(self):
        spec, _ = solve(parse("A 36 mm titanium quartz watch, white dial, date.").spec)
        self.assertEqual(spec.get("movement.kind"), "quartz")
        self.assertFalse(spec.get("derived.train"))
        self.assertGreater(spec.get("derived.battery_life_years"), 1.0)
        checks = verify(spec, build_scene(spec))
        self.assertEqual(summary(checks)["fail"], 0)

    def test_checksum_changes_with_spec(self):
        a = solved()
        b = solved()
        self.assertEqual(a.get("derived.checksum"), b.get("derived.checksum"))
        b.set("case.diameter_mm", 41.0)
        b, _ = solve(b)
        self.assertNotEqual(a.get("derived.checksum"), b.get("derived.checksum"))


class PartsTests(unittest.TestCase):
    def test_hands_sit_above_the_dial(self):
        scene = build_scene(solved())
        dial_top = max(part.bounds()["max"][2] for part in scene["parts"]
                       if part.role == "dial")
        hands = [part for part in scene["parts"] if part.role == "hand"]
        self.assertTrue(hands)
        lowest_hand = min(part.bounds()["min"][2] for part in hands)
        self.assertGreater(lowest_hand, dial_top,
                           "hands must sit above the dial, not at the caseback")

    def test_scene_has_vertical_layers(self):
        scene = build_scene(solved())
        centres = {(part.bounds()["min"][2] + part.bounds()["max"][2]) / 2
                   for part in scene["parts"]}
        self.assertGreater(len(centres), 4)
        self.assertIn("Movement", scene["tree"])
        self.assertIn("Dial", scene["tree"])


class VerifyTests(unittest.TestCase):
    def test_verdicts_are_layered(self):
        spec = solved()
        checks = verify(spec, build_scene(spec))
        verdicts = {c["verdict"] for c in checks}
        self.assertTrue(verdicts <= {"calculated", "estimated", "needs_test", "fail"})
        self.assertIn("calculated", verdicts)
        self.assertIn("estimated", verdicts)
        self.assertIn("needs_test", verdicts)
        totals = summary(checks)
        self.assertEqual(totals["total"], len(checks))
        self.assertEqual(totals["fail"], sum(1 for c in checks if c["passed"] is False))

    def test_failures_carry_a_fix(self):
        spec = new_spec("Thin")
        spec.set("case.thickness_mm", 5.0)
        spec.set("case.diameter_mm", 40.0)
        spec, _ = solve(spec)
        checks = verify(spec, build_scene(spec))
        failures = [c for c in checks if c["passed"] is False]
        self.assertTrue(failures)
        for item in failures:
            fix = item.get("fix")
            self.assertIsInstance(fix, dict, item["id"])
            self.assertTrue(fix.get("path") or fix.get("message"), item["id"])

    def test_complication_conflict_is_reported(self):
        spec = new_spec()
        spec.set("movement.complications", {"chronograph": {}, "gmt": {}})
        spec, _ = solve(spec)
        checks = verify(spec, build_scene(spec))
        self.assertIn("spec.complication_conflicts",
                      [c["id"] for c in checks if c["passed"] is False])

    def test_default_meshing_teeth_do_not_interfere(self):
        spec = solved()
        checks = verify(spec, build_scene(spec))
        clearance = next(c for c in checks if c["id"] == "geometry.gear_interference")
        self.assertTrue(clearance["passed"], clearance["detail"])
        self.assertIn("clear", clearance["detail"])


class DrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = solved()
        cls.canvases = drawing_set(cls.spec)

    def test_four_views(self):
        self.assertEqual(list(self.canvases), ["front", "section", "train", "notes"])

    def test_svg_and_dxf(self):
        svg = to_svg(self.canvases["front"])
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)
        dxf = to_dxf(list(self.canvases.values()))
        self.assertIn("SECTION", dxf)
        self.assertIn("ENTITIES", dxf)
        self.assertIn("TEXT", dxf)
        self.assertIn("EOF", dxf)

    def test_pdf_has_four_pages_and_real_text(self):
        import re
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "drawing.pdf")
            write_pdf(list(self.canvases.values()), path, title="test")
            with open(path, "rb") as handle:
                data = handle.read()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertEqual(len(re.findall(rb"/Type /Page\b", data)), 4)
        self.assertIn(b"Tj", data)
        self.assertIn(b"BT", data)


class BuildTests(unittest.TestCase):
    def test_package_and_cache(self):
        spec = solved()
        with tempfile.TemporaryDirectory() as tmp:
            result = build(spec, out_dir=tmp, per_part=True)
            files = {os.path.relpath(path, tmp) for path in result.files}
            for required in ("spec.json", "model.stl", "model.obj", "model.gltf",
                             "drawings/front.svg", "drawings/section.svg",
                             "drawings/train.svg", "drawings/notes.svg",
                             "drawings/watch_sheet.dxf", "drawings/watch_drawings.pdf",
                             "bom.csv", "bom.md", "assembly.md", "test_plan.md",
                             "dfm.md", "verification.md", "manifest.json",
                             "step_status.json", "README.md"):
                self.assertIn(required, files)
            self.assertTrue(any(name.startswith("parts/") for name in files))
            with open(os.path.join(tmp, "manifest.json")) as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["checksum"], spec.get("derived.checksum"))
            for row in manifest["files"]:
                path = os.path.join(tmp, row["path"])
                self.assertTrue(os.path.isfile(path), row["path"])
            self.assertEqual(manifest["step"]["status"], "unavailable")
            again = build(spec, out_dir=tmp, per_part=True)
            self.assertTrue(again.reused)


class NlpTests(unittest.TestCase):
    def test_diver_brief(self):
        result = parse(DIVER)
        spec = result.spec
        self.assertEqual(spec.get("case.diameter_mm"), 42.0)
        self.assertEqual(spec.get("case.water_resistance_m"), 300)
        self.assertEqual(spec.get("case.crown"), "screw-down")
        self.assertEqual(spec.get("dial.finish"), "matte")
        self.assertEqual(list(spec.get("movement.complications")), ["date"])
        self.assertEqual(result.engine, "rules")

    def test_no_date_removes_a_base_complication(self):
        base = parse(DIVER).spec
        result = parse("make it a dress watch, no date, 36 mm", base=base)
        self.assertEqual(result.spec.get("dial.date_window"), False)
        self.assertEqual(result.spec.get("movement.complications"), {})

    def test_locked_fields_are_skipped(self):
        base = new_spec()
        base.set("case.diameter_mm", 40.0)
        base.lock("case.diameter_mm")
        result = parse("a 44 mm pilot watch", base=base)
        self.assertEqual(result.spec.get("case.diameter_mm"), 40.0)
        self.assertIn("case.diameter_mm", result.skipped)

    def test_conflicting_complications_drop_one(self):
        result = parse("40 mm steel chronograph with a gmt complication")
        chosen = list(result.spec.get("movement.complications"))
        self.assertIn("chronograph", chosen)
        self.assertNotIn("gmt", chosen)
        self.assertTrue(any("conflict" in note for note in result.notes))

    def test_quartz_brief_has_battery_questions(self):
        result = parse("36 mm titanium quartz watch, white dial")
        self.assertEqual(result.spec.get("movement.kind"), "quartz")
        self.assertFalse(any("power reserve" in q.lower() for q in result.questions))
        self.assertLessEqual(len(result.questions), 4)

    def test_ideas_are_parseable(self):
        for item in ideas():
            result = parse(item["prompt"])
            self.assertIn(result.engine, {"rules", "rules+llm"})
            self.assertTrue(result.spec.get("meta.name") is not None)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer

        from watchcad import api

        cls.tmp = tempfile.TemporaryDirectory()
        api.BUILD_ROOT = __import__("pathlib").Path(cls.tmp.name)
        api.STUDIO = api.Studio(parse(DIVER, name="ApiTest").spec)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.urllib = urllib.request

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with self.urllib.urlopen(self.base + path) as response:
            return response.status, response.read()

    def post(self, path, body):
        request = self.urllib.Request(self.base + path,
                                      data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json"})
        with self.urllib.urlopen(request) as response:
            return response.status, json.loads(response.read())

    def test_full_session(self):
        status, raw = self.get("/api/state")
        state = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(state["spec"]["data"]["meta"]["name"], "ApiTest")
        self.assertGreater(state["scene"]["parts"], 50)

        status, schema = self.get("/api/schema")
        schema = json.loads(schema)
        self.assertIn("case", schema["groups"])
        self.assertTrue(schema["complications"])

        payload = json.loads(self.get("/api/scene")[1])
        self.assertGreater(payload["triangles"], 1000)
        self.assertTrue(payload["parts"][0]["verts"])

        status, state = self.post("/api/set", {"path": "case.diameter_mm", "value": 41.0})
        self.assertEqual(state["spec"]["data"]["case"]["diameter_mm"], 41.0)

        self.post("/api/lock", {"path": "case.diameter_mm", "locked": True})
        try:
            self.post("/api/set", {"path": "case.diameter_mm", "value": 44.0})
            self.fail("locked write should fail")
        except self.urllib.HTTPError as error:
            self.assertEqual(error.code, 409)
            error.close()
        self.post("/api/lock", {"path": "case.diameter_mm", "locked": False})

        status, state = self.post("/api/prompt", {"prompt": "make it 36 mm and quartz",
                                                  "use_llm": False})
        self.assertEqual(state["spec"]["data"]["movement"]["kind"], "quartz")
        self.assertIn("questions", state)

        status, state = self.post("/api/build", {"per_part": True})
        self.assertGreater(len(state["build"]["files"]), 40)
        sample = state["build"]["files"][0]
        status, raw = self.get("/api/download/file?path=" + sample)
        self.assertEqual(status, 200)
        status, raw = self.get("/api/download/zip")
        self.assertTrue(raw.startswith(b"PK"))

        try:
            self.get("/api/download/file?path=../../etc/passwd")
            self.fail("path traversal should be rejected")
        except self.urllib.HTTPError as error:
            self.assertEqual(error.code, 403)
            error.close()


class CliTests(unittest.TestCase):
    def test_new_and_verify(self):
        from watchcad import cli
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["new", "--preset", "diver",
                                           "--name", "CliTest", "-o", path]), 0)
            with open(path) as handle:
                spec = Spec.from_json(handle.read())
            self.assertEqual(spec.get("meta.name"), "CliTest")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["verify", path, "--json"]), 0)

    def test_export_svg(self):
        from watchcad import cli
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = os.path.join(tmp, "spec.json")
            svg_path = os.path.join(tmp, "front.svg")
            cli.main(["new", "--preset", "diver", "-o", spec_path])
            self.assertEqual(cli.main(["export", spec_path, "--format", "svg",
                                       "-o", svg_path]), 0)
            with open(svg_path) as handle:
                self.assertTrue(handle.read().startswith("<svg"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
