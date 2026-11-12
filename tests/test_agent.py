"""Deterministic tests for the autonomous design agent.

No network is used: the model client is a scripted stub, and the default run
uses the local planner.  Every test checks the same contract the UI relies on:
changes are validated, verification runs, locks are inviolable, and nothing is
committed without a solved, verified working copy.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from watchcad.agent import (
    AgentOrchestrator, Intent, ToolCall, apply_variant, detect_intent,
    run_agent, validate_arguments,
)
from watchcad.agent.context import Session
from watchcad.agent.evaluator import count_failures, failures
from watchcad.agent.models import ToolResult
from watchcad.agent.orchestrator import normalize_mode
from watchcad.agent.tool_executor import ToolExecutor
from watchcad.api import Studio
from watchcad.llm import LLMConfig, LLMReply
from watchcad.nlp import parse
from watchcad.schema import new_spec

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")


def base_spec():
    return parse(DIVER, name="AgentTest").spec


def make_session(name="agent-test", spec=None):
    return Session(name, Studio(spec if spec is not None else base_spec()))


class ScriptedLLM:
    """A deterministic OpenAI-compatible stub with no HTTP."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, config=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.replies:
            return LLMReply(content="Nothing further to do.")
        return self.replies.pop(0)


def call(name, arguments, call_id):
    return {"id": call_id, "name": name, "arguments": arguments}


class LocalAgentTests(unittest.TestCase):
    def test_autonomous_change_commits_only_after_verification(self):
        session = make_session()
        self.assertEqual(session.studio.spec.get("dial.color"), "black")
        result = run_agent(session, "make the dial blue", mode="autonomous",
                           use_llm=False)
        self.assertEqual(result.state.status, "complete")
        self.assertEqual(session.studio.spec.get("dial.color"), "blue")
        self.assertGreaterEqual(result.applied, 1)
        paths = [change.path for change in result.state.changes]
        self.assertIn("dial.color", paths)
        self.assertNotIn("meta.version", paths)
        self.assertFalse(any(path.startswith("derived.") for path in paths))
        self.assertEqual(result.state.verification_after.get("fail"), 0)
        self.assertTrue(result.state.evaluation.objective_achieved)
        kinds = [event.kind for event in result.state.timeline]
        self.assertIn("commit", kinds)
        self.assertIn("tool", kinds)

    def test_suggest_mode_prepares_without_staging_or_writing(self):
        session = make_session()
        result = run_agent(session, "make the dial blue", mode="suggest",
                           use_llm=False)
        self.assertEqual(result.state.status, "suggested")
        self.assertEqual(session.studio.spec.get("dial.color"), "black")
        self.assertIsNone(session.pending)
        self.assertIn("dial.color", [change.path for change in result.state.changes])

    def test_approval_stages_then_approve_applies(self):
        session = make_session()
        result = run_agent(session, "make the dial blue", mode="approval",
                           use_llm=False)
        self.assertEqual(result.state.status, "awaiting_approval")
        self.assertEqual(session.studio.spec.get("dial.color"), "black")
        self.assertTrue(session.pending)
        token = session.pending["token"]
        self.assertEqual(result.state.pending_token, token)
        pending = session.approve(token)
        self.assertEqual(pending["token"], token)
        self.assertEqual(session.studio.spec.get("dial.color"), "blue")
        self.assertFalse(session.pending)

    def test_locked_field_is_never_changed(self):
        session = make_session()
        session.studio.spec.lock("case.diameter_mm", True)
        session.studio.rebuild()
        result = run_agent(session, "make the case 44 mm", mode="autonomous",
                           use_llm=False)
        self.assertEqual(session.studio.spec.get("case.diameter_mm"), 42.0)
        self.assertNotIn("case.diameter_mm",
                         [change.path for change in result.state.changes])
        self.assertTrue(result.state.evaluation.protected_intact)

    def test_bounded_repair_uses_the_verifier_hint(self):
        spec = new_spec("Thin")
        spec.set("case.diameter_mm", 40.0)
        spec.set("case.thickness_mm", 8.0)
        session = make_session("repair", spec)
        before = count_failures(session.studio.checks)
        self.assertGreater(before, 0)
        result = run_agent(session, "fix the failing checks", mode="autonomous",
                           use_llm=False, max_iterations=3)
        self.assertGreaterEqual(result.state.iterations, 1)
        self.assertEqual(count_failures(session.studio.checks), 0)
        self.assertGreater(session.studio.spec.get("case.thickness_mm"), 8.0)
        attempts = [attempt.to_dict() for attempt in result.state.repair_attempts]
        self.assertTrue(any(attempt["improved"] for attempt in attempts))
        self.assertTrue(any("fix hint" in attempt["note"] for attempt in attempts))

    def test_undo_and_redo_restore_versions(self):
        session = make_session()
        run_agent(session, "make the dial blue", mode="autonomous", use_llm=False)
        self.assertEqual(session.studio.spec.get("dial.color"), "blue")
        restored = session.undo()
        self.assertIsNotNone(restored)
        self.assertEqual(session.studio.spec.get("dial.color"), "black")
        forward = session.redo()
        self.assertIsNotNone(forward)
        self.assertEqual(session.studio.spec.get("dial.color"), "blue")

    def test_variations_are_proposals_only(self):
        session = make_session()
        before = session.studio.spec.get("derived.checksum")
        result = run_agent(session, "show me alternatives", mode="autonomous",
                           use_llm=False)
        self.assertEqual(result.state.intent, "variations")
        self.assertGreaterEqual(len(result.state.variants), 1)
        self.assertEqual(session.studio.spec.get("derived.checksum"), before)
        for variant in result.state.variants:
            self.assertTrue(variant.get("changes"))
            self.assertIn("verification", variant)

    def test_apply_variant_runs_the_validated_pipeline(self):
        session = make_session()
        result = run_agent(session, "show me alternatives", mode="suggest",
                           use_llm=False)
        variant = next(v for v in result.state.variants
                       if any(change["path"] == "dial.color"
                              for change in v.get("changes", [])))
        before = session.studio.spec.get("dial.color")
        applied = apply_variant(session, variant, mode="autonomous")
        self.assertEqual(applied.state.status, "complete")
        self.assertEqual(session.studio.spec.get("dial.color"),
                         variant["changes"][0]["to"])
        self.assertNotEqual(session.studio.spec.get("dial.color"), before)


class ToolContractTests(unittest.TestCase):
    def test_tool_schema_rejects_bad_arguments(self):
        from watchcad.agent.tools import TOOL_SPECS

        clean, _ = validate_arguments(TOOL_SPECS["update_spec"],
                                      {"path": "dial.color", "value": "blue"})
        self.assertEqual(clean["path"], "dial.color")
        with self.assertRaises(Exception):
            validate_arguments(TOOL_SPECS["update_spec"],
                               {"path": "dial.color", "value": "blue", "bad": 1})
        with self.assertRaises(Exception):
            validate_arguments(TOOL_SPECS["update_spec"], {"path": "dial.color"})

    def test_derived_and_locked_paths_are_denied(self):
        session = make_session()
        session.studio.spec.lock("case.diameter_mm", True)
        session.studio.rebuild()
        executor = ToolExecutor(studio=session.studio, session=session)
        derived = executor.execute(ToolCall(
            name="update_spec",
            arguments={"path": "derived.checksum", "value": "forged"}))
        self.assertFalse(derived.ok)
        locked = executor.execute(ToolCall(
            name="update_spec",
            arguments={"path": "case.diameter_mm", "value": 44.0}))
        self.assertFalse(locked.ok)
        self.assertEqual(session.studio.spec.get("case.diameter_mm"), 42.0)

    def test_apply_patch_reports_only_user_fields(self):
        session = make_session()
        executor = ToolExecutor(studio=session.studio, session=session)
        result = executor.execute(ToolCall(
            name="apply_patch",
            arguments={"ops": [
                {"op": "replace", "path": "dial.color", "value": "blue"},
                {"op": "replace", "path": "case.material", "value": "titanium"},
            ]}))
        self.assertTrue(result.ok, result.error)
        payload = result.data
        paths = [row["path"] for row in payload["changes"]]
        self.assertEqual(sorted(paths), ["case.material", "dial.color"])
        self.assertFalse(any(path.startswith("derived.") for path in paths))
        self.assertIn("2 change", payload["message"])

    def test_cancelled_run_commits_nothing(self):
        session = make_session()
        control = session.runs.setdefault("stop", __import__(
            "watchcad.agent.context", fromlist=["RunControl"]).RunControl())
        control.cancel()
        result = run_agent(session, "make the dial blue", mode="autonomous",
                           use_llm=False, control=control)
        self.assertEqual(session.studio.spec.get("dial.color"), "black")
        self.assertIn(result.state.status, {"cancelled", "complete"})
        if result.state.status == "cancelled":
            self.assertEqual(result.applied, 0)


class MockedLLMTests(unittest.TestCase):
    def test_model_tool_calls_flow_through_the_executor(self):
        session = make_session()
        client = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("get_current_spec", {"full": False}, "c1"),
                call("update_spec", {"path": "dial.color", "value": "blue"}, "c2"),
                call("run_solver", {}, "c3"),
                call("run_verification", {}, "c4"),
            ]),
            LLMReply(content="The dial is blue and the deterministic checks are clean."),
        ])
        result = run_agent(
            session, "make the dial blue", mode="autonomous", use_llm=True,
            llm_client=client,
            llm_config=LLMConfig(url="mock://local", model="scripted"))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(session.studio.spec.get("dial.color"), "blue")
        self.assertEqual(result.state.status, "complete")
        self.assertEqual(result.state.engine, "rules+llm")
        self.assertEqual(result.state.verification_after.get("fail"), 0)
        tools = [event.data.get("tool") for event in result.state.timeline
                 if event.data.get("tool")]
        self.assertIn("update_spec", tools)
        self.assertIn("run_verification", tools)

    def test_model_cannot_write_a_locked_field(self):
        session = make_session()
        session.studio.spec.lock("case.diameter_mm", True)
        session.studio.rebuild()
        client = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("update_spec", {"path": "case.diameter_mm", "value": 44.0}, "c1"),
                call("run_verification", {}, "c2"),
            ]),
            LLMReply(content="The field is locked, so I left it alone."),
        ])
        result = run_agent(
            session, "make the case 44 mm", mode="autonomous", use_llm=True,
            llm_client=client,
            llm_config=LLMConfig(url="mock://local", model="scripted"))
        self.assertEqual(session.studio.spec.get("case.diameter_mm"), 42.0)
        rejected = [event for event in result.state.timeline
                    if event.status in ("error", "denied")]
        self.assertTrue(rejected)

    def test_llm_failure_falls_back_to_the_local_planner(self):
        session = make_session()

        class BrokenLLM:
            def chat(self, *args, **kwargs):
                raise OSError("connection refused")

        result = run_agent(
            session, "make the dial blue", mode="autonomous", use_llm=True,
            llm_client=BrokenLLM(),
            llm_config=LLMConfig(url="mock://local", model="broken"))
        self.assertEqual(session.studio.spec.get("dial.color"), "blue")
        self.assertIn("fallback", result.state.engine)
        self.assertTrue(any(event.kind == "fallback"
                            for event in result.state.timeline))


class PrecedenceTests(unittest.TestCase):
    def test_explicit_prompt_beats_the_model_patch(self):
        patches = [
            {"op": "replace", "path": "case.diameter_mm", "value": 44.0},
            {"op": "replace", "path": "dial.color", "value": "red"},
            {"op": "replace", "path": "case.thickness_mm", "value": 11.0},
        ]
        with mock.patch.dict(os.environ, {"WATCHCAD_LLM_URL": "http://mock"}):
            with mock.patch("watchcad.nlp._llm_parse",
                            return_value=(patches, None)):
                result = parse("A 42 mm steel watch with a black dial.", use_llm=True)
        self.assertEqual(result.spec.get("case.diameter_mm"), 42.0)
        self.assertEqual(result.spec.get("dial.color"), "black")
        self.assertEqual(result.spec.get("case.thickness_mm"), 11.0)
        self.assertTrue(any("user's stated value" in note for note in result.notes))

    def test_intent_detection_is_deterministic(self):
        cases = {
            "make the dial blue": "modify",
            "fix the failing checks": "repair",
            "explain the case thickness": "explain",
            "critique this design": "critic",
            "show me alternatives": "variations",
            "undo the last change": "undo",
            "build the package": "build",
        }
        for request, expected in cases.items():
            self.assertEqual(detect_intent(request).name, expected, request)

    def test_mode_aliases_are_normalised(self):
        self.assertEqual(normalize_mode("suggest-only"), "suggest")
        self.assertEqual(normalize_mode("require_approval"), "approval")
        self.assertEqual(normalize_mode("auto"), "autonomous")
        self.assertEqual(normalize_mode("nonsense"), "autonomous")


class AgentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import urllib.request
        from http.server import ThreadingHTTPServer

        from watchcad import api

        cls.tmp = tempfile.TemporaryDirectory()
        api.BUILD_ROOT = __import__("pathlib").Path(cls.tmp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.urllib = urllib.request

    def setUp(self):
        # Each test gets its own isolated session so approval/run ordering
        # cannot leak a committed change into the next test.
        self.session = "agent-" + self._testMethodName

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        merged = {"Content-Type": "application/json",
                  "X-WatchCAD-Session": self.session}
        merged.update(headers or {})
        request = self.urllib.Request(self.base + path, data=data, headers=merged)
        with self.urllib.urlopen(request, timeout=120) as response:
            return response.status, response.read()

    def post(self, path, body):
        status, raw = self.request(path, body)
        return status, json.loads(raw)

    def test_catalogue_and_intent_endpoints(self):
        status, raw = self.request("/api/agent/tools")
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        names = {tool["name"] for tool in payload["tools"]}
        self.assertIn("update_spec", names)
        self.assertIn("run_verification", names)
        status, raw = self.request("/api/agent/nlp?q=fix%20the%20failing%20checks")
        self.assertEqual(json.loads(raw)["name"], "repair")
        status, raw = self.request("/api/agent/knowledge?q=sapphire")
        self.assertTrue(json.loads(raw)["matches"])

    def test_non_streaming_run_and_history(self):
        status, payload = self.post("/api/agent/run", {
            "prompt": "make the dial blue",
            "mode": "autonomous",
            "use_llm": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "complete")
        self.assertIn("dial.color", [change["path"] for change in payload["changes"]])
        self.assertEqual(payload["snapshot"]["spec"]["data"]["dial"]["color"], "blue")
        self.assertEqual(payload["snapshot"]["summary"]["fail"], 0)
        status, raw = self.request("/api/agent/history")
        history = json.loads(raw)
        self.assertTrue(history["versions"])
        self.assertIn("session_id", history)

        status, payload = self.post("/api/agent/undo", {})
        self.assertEqual(payload["spec"]["data"]["dial"]["color"], "black")
        status, payload = self.post("/api/agent/redo", {})
        self.assertEqual(payload["spec"]["data"]["dial"]["color"], "blue")

    def test_streaming_run_emits_timeline_and_end(self):
        status, raw = self.request("/api/agent/run", {
            "prompt": "make the dial blue",
            "mode": "suggest",
            "use_llm": False,
            "stream": True,
        })
        text = raw.decode()
        self.assertEqual(status, 200)
        self.assertIn("data:", text)
        self.assertIn('"type": "start"', text)
        self.assertIn('"type": "timeline"', text)
        self.assertIn('"type": "result"', text)
        self.assertTrue(text.rstrip().endswith('event: end\ndata: {"type":"end"}'))

    def test_approval_endpoint_round_trip(self):
        status, payload = self.post("/api/agent/run", {
            "prompt": "make the dial blue",
            "mode": "approval",
            "use_llm": False,
        })
        self.assertEqual(payload["status"], "awaiting_approval")
        token = payload["pending"]["token"]
        self.assertTrue(token)
        status, approved = self.post("/api/agent/approve", {"token": token})
        self.assertEqual(approved["spec"]["data"]["dial"]["color"], "blue")
        self.assertFalse(approved["session"]["pending"])


if __name__ == "__main__":
    unittest.main()
