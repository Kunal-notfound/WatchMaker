"""Increment A tests: component focus and the deterministic design critic.

No network and no real model are used.  These tests pin down three contracts:

* a viewer selection resolves to a stable component scope;
* a focused edit can only write fields owned by that component, and the
  refusal names the owning component;
* every critic number is computed deterministically from the solved design and
  no critic call mutates the specification.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from watchcad import api
from watchcad.agent.chat import ConversationRuntime
from watchcad.agent.context import Session
from watchcad.agent.models import ToolCall
from watchcad.agent.orchestrator import run_agent
from watchcad.agent.tool_executor import ToolExecutor
from watchcad.api import Studio, _starter_spec
from watchcad.components import (
    COMPONENTS, UnknownComponent, allows_path, component_context,
    component_for_path, resolve_component,
)
from watchcad.critic import AXIS_ORDER, critique
from watchcad.schema import FIELDS
from watchcad.store import ConversationStore


def bad_studio() -> Studio:
    """A design with several independent, deterministic inconsistencies."""
    spec = _starter_spec()
    spec.set("case.water_resistance_m", 50)
    spec.set("case.bezel", "dive")
    spec.set("case.lug_width_mm", 17.5)
    spec.set("case.crown_diameter_mm", 4.0)
    spec.set("case.lug_to_lug_mm", 56.0)
    spec.set("dial.index_style", "no indices")
    spec.set("dial.lume", False)
    spec.set("dial.date_window", True)
    spec.set("movement.complications", {})
    return Studio(spec)


class ComponentResolutionTests(unittest.TestCase):
    def test_ids_aliases_parts_roles_and_groups_resolve(self):
        self.assertEqual(resolve_component("crown").id, "crown")
        self.assertEqual(resolve_component("Crown").id, "crown")
        self.assertEqual(resolve_component("case_mid").id, "case")
        self.assertEqual(resolve_component("crystal_gasket").id, "crystal")
        self.assertEqual(resolve_component("hour_hand").id, "hands")
        self.assertEqual(resolve_component("mainplate").id, "movement")
        self.assertEqual(resolve_component("date_ring").id, "complications")
        self.assertEqual(resolve_component("dial.color").id, "dial")
        self.assertEqual(resolve_component("movement.complications.date").id,
                         "complications")
        self.assertEqual(
            resolve_component({"name": "hour_hand", "role": "hand",
                               "group": "Hands"}).id, "hands")

    def test_empty_selection_is_no_focus_and_unknown_is_loud(self):
        self.assertIsNone(resolve_component(None))
        self.assertIsNone(resolve_component(""))
        with self.assertRaises(UnknownComponent):
            resolve_component("flux capacitor")

    def test_path_ownership_prefers_the_specific_component(self):
        self.assertEqual(component_for_path("case.crystal_thickness_mm").id,
                         "crystal")
        self.assertEqual(component_for_path("movement.complications.date").id,
                         "complications")
        self.assertEqual(component_for_path("case.lug_width_mm").id, "case")

    def test_scope_allows_only_owned_paths(self):
        dial = resolve_component("dial")
        self.assertTrue(allows_path(dial, "dial.color"))
        self.assertFalse(allows_path(dial, "case.diameter_mm"))
        self.assertTrue(allows_path(None, "case.diameter_mm"))

    def test_component_context_is_read_only_and_names_its_parts(self):
        studio = Studio(_starter_spec())
        before = studio.spec.get("derived.checksum")
        context = component_context(studio, "crown")
        self.assertEqual(context["component"]["id"], "crown")
        paths = {row["path"] for row in context["fields"]}
        self.assertIn("case.crown", paths)
        self.assertIn("case.crown_diameter_mm", paths)
        names = {row["name"] for row in context["parts"]}
        self.assertIn("crown", names)
        self.assertIn("crown_tube", names)
        self.assertIn("dfm.water_resistance",
                      {row["id"] for row in context["checks"]})
        self.assertEqual(studio.spec.get("derived.checksum"), before)


class ScopedEditTests(unittest.TestCase):
    def test_focused_tool_refuses_an_out_of_scope_write(self):
        studio = Studio(_starter_spec())
        executor = ToolExecutor(studio=studio, focus="dial")
        result = executor.execute(ToolCall(
            name="update_spec",
            arguments={"path": "case.diameter_mm", "value": 44}))
        self.assertFalse(result.ok)
        self.assertIn("Dial", result.error)
        self.assertIn("Case", result.error)
        self.assertEqual(studio.spec.get("case.diameter_mm"), 42.0)

    def test_focused_tool_accepts_and_commits_an_owned_write(self):
        studio = Studio(_starter_spec())
        executor = ToolExecutor(studio=studio, focus="dial")
        result = executor.execute(ToolCall(
            name="update_spec", arguments={"path": "dial.color", "value": "blue"}))
        self.assertTrue(result.ok, result.error)
        self.assertNotEqual(studio.spec.get("dial.color"), "blue")
        executor.commit("scoped edit")
        self.assertEqual(studio.spec.get("dial.color"), "blue")

    def test_mixed_patch_is_rejected_as_a_whole(self):
        studio = Studio(_starter_spec())
        executor = ToolExecutor(studio=studio, focus="dial")
        result = executor.execute(ToolCall(
            name="apply_patch",
            arguments={"ops": [
                {"op": "replace", "path": "dial.lume", "value": False},
                {"op": "replace", "path": "case.diameter_mm", "value": 44},
            ]}))
        self.assertFalse(result.ok)
        self.assertFalse(executor.has_changes())
        self.assertEqual(studio.spec.get("dial.lume"), True)

    def test_cleared_focus_allows_the_whole_design(self):
        studio = Studio(_starter_spec())
        executor = ToolExecutor(studio=studio, focus=None)
        result = executor.execute(ToolCall(
            name="update_spec",
            arguments={"path": "case.diameter_mm", "value": 44}))
        self.assertTrue(result.ok, result.error)

    def test_planner_filters_out_of_scope_patches_and_explains(self):
        studio = Studio(_starter_spec())
        session = Session("focus-planner", studio)
        result = run_agent(session, "make the case 44 mm", use_llm=False,
                           focus="dial")
        self.assertEqual(result.applied, 0)
        self.assertEqual(studio.spec.get("case.diameter_mm"), 42.0)
        joined = " ".join(result.state.notes + result.state.questions)
        self.assertIn("focus is Dial", joined)
        self.assertIn("case.diameter_mm", joined)

    def test_conversation_focus_blocks_the_local_change(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ConversationStore(str(Path(tmp.name) / "focus.sqlite3"))
        self.addCleanup(store.close)
        studio = Studio(_starter_spec())
        session = Session("focus-chat", studio)
        conversation = store.create_conversation(
            title="Focus", design_session=session.session_id)
        runtime = ConversationRuntime(session, store, conversation["id"])
        result = runtime.run("make the case 44 mm", use_llm=False,
                             focus={"name": "dial", "role": "dial",
                                    "group": "Dial"})
        self.assertEqual(result.changes, [])
        self.assertEqual(studio.spec.get("case.diameter_mm"), 42.0)
        names = [call.get("name") for call in result.tool_calls]
        self.assertNotIn("apply_patch", names)


class CriticTests(unittest.TestCase):
    def test_five_axes_are_ordered_and_labelled(self):
        report = critique(Studio(_starter_spec()))
        self.assertEqual(tuple(axis["id"] for axis in report["axes"]), AXIS_ORDER)
        self.assertEqual(report["kind"], "watchcad.deterministic-critic")
        for axis in report["axes"]:
            self.assertIn(axis["confidence"], ("calculated", "estimated"))
            self.assertGreaterEqual(axis["score"], 0)
            self.assertLessEqual(axis["score"], 100)

    def test_report_is_deterministic_and_never_mutates_the_spec(self):
        studio = bad_studio()
        before = json.dumps(studio.spec.to_dict(), sort_keys=True, default=str)
        first = critique(studio)
        second = critique(studio)
        after = json.dumps(studio.spec.to_dict(), sort_keys=True, default=str)
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(first["checksum"],
                         studio.solved.get("derived.checksum"))

    def test_inconsistencies_produce_findings_and_schema_valid_fixes(self):
        report = critique(bad_studio())
        kinds = [finding["kind"] for axis in report["axes"]
                 for finding in axis["findings"]]
        self.assertIn("risk", kinds)
        fixes = [fix for axis in report["axes"] for fix in axis["fixes"]]
        paths = {fix["path"] for fix in fixes}
        for expected in ("dial.index_style", "dial.lume", "case.bezel",
                         "case.lug_to_lug_mm", "case.crown_diameter_mm",
                         "case.lug_width_mm", "dial.date_window"):
            self.assertIn(expected, paths)
        for fix in fixes:
            meta = FIELDS[fix["path"]]
            self.assertEqual(fix["path"], fix["path"].strip())
            if meta["kind"] == "enum":
                self.assertIn(fix["value"], meta["choices"])
            elif meta["kind"] in ("num", "int"):
                if meta.get("min") is not None:
                    self.assertGreaterEqual(fix["value"], meta["min"])
                if meta.get("max") is not None:
                    self.assertLessEqual(fix["value"], meta["max"])
        # Deduplication: one fix per path.
        self.assertEqual(len(paths), len(fixes))

    def test_locked_fields_are_reported_not_offered(self):
        studio = bad_studio()
        studio.spec.lock("case.lug_to_lug_mm", True)
        studio.rebuild()
        report = critique(studio)
        paths = {fix["path"] for axis in report["axes"] for fix in axis["fixes"]}
        self.assertNotIn("case.lug_to_lug_mm", paths)
        self.assertTrue(any("case.lug_to_lug_mm" in note
                            for note in report["notes"]))

    def test_focus_annotates_findings_and_scope(self):
        report = critique(bad_studio(), "dial")
        self.assertEqual(report["focus"]["id"], "dial")
        self.assertEqual(report["focus"]["fields"],
                         list(COMPONENTS["dial"].fields))
        dial_findings = [finding for axis in report["axes"]
                         for finding in axis["findings"]
                         if finding.get("in_focus")]
        self.assertTrue(dial_findings)
        for finding in dial_findings:
            self.assertEqual(finding["component"], "dial")

    def test_honesty_notes_keep_estimates_separate(self):
        notes = " ".join(critique(Studio(_starter_spec()))["notes"])
        self.assertIn("estimates", notes)
        self.assertIn("not a measurement", notes)


class CriticApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        api.BUILD_ROOT = Path(cls.tmp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.session = "increment-a-" + self._testMethodName

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json",
                     "X-WatchCAD-Session": self.session},
            method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_component_context_endpoint(self):
        status, payload = self.request(
            "/api/component/context?component=crown")
        self.assertEqual(status, 200)
        self.assertEqual(payload["component"]["id"], "crown")
        self.assertIn("case.crown_diameter_mm",
                      {row["path"] for row in payload["fields"]})
        self.assertIn("crown", {row["name"] for row in payload["parts"]})

    def test_critic_endpoint_with_focus(self):
        status, payload = self.request("/api/critic?component=dial")
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], "watchcad.deterministic-critic")
        self.assertEqual(payload["focus"]["id"], "dial")
        self.assertEqual(len(payload["axes"]), 5)

    def test_unknown_component_is_a_400(self):
        status, payload = self.request("/api/critic?component=flux")
        self.assertEqual(status, 400)
        self.assertIn("unknown component", payload.get("error", ""))

    def test_agent_route_passes_the_component_focus(self):
        status, payload = self.request("/api/agent/run", {
            "prompt": "make the case 44 mm",
            "mode": "autonomous",
            "use_llm": False,
            "component": {"name": "dial", "role": "dial", "group": "Dial"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("applied"), 0)
        case_diameter = payload["snapshot"]["spec"]["data"]["case"]["diameter_mm"]
        self.assertEqual(case_diameter, 42.0)
        joined = " ".join(payload.get("notes", []) + payload.get("questions", []))
        self.assertIn("focus is Dial", joined)
        self.assertIn("case.diameter_mm", joined)


if __name__ == "__main__":
    unittest.main()
