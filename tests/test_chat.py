"""Deterministic tests for the conversational design assistant.

No network and no real model are used.  A scripted model client returns exactly
the replies each scenario needs, so the tests pin down the contract the UI
relies on: conversation stays separate from CAD mutation, every mutation goes
through validated tools, verification runs before commit, and failures or
missing model configuration are reported honestly.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from watchcad import api
from watchcad.agent.chat import ConversationRuntime
from watchcad.agent.context import Session
from watchcad.api import Studio
from watchcad.llm import LLMConfig, LLMError, LLMReply
from watchcad.nlp import parse
from watchcad.store import ConversationStore


DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lK3Q6wAAAABJRU5ErkJggg=="


def call(name: str, arguments: dict, call_id: str) -> dict:
    return {"id": call_id, "name": name, "arguments": arguments}


class ScriptedLLM:
    """A deterministic stand-in for the provider client."""

    def __init__(self, replies: list[LLMReply], inspector=None):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.inspector = inspector

    def chat(self, messages, tools=None, config=None):
        self.calls.append({"messages": messages, "tools": tools})
        if self.inspector is not None:
            reply = self.inspector(messages, tools, len(self.calls))
            if reply is not None:
                return reply
        if not self.replies:
            raise AssertionError("scripted model ran out of replies")
        return self.replies.pop(0)


class ChatTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConversationStore(os.path.join(self.tmp.name, "chat.sqlite3"))
        self.addCleanup(self.store.close)
        self.studio = Studio(parse(DIVER, name="ChatTest").spec)
        self.session = Session("chat-test", self.studio)
        self.conversation = self.store.create_conversation(
            title="Chat test", design_session=self.session.session_id)

    def runtime(self, client=None, config=None):
        return ConversationRuntime(
            self.session, self.store, self.conversation["id"],
            llm_client=client,
            llm_config=config or LLMConfig(url="mock://local", model="scripted"))

    def turn(self, client, message, **kwargs):
        events = []
        result = self.runtime(client).run(message, emit=events.append, **kwargs)
        return result, events

    # -- conversation only -------------------------------------------------
    def test_general_question_is_answered_without_cad_mutation(self):
        client = ScriptedLLM([
            LLMReply(content="Sapphire is synthetic corundum (Mohs 9). It resists "
                             "scratches, which is why it is common on dive watches."),
        ])
        before = self.studio.spec.get("dial.color")
        result, _ = self.turn(client, "Why sapphire instead of mineral glass?",
                              mode="safe")
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.engine, "llm (scripted)")
        self.assertEqual(result.changes, [])
        self.assertIn("Mohs 9", result.text)
        self.assertEqual(self.studio.spec.get("dial.color"), before)
        self.assertEqual(self.store.tool_events(self.conversation["id"]), [])
        rows = self.store.messages(self.conversation["id"])
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])

    def test_follow_up_sees_the_previous_transcript(self):
        seen = {}

        def inspector(messages, tools, count):
            if count == 2:
                joined = json.dumps(messages, default=str)
                seen["has_previous"] = "scratch-resistant" in joined
                seen["has_user_turn"] = "Why sapphire" in joined
            return None

        client = ScriptedLLM([
            LLMReply(content="Sapphire is scratch-resistant because it is corundum."),
            LLMReply(content="Yes: that is the same crystal already in this design."),
        ], inspector=inspector)
        self.turn(client, "Why sapphire?", mode="safe")
        result, _ = self.turn(client, "Is that what this watch uses?", mode="safe")
        self.assertTrue(seen.get("has_previous"))
        self.assertTrue(seen.get("has_user_turn"))
        self.assertIn("same crystal", result.text)

    def test_opinion_and_discussion_do_not_change_the_design(self):
        client = ScriptedLLM([
            LLMReply(content="Titanium would cut roughly half the case mass, but "
                             "it scratches and finishes differently."),
            LLMReply(content="Fair. I would keep steel for the tool-watch look, or "
                             "offer a titanium caseback instead. Nothing changed."),
        ])
        color = self.studio.spec.get("dial.color")
        diameter = self.studio.spec.get("case.diameter_mm")
        self.turn(client, "Would titanium be better?", mode="safe")
        result, _ = self.turn(client, "Actually, I don't like that.", mode="safe")
        self.assertEqual(result.changes, [])
        self.assertEqual(self.studio.spec.get("dial.color"), color)
        self.assertEqual(self.studio.spec.get("case.diameter_mm"), diameter)
        self.assertIn("Nothing changed", result.text)

    # -- validated CAD mutation -------------------------------------------
    def test_model_change_runs_tools_verifies_and_commits(self):
        client = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("get_current_spec", {"full": False}, "c1"),
                call("update_spec", {"path": "dial.color", "value": "blue"}, "c2"),
                call("run_solver", {}, "c3"),
                call("run_verification", {}, "c4"),
            ]),
            LLMReply(content="The dial is now blue and the deterministic checks pass."),
        ])
        result, events = self.turn(client, "Make the dial blue", mode="safe")
        self.assertEqual(result.status, "complete")
        self.assertEqual(self.studio.spec.get("dial.color"), "blue")
        self.assertEqual([change["path"] for change in result.changes], ["dial.color"])
        self.assertEqual(result.verification.get("fail"), 0)
        self.assertIn("Applied 1 change", result.text)
        self.assertIn("Verification:", result.text)
        names = [event["name"] for event in self.store.tool_events(self.conversation["id"])]
        self.assertIn("update_spec", names)
        self.assertIn("run_verification", names)
        self.assertTrue(any(event["type"] == "timeline" for event in events))

    def test_approval_mode_stages_the_change_until_approved(self):
        client = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("update_spec", {"path": "dial.color", "value": "blue"}, "c1"),
                call("run_solver", {}, "c2"),
                call("run_verification", {}, "c3"),
            ]),
            LLMReply(content="I have prepared the blue dial for approval."),
        ])
        result, _ = self.turn(client, "Make the dial blue", mode="approval")
        self.assertEqual(result.status, "awaiting_approval")
        self.assertTrue(result.pending and result.pending.get("token"))
        self.assertEqual(self.studio.spec.get("dial.color"), "black")
        token = self.session.approve(result.pending["token"])
        self.assertEqual(token["ops"][0]["path"], "dial.color")
        self.assertEqual(self.studio.spec.get("dial.color"), "blue")

    def test_locked_or_derived_field_is_rejected_and_reported(self):
        self.studio.spec.locks.add("dial.color")
        self.studio.rebuild()
        client = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("update_spec", {"path": "dial.color", "value": "blue"}, "c1"),
                call("update_spec", {"path": "derived.checksum", "value": "x"}, "c2"),
            ]),
            LLMReply(content="I could not change those fields: they are locked or "
                             "owned by the solver. The design is unchanged."),
        ])
        result, _ = self.turn(client, "Force the dial blue", mode="safe")
        self.assertEqual(result.changes, [])
        self.assertEqual(self.studio.spec.get("dial.color"), "black")
        self.assertTrue(all(not item["ok"] for item in result.tool_calls))
        self.assertIn("unchanged", result.text)

    # -- research and history ---------------------------------------------
    def test_research_returns_provenance_and_sources(self):
        client = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("research_topic", {"query": "sapphire crystal",
                                        "include_web": False}, "c1"),
            ]),
            LLMReply(content="Sapphire is reference data: synthetic corundum with "
                             "Mohs 9. I have no live web source configured, so this "
                             "comes from the curated table."),
        ])
        result, _ = self.turn(client, "Research sapphire crystal durability",
                              mode="safe")
        events = self.store.tool_events(self.conversation["id"])
        self.assertTrue(events)
        research = events[-1]["result"]["data"]
        self.assertTrue(research.get("local"))
        self.assertFalse(research.get("web", {}).get("configured"))
        labels = {item.get("status_label") for item in research["local"]}
        self.assertTrue(any(label for label in labels))
        self.assertIn("curated", result.text.lower())

    def test_undo_tool_restores_the_previous_verified_design(self):
        first = ScriptedLLM([
            LLMReply(content="", tool_calls=[
                call("update_spec", {"path": "dial.color", "value": "blue"}, "c1"),
                call("run_solver", {}, "c2"),
                call("run_verification", {}, "c3"),
            ]),
            LLMReply(content="Blue dial applied."),
        ])
        self.turn(first, "Make the dial blue", mode="safe")
        self.assertEqual(self.studio.spec.get("dial.color"), "blue")

        second = ScriptedLLM([
            LLMReply(content="", tool_calls=[call("undo_design", {}, "u1")]),
            LLMReply(content="I restored the previous version. The dial is black again."),
        ])
        result, _ = self.turn(second, "Actually, undo that", mode="safe")
        self.assertEqual(self.studio.spec.get("dial.color"), "black")
        self.assertTrue(any(item["name"] == "undo_design" and item["ok"]
                            for item in result.tool_calls))
        self.assertEqual(result.changes, [])

    def test_edit_message_replaces_that_turn_and_later_answers(self):
        first = ScriptedLLM([LLMReply(content="First answer.")])
        self.turn(first, "First question", mode="safe")
        rows = self.store.messages(self.conversation["id"])
        user_id = rows[0]["id"]

        second = ScriptedLLM([LLMReply(content="Second answer.")])
        events = []
        self.runtime(second).run(
            "Second question", mode="safe", emit=events.append,
            edit_message_id=user_id)
        rows = self.store.messages(self.conversation["id"])
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["content"], "Second question")
        self.assertEqual(rows[1]["content"], "Second answer.")

    def test_regenerate_replaces_only_the_last_answer(self):
        first = ScriptedLLM([LLMReply(content="Original answer.")])
        self.turn(first, "A question", mode="safe")
        second = ScriptedLLM([LLMReply(content="Regenerated answer.")])
        self.runtime(second).run("", mode="safe", regenerate=True)
        rows = self.store.messages(self.conversation["id"])
        self.assertEqual([row["content"] for row in rows],
                         ["A question", "Regenerated answer."])

    # -- long conversations, reload and images ----------------------------
    def test_long_conversation_is_summarised_without_losing_the_transcript(self):
        for index in range(20):
            self.store.add_message(self.conversation["id"], "user",
                                   f"Question {index}: what about material {index}?")
            self.store.add_message(self.conversation["id"], "assistant",
                                   f"Answer {index}: here is a short trade-off.")
        client = ScriptedLLM([LLMReply(content="The current design is a 42 mm steel diver.")])
        result, _ = self.turn(client, "Remind me what we are building", mode="safe")
        conversation = self.store.get_conversation(self.conversation["id"])
        self.assertTrue(conversation.get("summary"))
        self.assertGreater(int(conversation.get("summary_through") or 0), 0)
        self.assertIn("42 mm", result.text)
        self.assertGreater(len(self.store.messages(self.conversation["id"])), 40)

    def test_reload_restores_the_persisted_transcript_and_design(self):
        self.studio.set("dial.color", "blue")
        self.session.checkpoint("blue dial", "agent", force=True)
        first = self.store.add_message(self.conversation["id"], "user",
                                       "We chose a blue dial.")
        answer = self.store.add_message(self.conversation["id"], "assistant",
                                       "Yes, the blue dial is in the design.",
                                       data={"engine": "llm (scripted)"})
        self.store.save_design(self.session.session_id,
                               self.studio.spec.to_dict(),
                               conversation_id=self.conversation["id"])

        rows = self.store.messages(self.conversation["id"])
        self.assertEqual([row["content"] for row in rows],
                         [first["content"], answer["content"]])
        reloaded = self.store.get_design(self.session.session_id)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["spec"]["data"]["dial"]["color"], "blue")

    def test_vision_message_is_forwarded_and_invalid_images_are_dropped(self):
        seen = {}

        def inspector(messages, tools, count):
            seen["last"] = messages[-1]
            return None

        client = ScriptedLLM([
            LLMReply(content="It looks like a steel dive watch. I can only discuss "
                             "style and proportions from a photo, not exact sizes."),
        ], inspector=inspector)
        result, _ = self.turn(client, "What do you make of this?",
                              images=[PNG, "data:text/plain;base64,AAAA"],
                              mode="safe")
        content = seen["last"]["content"]
        self.assertIsInstance(content, list)
        self.assertTrue(any(part.get("type") == "image_url" for part in content))
        self.assertIn("not exact sizes", result.text)
        user = self.store.messages(self.conversation["id"])[0]
        self.assertEqual(user["data"]["images"], [PNG])

    # -- honest fallbacks --------------------------------------------------
    def test_missing_model_is_labelled_as_the_local_fallback(self):
        events = []
        runtime = ConversationRuntime(
            self.session, self.store, self.conversation["id"],
            llm_client=None, llm_config=LLMConfig(url=""))
        result = runtime.run("Make the dial blue", mode="safe",
                             use_llm=True, emit=events.append)
        self.assertIn("local rules", result.engine)
        self.assertEqual(self.studio.spec.get("dial.color"), "blue")
        self.assertTrue(any(event.get("type") == "notice" for event in events))
        self.assertNotIn("llm (", result.engine)

    def test_unconfigured_conversation_says_it_cannot_chat_yet(self):
        runtime = ConversationRuntime(
            self.session, self.store, self.conversation["id"],
            llm_client=None, llm_config=LLMConfig(url=""))
        result = runtime.run("Why is a dive bezel useful?", mode="safe",
                             use_llm=True)
        self.assertIn("No language model is configured", result.text)
        self.assertIn("local", result.engine)

    def test_model_failure_is_an_error_not_a_fake_answer(self):
        class Broken:
            def chat(self, messages, tools=None, config=None):
                raise LLMError("upstream returned HTTP 500")

        client = Broken()
        result, events = self.turn(client, "Make the dial blue", mode="safe")
        self.assertEqual(result.status, "error")
        self.assertEqual(self.studio.spec.get("dial.color"), "black")
        self.assertIn("couldn't get a usable response", result.text)
        self.assertIn("No design change was committed", result.text)
        self.assertTrue(any(event.get("type") == "error" for event in events))


class ChatApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        api.BUILD_ROOT = Path(cls.tmp.name)
        api.STUDIO = api.Studio(parse(DIVER, name="ApiChat").spec)
        api.SESSIONS = api.SessionManager(lambda: api.STUDIO, lambda: api.Studio())
        api.STORE = ConversationStore(os.path.join(cls.tmp.name, "api.sqlite3"))
        api.LLM_CLIENT = ScriptedLLM([
            LLMReply(content="Sapphire resists scratches."),
            LLMReply(content="", tool_calls=[
                call("update_spec", {"path": "dial.color", "value": "blue"}, "c1"),
                call("run_solver", {}, "c2"),
                call("run_verification", {}, "c3"),
            ]),
            LLMReply(content="The dial is now blue."),
            LLMReply(content="Streaming works."),
        ])
        api.LLM_CONFIG = LLMConfig(url="mock://local", model="scripted")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        api.STORE.close()
        api.STORE = None
        api.LLM_CLIENT = None
        api.LLM_CONFIG = None
        cls.tmp.cleanup()

    def request(self, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
        return text if raw else json.loads(text)

    def test_conversation_crud_search_chat_and_reload(self):
        created = self.request("/api/conversations", {"title": "API chat"})
        conversation_id = created["conversation"]["id"]
        self.assertTrue(conversation_id)

        answer = self.request("/api/chat", {
            "conversation_id": conversation_id,
            "message": "Why sapphire?",
            "mode": "safe",
        })
        self.assertEqual(answer["engine"], "llm (scripted)")
        self.assertIn("Sapphire", answer["text"])
        self.assertEqual(len(answer["messages"]), 2)

        changed = self.request("/api/chat", {
            "conversation_id": conversation_id,
            "message": "Make the dial blue",
            "mode": "safe",
        })
        self.assertEqual(changed["status"], "complete")
        self.assertEqual(changed["snapshot"]["spec"]["data"]["dial"]["color"], "blue")
        self.assertTrue(changed["changes"])

        reloaded = self.request(f"/api/conversations/{conversation_id}")
        self.assertEqual(len(reloaded["messages"]), 4)
        self.assertEqual(reloaded["snapshot"]["spec"]["data"]["dial"]["color"], "blue")
        self.assertEqual(reloaded["conversation"]["title"], "API chat")

        first_message_id = reloaded["messages"][0]["id"]
        trimmed = self.request(
            f"/api/conversations/{conversation_id}/messages/{first_message_id}/delete",
            {})
        self.assertTrue(trimmed["deleted"])
        self.assertEqual(len(trimmed["messages"]), 3)

        renamed = self.request(f"/api/conversations/{conversation_id}/rename",
                               {"title": "Renamed chat"})
        self.assertEqual(renamed["conversation"]["title"], "Renamed chat")
        found = self.request("/api/conversations?q=blue")
        self.assertTrue(any(row["id"] == conversation_id
                            for row in found["conversations"]))

        deleted = self.request(f"/api/conversations/{conversation_id}/delete", {})
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(f"/api/conversations/{conversation_id}")
        self.assertEqual(caught.exception.code, 404)

    def test_streaming_chat_emits_sse_frames_and_end_marker(self):
        created = self.request("/api/conversations", {"title": "Stream"})
        raw = self.request("/api/chat", {
            "conversation_id": created["conversation"]["id"],
            "message": "Stream a short answer",
            "mode": "safe",
            "stream": True,
        }, raw=True)
        self.assertIn("data:", raw)
        self.assertIn("event: end", raw)
        self.assertIn('"type": "result"', raw)
        self.assertIn("Streaming works.", raw)

    def test_settings_never_returns_a_secret(self):
        payload = self.request("/api/settings")
        self.assertIn("llm", payload)
        self.assertNotIn("key", payload["llm"])
        self.assertIn("key_configured", payload["llm"])


if __name__ == "__main__":
    unittest.main()
