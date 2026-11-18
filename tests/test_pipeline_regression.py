"""Regression tests for the canonical spec-to-viewport pipeline.

These tests are deliberately dependency-free and deterministic.  They cover the
contracts that keep an edited component, material or finish from being lost
between validation, geometry generation, GLB export and the live viewer:

* one monotonic revision per successful commit;
* revision fences for deferred and stale work;
* last-good preservation when generation fails;
* quality presets that never change physical geometry;
* crease-aware normals, mesh validation and deterministic UV/material data;
* GLB headers, cache invalidation and stale preview rejection.

Run with the rest of the suite:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from watchcad import api, materials
from watchcad import mesh as M
from watchcad.agent.context import Session
from watchcad.nlp import parse
from watchcad.parts import build_scene
from watchcad.revision import RenderGenerationError, RevisionConflict
from watchcad.schema import Spec
from watchcad.solver import solve
from watchcad.store import ConversationStore
from watchcad.verify import summary, verify

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")


def starter_spec() -> Spec:
    return parse(DIVER, name="PipelineRegression").spec


def solved_spec() -> Spec:
    solved, _ = solve(starter_spec())
    return solved


def part_named(parts, name):
    return next(part for part in parts if part.name == name)


def glb_json(payload: bytes) -> dict:
    magic, version, length = struct.unpack("<III", payload[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(payload)
    json_length, json_type = struct.unpack("<II", payload[12:20])
    assert json_type == 0x4E4F534A
    return json.loads(payload[20:20 + json_length].decode("utf-8"))


def glb_material(document: dict, name: str) -> dict:
    return next(item for item in document["materials"] if item["name"] == name)


class RevisionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.studio = api.Studio(starter_spec())

    def test_commit_increments_revision_and_logs_every_stage(self):
        events = []
        first = self.studio.snapshot()
        candidate = self.studio.spec.copy()
        self.assertTrue(candidate.set("case.diameter_mm", 43.0))

        with mock.patch.object(api, "_render_log",
                               side_effect=lambda event, **fields: events.append(
                                   (event, fields))):
            result = self.studio.commit(candidate, expected_revision=first["revision"],
                                        label="Resize case", component="case")

        second = self.studio.snapshot()
        self.assertEqual(result["revision"], first["revision"] + 1)
        self.assertEqual(second["revision"], first["revision"] + 1)
        self.assertEqual(second["spec"]["data"]["case"]["diameter_mm"], 43.0)
        self.assertEqual(second["checksum"], second["spec"]["data"]["derived"]["checksum"])
        self.assertEqual(second["modification_id"], result["modification_id"])
        self.assertNotEqual(first["checksum"], second["checksum"])
        self.assertEqual(second["render"]["status"], "rendered")

        stages = {fields.get("stage") for event, fields in events
                  if event == "stage"}
        self.assertTrue({
            "modification_pending", "validating", "solving",
            "generating_geometry", "exporting_model", "updating_viewport",
            "rendered",
        }.issubset(stages))
        self.assertTrue(any(event == "rendered" for event, _ in events))

    def test_stale_commit_is_rejected_and_keeps_live_state(self):
        base = self.studio.snapshot()
        candidate = self.studio.spec.copy()
        candidate.set("case.diameter_mm", 44.0)
        self.studio.commit(candidate, expected_revision=base["revision"])

        after = self.studio.snapshot()
        stale = self.studio.spec.copy()
        stale.set("case.diameter_mm", 39.0)
        with self.assertRaises(RevisionConflict):
            self.studio.commit(stale, expected_revision=base["revision"])

        current = self.studio.snapshot()
        self.assertEqual(current["revision"], after["revision"])
        self.assertEqual(current["checksum"], after["checksum"])
        self.assertEqual(current["modification_id"], after["modification_id"])
        self.assertEqual(current["spec"]["data"]["case"]["diameter_mm"], 44.0)

    def test_invalid_generation_preserves_last_good_artifacts(self):
        before = self.studio.snapshot()
        scene_triangles = self.studio.scene["triangles"]
        invalid = self.studio.spec.copy()
        # Force past schema validation so the pipeline's validation stage fails
        # before any derived artifact is installed.
        self.assertTrue(invalid.set("case.diameter_mm", 400.0, force=True))

        with self.assertRaises(RenderGenerationError) as caught:
            self.studio.commit(invalid, expected_revision=before["revision"])
        self.assertIn("validation failed", str(caught.exception))

        after = self.studio.snapshot()
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["checksum"], before["checksum"])
        self.assertEqual(after["modification_id"], before["modification_id"])
        self.assertEqual(after["spec"], before["spec"])
        self.assertEqual(self.studio.scene["triangles"], scene_triangles)
        self.assertEqual(after["render"]["status"], "failed")
        self.assertIn("validation failed", after["render"]["error"])

    def test_undo_and_redo_rebuild_geometry_at_new_revisions(self):
        session = Session("pipeline-history", self.studio)
        baseline = self.studio.snapshot()

        self.studio.set("dial.color", "blue")
        session.checkpoint("Blue dial", "manual")
        blue = self.studio.snapshot()

        self.studio.set("dial.color", "green")
        session.checkpoint("Green dial", "manual")
        green = self.studio.snapshot()

        self.assertEqual(baseline["revision"] + 1, blue["revision"])
        self.assertEqual(blue["revision"] + 1, green["revision"])
        self.assertEqual(len({baseline["checksum"], blue["checksum"],
                              green["checksum"]}), 3)

        undone = session.undo()
        self.assertIsNotNone(undone)
        restored = self.studio.snapshot()
        self.assertEqual(restored["revision"], green["revision"] + 1)
        self.assertEqual(restored["spec"]["data"]["dial"]["color"], "blue")
        self.assertEqual(restored["scene"]["triangles"], blue["scene"]["triangles"])

        redone = session.redo()
        self.assertIsNotNone(redone)
        replayed = self.studio.snapshot()
        self.assertEqual(replayed["revision"], restored["revision"] + 1)
        self.assertEqual(replayed["spec"]["data"]["dial"]["color"], "green")
        self.assertEqual(replayed["scene"]["triangles"], green["scene"]["triangles"])
        self.assertEqual(replayed["render"]["status"], "rendered")

    def test_material_component_and_texture_changes_reach_geometry_and_glb(self):
        spec = solved_spec()
        self.assertTrue(spec.set("strap.material", "leather"))
        spec, _ = solve(spec)
        studio = api.Studio(spec)

        before_scene = studio.scene_for("high")
        before = M.to_glb(list(before_scene["parts"]), "before")
        before_document = glb_json(before)
        before_case = glb_material(before_document, "case_mid")
        before_dial = glb_material(before_document, "dial")
        self.assertEqual(
            before_case["extras"]["material_key"], materials.case_key("316L stainless steel"))
        self.assertIn("dial", [part.name for part in before_scene["parts"]])
        self.assertIn("strap", [part.name for part in before_scene["parts"]])

        candidate = studio.spec.copy()
        changes = {
            "case.material": "titanium",
            "case.finish": "bead-blasted",
            "dial.color": "blue",
            "dial.finish": "guilloche",
            "strap.material": "leather",
            "strap.color": "#6b3f2a",
        }
        for path, value in changes.items():
            self.assertTrue(candidate.set(path, value), path)
        result = studio.commit(candidate, label="Finish and material change",
                               component="material")

        after_scene = studio.scene_for("high")
        case_mid = part_named(after_scene["parts"], "case_mid")
        dial = part_named(after_scene["parts"], "dial")
        strap = part_named(after_scene["parts"], "strap")
        self.assertEqual(case_mid.material, "titanium")
        self.assertEqual(case_mid.meta["finish"], "bead-blasted")
        self.assertEqual(case_mid.meta["texture"], "grain")
        self.assertEqual(dial.meta["finish"], "guilloche")
        self.assertEqual(dial.meta["texture"], "guilloche")
        self.assertEqual(strap.material, "leather")
        self.assertEqual(strap.color.lower(), "#6b3f2a")

        after = M.to_glb(list(after_scene["parts"]), "after")
        document = glb_json(after)
        case_gl = glb_material(document, "case_mid")
        dial_gl = glb_material(document, "dial")
        self.assertEqual(case_gl["extras"]["material_key"], "titanium")
        self.assertEqual(case_gl["extras"]["finish"], "bead-blasted")
        self.assertEqual(case_gl["extras"]["texture"], "grain")
        self.assertEqual(dial_gl["extras"]["texture"], "guilloche")
        self.assertNotEqual(case_gl["extras"]["texture_seed"],
                            before_case["extras"]["texture_seed"])
        self.assertNotEqual(
            dial_gl["pbrMetallicRoughness"]["baseColorFactor"],
            before_dial["pbrMetallicRoughness"]["baseColorFactor"])
        self.assertNotEqual(after, before)
        self.assertEqual(after, M.to_glb(list(after_scene["parts"]), "after"))
        self.assertEqual(result["revision"], 2)


class MeshRegressionTests(unittest.TestCase):
    def test_quality_preserves_dimensions_and_mesh_validity(self):
        spec = solved_spec()
        self.assertTrue(spec.set("strap.material", "leather"))
        spec, _ = solve(spec)
        scenes = {quality: build_scene(spec, quality)
                  for quality in ("low", "medium", "high")}

        bounds = scenes["high"]["bounds"]
        for quality, scene in scenes.items():
            # The reported envelope is an exact spec contract.  Volume-derived
            # mass is a presentation estimate: polygon count may move it by a
            # few tenths of a gram, but never the solved dimensions.
            self.assertEqual(scene["bounds"], bounds, quality)
            self.assertLess(abs(scene["mass_g"] - scenes["high"]["mass_g"])
                            / scenes["high"]["mass_g"], 0.01, quality)
            self.assertEqual(M.validate_meshes(list(scene["parts"])), [], quality)
            self.assertEqual(summary(verify(spec, scene))["fail"], 0, quality)

        self.assertLess(scenes["low"]["triangles"], scenes["high"]["triangles"])

        parts = scenes["high"]["parts"]
        self.assertEqual(sum(part.name == "strap" for part in parts), 1,
                         "the wrist strap is one continuous mesh")
        self.assertEqual(sum(part.name == "strap_keeper" for part in parts), 2)
        self.assertEqual(sum(part.name == "strap_hole" for part in parts), 3)
        self.assertEqual(sum(part.name == "buckle" for part in parts), 1)
        self.assertGreaterEqual(sum(part.name.startswith("lug") for part in parts), 4)

        strap = part_named(parts, "strap")
        strap_z = [vertex[2] for vertex in strap.verts]
        self.assertLess(min(strap_z), -20.0, "strap follows the wrist below the case")
        self.assertGreater(max(strap_z), 0.0, "strap reaches the spring bars")

    def test_crease_splitting_produces_unit_normals_and_valid_uvs(self):
        cube = M.box(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        split = M.split_creases(cube)
        self.assertGreater(len(split.verts), len(cube.verts),
                           "90-degree corners need duplicated normal groups")
        self.assertEqual(len(split.normals), len(split.verts))
        self.assertEqual(len(split.faces), len(cube.faces))
        self.assertEqual(M.validate_mesh(split), [])

        uvs = M._uv_coords(split)
        self.assertEqual(len(uvs), len(split.verts))
        self.assertTrue(all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in uvs))

        document = glb_json(M.to_glb([split], "creased-cube"))
        primitive = document["meshes"][0]["primitives"][0]
        self.assertIn("NORMAL", primitive["attributes"])
        self.assertIn("TEXCOORD_0", primitive["attributes"])
        self.assertEqual(document["accessors"][primitive["attributes"]["NORMAL"]]["count"],
                         len(split.normals))

    def test_mesh_validation_rejects_defects_before_glb_export(self):
        degenerate = M.Mesh("degenerate",
                            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
                            [(0, 1, 2)])
        kinds = {row["kind"] for row in M.validate_mesh(degenerate)}
        self.assertIn("zero_area", kinds)
        with self.assertRaises(M.MeshValidationError):
            M.to_glb([degenerate])

        bad_normal = M.box(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
        bad_normal.normals = [(0.0, 0.0, 2.0)] * len(bad_normal.verts)
        kinds = {row["kind"] for row in M.validate_mesh(bad_normal)}
        self.assertIn("invalid_normal", kinds)
        with self.assertRaises(M.MeshValidationError):
            M.to_glb([bad_normal])


class ApiModelSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_store = api.STORE
        cls.old_build_root = api.BUILD_ROOT
        api.BUILD_ROOT = Path(cls.tmp.name) / "builds"
        api.BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        cls.store = ConversationStore(str(Path(cls.tmp.name) / "pipeline.sqlite3"))
        api.STORE = cls.store
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        api.STORE = cls.old_store
        api.BUILD_ROOT = cls.old_build_root
        cls.store.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.session = f"pipeline-{self._testMethodName}"

    def request(self, path, body=None, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        merged = {"Content-Type": "application/json",
                  "X-WatchCAD-Session": self.session}
        merged.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, headers=merged)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            payload = error.read()
            error.close()
            return error.code, dict(error.headers.items()), payload

    def get_json(self, path):
        status, _, raw = self.request(path)
        self.assertEqual(status, 200, raw.decode("utf-8", "replace"))
        return json.loads(raw)

    def scene_signature(self):
        status, _, raw = self.request("/api/scene?quality=low")
        self.assertEqual(status, 200, raw.decode("utf-8", "replace"))
        scene = json.loads(raw)
        return {
            "mass_g": scene["mass_g"],
            "triangles": scene["triangles"],
            "parts": [(part.get("name"), part.get("bounds"))
                      for part in scene["parts"]],
        }

    def post_json(self, path, body):
        status, headers, raw = self.request(path, body)
        self.assertEqual(status, 200, raw.decode("utf-8", "replace"))
        return json.loads(raw), headers

    @staticmethod
    def header(headers, name):
        wanted = name.lower()
        for key, value in headers.items():
            if key.lower() == wanted:
                return value
        return ""

    def test_model_headers_generations_and_cache_invalidation(self):
        state = self.get_json("/api/state")
        revision = int(state["revision"])
        modification_id = state["modification_id"]
        checksum = state["checksum"]

        status, headers, first_model = self.request("/api/model.glb?quality=low")
        self.assertEqual(status, 200)
        self.assertEqual(int(self.header(headers, "X-Design-Revision")), revision)
        self.assertEqual(self.header(headers, "X-Design-Checksum"), checksum)
        self.assertEqual(self.header(headers, "X-Modification-ID"), modification_id)
        self.assertEqual(self.header(headers, "X-Model-Preview"), "false")
        generation = int(self.header(headers, "X-Model-Generation"))
        self.assertGreater(generation, 0)

        status, headers, cached = self.request("/api/model.glb?quality=low")
        self.assertEqual(status, 200)
        self.assertEqual(cached, first_model, "the same revision reuses its GLB")
        self.assertGreater(int(self.header(headers, "X-Model-Generation")), generation)

        preview_payload, _ = self.post_json("/api/optimise/preview", {
            "changes": [{"path": "case.material", "value": "titanium"}],
            "baseline_checksum": checksum,
            "label": "material preview",
        })
        preview_token = preview_payload["preview"]
        status, headers, preview_model = self.request(
            f"/api/model.glb?preview={preview_token}&quality=low")
        self.assertEqual(status, 200)
        self.assertEqual(self.header(headers, "X-Model-Preview"), "true")
        self.assertNotEqual(preview_model, first_model)
        self.assertEqual(int(self.header(headers, "X-Design-Revision")), revision)

        changed, _ = self.post_json("/api/set", {
            "path": "case.material", "value": "titanium",
            "expected_revision": revision,
        })
        new_revision = int(changed["revision"])
        self.assertEqual(new_revision, revision + 1)
        self.assertNotEqual(changed["modification_id"], modification_id)

        status, _, _ = self.request("/api/model.glb?revision=%d" % revision)
        self.assertEqual(status, 409, "a stale GLB request must not be served")

        status, headers, invalid_preview = self.request(
            f"/api/model.glb?preview={preview_token}&quality=low")
        self.assertEqual(status, 409, "a preview based on an old revision must expire")

        status, headers, updated_model = self.request("/api/model.glb?quality=low")
        self.assertEqual(status, 200)
        self.assertEqual(int(self.header(headers, "X-Design-Revision")), new_revision)
        self.assertEqual(int(self.header(headers, "X-Model-Generation")), generation + 3)
        self.assertNotEqual(updated_model, first_model,
                            "committing a material change invalidates the GLB cache")

    def test_stale_post_and_scene_requests_are_fenced(self):
        state = self.get_json("/api/state")
        revision = int(state["revision"])
        _, _ = self.post_json("/api/set", {
            "path": "dial.color", "value": "blue",
            "expected_revision": revision,
        })
        current = self.get_json("/api/state")
        self.assertEqual(int(current["revision"]), revision + 1)

        status, _, _ = self.request("/api/set", {
            "path": "dial.color", "value": "green",
            "expected_revision": revision,
        })
        self.assertEqual(status, 409)
        status, _, _ = self.request(f"/api/scene?quality=low&revision={revision}")
        self.assertEqual(status, 409)

        status, headers, raw = self.request("/api/scene?quality=low")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(int(self.header(headers, "X-Design-Revision")), revision + 1)
        self.assertEqual(payload["revision"], revision + 1)
        self.assertGreater(payload["generation"], 0)
        self.assertEqual(payload["quality"], "low")

    def test_model_and_scene_carry_complete_session_identity(self):
        state = self.get_json("/api/state")
        status, headers, raw = self.request("/api/scene?quality=low")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        for key in ("session_id", "revision", "checksum", "modification_id",
                    "generation", "preview", "preview_id"):
            self.assertIn(key, payload)
        self.assertEqual(payload["session_id"], self.session)
        self.assertEqual(payload["revision"], int(state["revision"]))
        self.assertEqual(payload["checksum"], state["checksum"])
        self.assertEqual(payload["modification_id"], state["modification_id"])
        self.assertFalse(payload["preview"])
        self.assertEqual(payload["preview_id"], "")
        self.assertEqual(self.header(headers, "X-WatchCAD-Session"), self.session)
        self.assertEqual(self.header(headers, "X-Design-Checksum"), state["checksum"])
        self.assertEqual(self.header(headers, "X-Modification-ID"),
                         state["modification_id"])
        self.assertEqual(self.header(headers, "X-Model-Preview"), "false")
        self.assertEqual(self.header(headers, "X-Model-Preview-ID"), "")

        status, headers, raw = self.request("/api/model.glb?quality=low")
        self.assertEqual(status, 200)
        self.assertEqual(self.header(headers, "X-WatchCAD-Session"), self.session)
        document = glb_json(raw)
        embedded = document["asset"]["extras"]["watchcad"]
        self.assertEqual(embedded["session_id"], self.session)
        self.assertEqual(embedded["revision"], int(state["revision"]))
        self.assertEqual(embedded["checksum"], state["checksum"])
        self.assertEqual(embedded["modification_id"], state["modification_id"])
        self.assertFalse(embedded["preview"])
        self.assertEqual(embedded["preview_id"], "")

    def test_preview_identity_is_isolated_from_live_model(self):
        state = self.get_json("/api/state")
        payload, _ = self.post_json("/api/optimise/preview", {
            "changes": [{"path": "case.material", "value": "titanium"}],
            "baseline_checksum": state["checksum"],
            "label": "identity preview",
        })
        token = payload["preview"]
        status, headers, raw = self.request(
            f"/api/model.glb?preview={token}&quality=low")
        self.assertEqual(status, 200)
        self.assertEqual(self.header(headers, "X-Model-Preview"), "true")
        self.assertEqual(self.header(headers, "X-Model-Preview-ID"), token)
        self.assertEqual(self.header(headers, "X-Design-Checksum"),
                         payload["checksum"])
        self.assertNotEqual(payload["checksum"], state["checksum"])
        embedded = glb_json(raw)["asset"]["extras"]["watchcad"]
        self.assertTrue(embedded["preview"])
        self.assertEqual(embedded["preview_id"], token)
        self.assertEqual(embedded["checksum"], payload["checksum"])

        _, _, live = self.request("/api/model.glb?quality=low")
        live_embedded = glb_json(live)["asset"]["extras"]["watchcad"]
        self.assertFalse(live_embedded["preview"])
        self.assertEqual(live_embedded["preview_id"], "")
        self.assertEqual(live_embedded["checksum"], state["checksum"])
        self.assertNotEqual(live, raw)

        _, _, scene_raw = self.request(
            f"/api/scene?preview={token}&quality=low")
        scene_payload = json.loads(scene_raw)
        self.assertTrue(scene_payload["preview"])
        self.assertEqual(scene_payload["preview_id"], token)
        self.assertEqual(scene_payload["checksum"], payload["checksum"])

    def test_model_generations_are_session_local(self):
        other = f"{self.session}-other"
        generations = []
        for _ in range(3):
            status, headers, _ = self.request("/api/model.glb?quality=low")
            self.assertEqual(status, 200)
            generations.append(int(self.header(headers, "X-Model-Generation")))
            self.assertEqual(self.header(headers, "X-WatchCAD-Session"), self.session)
        self.assertEqual(generations, sorted(set(generations)))

        status, headers, _ = self.request(
            "/api/model.glb?quality=low",
            headers={"X-WatchCAD-Session": other})
        self.assertEqual(status, 200)
        self.assertEqual(self.header(headers, "X-WatchCAD-Session"), other)
        other_first = int(self.header(headers, "X-Model-Generation"))
        self.assertLess(other_first, generations[-1],
                        "a new session starts its own generation sequence")

        status, headers, _ = self.request(
            "/api/model.glb?quality=low",
            headers={"X-WatchCAD-Session": other})
        self.assertEqual(status, 200)
        other_second = int(self.header(headers, "X-Model-Generation"))
        self.assertGreater(other_second, other_first)
        self.assertLess(other_second, generations[-1],
                        "returning to a session may use a lower generation")

    def test_components_endpoint_exposes_the_authoritative_registry(self):
        before = self.get_json("/api/state")
        payload = self.get_json("/api/components")
        components = {item["id"]: item for item in payload["components"]}
        self.assertGreaterEqual(len(components), 10)
        for component_id in ("case", "dial", "hands", "movement", "complications"):
            self.assertIn(component_id, components)
            item = components[component_id]
            self.assertTrue(item["label"])
            self.assertTrue(item["group"])
            self.assertTrue(item["parts"])
            self.assertTrue(item["checks"])
            self.assertTrue(item["fields"])
        self.assertIn("dial.diameter_mm", components["dial"]["dimension_fields"])
        self.assertIn("hour_hand", components["hands"]["parts"])
        after = self.get_json("/api/state")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["checksum"], before["checksum"])

    def test_snapshot_does_not_mutate_and_restore_commits_a_new_revision(self):
        initial = self.get_json("/api/state")
        initial_revision = int(initial["revision"])
        initial_version = int(initial["spec"]["data"]["meta"]["version"])
        initial_checksum = initial["checksum"]
        initial_geometry = self.scene_signature()

        snapshot, _ = self.post_json("/api/agent/snapshot", {
            "label": "API snapshot",
            "expected_revision": initial_revision,
        })
        self.assertEqual(int(snapshot["revision"]), initial_revision)
        self.assertEqual(snapshot["checksum"], initial_checksum)
        self.assertEqual(snapshot["version"]["label"], "API snapshot")
        self.assertEqual(snapshot["version"]["kind"], "snapshot")

        unchanged = self.get_json("/api/state")
        self.assertEqual(int(unchanged["revision"]), initial_revision)
        self.assertEqual(unchanged["checksum"], initial_checksum)
        self.assertEqual(int(unchanged["spec"]["data"]["meta"]["version"]),
                         initial_version)

        history = self.get_json("/api/agent/history")
        self.assertTrue(any(item["id"] == snapshot["version"]["id"]
                            and item["kind"] == "snapshot"
                            for item in history["history"]))

        status, _, _ = self.request("/api/agent/snapshot", {
            "label": "stale snapshot",
            "expected_revision": initial_revision - 1,
        })
        self.assertEqual(status, 409, "a stale snapshot request must be fenced")

        changed, _ = self.post_json("/api/set", {
            "path": "dial.color", "value": "blue",
            "expected_revision": initial_revision,
        })
        changed_revision = int(changed["revision"])
        self.assertNotEqual(changed["checksum"], initial_checksum)
        self.assertEqual(changed["spec"]["data"]["dial"]["color"], "blue")

        restored, _ = self.post_json("/api/agent/restore", {
            "id": snapshot["version"]["id"],
            "expected_revision": changed_revision,
        })
        self.assertEqual(int(restored["revision"]), changed_revision + 1)
        # Restore increments meta.version, which is part of the spec checksum,
        # so the checksum must change.  The geometry it solves is invariant.
        self.assertNotEqual(restored["checksum"], initial_checksum)
        self.assertEqual(self.scene_signature(), initial_geometry,
                         "restoring a version re-solves the original geometry")
        self.assertEqual(restored["spec"]["data"]["dial"]["color"],
                         initial["spec"]["data"]["dial"]["color"])
        self.assertGreater(int(restored["spec"]["data"]["meta"]["version"]),
                           int(changed["spec"]["data"]["meta"]["version"]))

        status, _, _ = self.request("/api/agent/restore", {
            "id": snapshot["version"]["id"],
            "expected_revision": changed_revision,
        })
        self.assertEqual(status, 409, "a stale restore request must be fenced")

        status, _, raw = self.request("/api/agent/restore", {
            "id": "v9999", "expected_revision": int(restored["revision"]),
        })
        self.assertEqual(status, 404, raw.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
