"""Bring-your-own-key provider tests.

No network is used.  Connection probes are replaced with scripted probes, so
the tests check the security boundary and the honesty of capability reporting
rather than a provider's availability.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from watchcad import api, providers
from watchcad.api import Studio
from watchcad.nlp import parse
from watchcad.solver import solve

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")


class ProviderVaultTests(unittest.TestCase):
    def setUp(self):
        providers.VAULT._keys.clear()
        self.addCleanup(providers.VAULT._keys.clear)

    def test_keys_are_isolated_by_session_and_cleared(self):
        self.assertTrue(providers.VAULT.set("session-a", "sk-secret-a"))
        self.assertTrue(providers.VAULT.set("session-b", "sk-secret-b"))
        self.assertEqual(providers.VAULT.get("session-a"), "sk-secret-a")
        self.assertEqual(providers.VAULT.get("session-b"), "sk-secret-b")
        self.assertTrue(providers.VAULT.has("session-a"))
        self.assertTrue(providers.VAULT.clear("session-a"))
        self.assertFalse(providers.VAULT.has("session-a"))
        self.assertTrue(providers.VAULT.has("session-b"))
        self.assertEqual(providers.VAULT.count(), 1)

    def test_empty_key_removes_the_session_entry(self):
        providers.VAULT.set("session", "sk-secret")
        self.assertFalse(providers.VAULT.set("session", ""))
        self.assertFalse(providers.VAULT.has("session"))

    def test_catalog_never_contains_a_key(self):
        text = json.dumps(providers.catalog())
        for secret in ("api_key", "sk-", "WATCHCAD_LLM_KEY"):
            self.assertNotIn(secret, text)
        self.assertEqual({item["id"] for item in providers.catalog()},
                         set(providers.PROVIDERS))

    def test_payload_key_wins_over_the_session_vault(self):
        providers.VAULT.set("session", "sk-from-vault")
        config = providers.config_from_payload(
            {"provider": "groq", "key": "sk-from-payload"}, "session")
        self.assertEqual(config.key, "sk-from-payload")
        self.assertEqual(config.provider, "groq")
        config = providers.config_from_payload({"provider": "groq"}, "session")
        self.assertEqual(config.key, "sk-from-vault")

    def test_probe_reports_capabilities_without_storing_the_key(self):
        calls = {}
        original_chat = providers._probe_chat
        original_stream = providers._probe_stream

        def fake_chat(cfg, **kwargs):
            calls.setdefault("chat", []).append(kwargs)
            return True, "ok"

        def fake_stream(cfg):
            calls["stream"] = True
            return True, "text/event-stream"

        providers._probe_chat = fake_chat
        providers._probe_stream = fake_stream
        try:
            result = providers.probe({
                "provider": "openrouter",
                "endpoint": "https://example.invalid/v1",
                "model": "openai/gpt-4o-mini",
                "key": "sk-not-stored",
            }, "session")
        finally:
            providers._probe_chat = original_chat
            providers._probe_stream = original_stream
        self.assertTrue(result["ok"])
        self.assertTrue(result["capabilities"]["streaming"]["supported"])
        self.assertTrue(result["capabilities"]["tools"]["supported"])
        self.assertTrue(result["capabilities"]["json"]["supported"])
        self.assertTrue(calls.get("stream"))
        self.assertFalse(providers.VAULT.has("session"))
        self.assertNotIn("sk-not-stored", json.dumps(result))

    def test_probe_rejects_missing_credentials(self):
        result = providers.probe({"provider": "groq", "key": ""}, "session")
        self.assertFalse(result["ok"])
        self.assertIn("API key", result["error"])

    def test_probe_rejects_non_http_endpoints(self):
        result = providers.probe({
            "provider": "openai-compatible",
            "endpoint": "file:///etc/passwd",
            "key": "sk-not-used",
        }, "session")
        self.assertFalse(result["ok"])
        self.assertIn("http", result["error"])
        self.assertFalse(providers.VAULT.has("session"))

    def test_error_redaction_removes_the_key(self):
        text = providers._redact("boom: sk-live-secret failed", "sk-live-secret")
        self.assertNotIn("sk-live-secret", text)
        self.assertIn("[redacted]", text)

    def test_vision_capability_comes_from_the_model_name(self):
        caps = providers.preset_capabilities("openrouter", "openai/gpt-4o-mini")
        self.assertTrue(caps["images"]["supported"])
        caps = providers.preset_capabilities("groq", "llama-3.1-8b-instant")
        self.assertFalse(caps["images"]["supported"])


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        providers.VAULT._keys.clear()
        self.addCleanup(providers.VAULT._keys.clear)
        self.old_config = api.LLM_CONFIG
        self.old_client = api.LLM_CLIENT
        api.LLM_CONFIG = None
        api.LLM_CLIENT = None

    def tearDown(self):
        api.LLM_CONFIG = self.old_config
        api.LLM_CLIENT = self.old_client

    def test_session_key_is_used_for_that_session_only(self):
        providers.VAULT.set("session-a", "sk-session-a")
        providers.VAULT.set("session-b", "sk-session-b")
        self.assertEqual(api._conversation_config("session-a").key, "sk-session-a")
        self.assertEqual(api._conversation_config("session-b").key, "sk-session-b")
        self.assertNotEqual(api._conversation_config("session-c").key, "sk-session-a")


class ProviderApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer

        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_settings = api.app_settings.SETTINGS_PATH
        cls.old_build = api.BUILD_ROOT
        cls.old_studio = api.STUDIO
        cls.old_sessions = api.SESSIONS
        api.app_settings.SETTINGS_PATH = Path(cls.tmp.name) / "settings.json"
        api.BUILD_ROOT = os.path.join(cls.tmp.name, "builds")
        api.STUDIO = Studio(solve(parse(DIVER, name="ProviderApi").spec)[0])
        api.SESSIONS = api.SessionManager(lambda: api.STUDIO, lambda: api.Studio())
        api.SERVER = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.port = api.SERVER.server_address[1]
        cls.thread = threading.Thread(target=api.SERVER.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        api.SERVER.shutdown()
        cls.thread.join(timeout=5)
        api.SERVER = None
        api.app_settings.SETTINGS_PATH = cls.old_settings
        api.BUILD_ROOT = cls.old_build
        api.STUDIO = cls.old_studio
        api.SESSIONS = cls.old_sessions
        cls.tmp.cleanup()

    def setUp(self):
        providers.VAULT._keys.clear()
        self.addCleanup(providers.VAULT._keys.clear)

    def request(self, path, body=None, session=None):
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if session:
            headers["X-WatchCAD-Session"] = session
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            method="POST" if body is not None else "GET", headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())

    def test_keys_are_session_scoped_and_never_echoed(self):
        secret = "sk-live-secret-do-not-echo"
        saved = self.request("/api/providers/save", {
            "provider": "groq",
            "endpoint": "https://api.groq.com/openai/v1",
            "model": "llama-3.3-70b-versatile",
            "key": secret,
        }, session="provider-a")
        self.assertNotIn(secret, json.dumps(saved))
        self.assertTrue(saved["session_key"])
        self.assertTrue(saved["active"]["session_key"])
        self.assertNotIn("key", saved["active"])

        listing = self.request("/api/providers", session="provider-a")
        self.assertTrue(listing["active"]["session_key"])
        self.assertNotIn(secret, json.dumps(listing))
        self.assertEqual(listing["security"]["persisted"], False)
        self.assertEqual(listing["security"]["storage"],
                         "process memory, per browser session")

        other = self.request("/api/providers", session="provider-b")
        self.assertFalse(other["active"]["session_key"])

        removed = self.request("/api/providers/remove", {}, session="provider-a")
        self.assertNotIn(secret, json.dumps(removed))
        self.assertFalse(removed["active"]["session_key"])
        self.assertFalse(self.request("/api/providers", session="provider-a")
                         ["active"]["session_key"])

    def test_save_rejects_non_http_endpoints(self):
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/providers/save", {
                "provider": "openai-compatible",
                "endpoint": "file:///etc/passwd",
                "model": "x",
                "key": "sk-not-stored",
            }, session="provider-a")
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertFalse(providers.VAULT.has("provider-a"))

    def test_model_glb_endpoint_serves_binary_gltf(self):
        import urllib.request

        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/model.glb", timeout=30) as response:
            self.assertEqual(response.headers.get_content_type(),
                             "model/gltf-binary")
            body = response.read()
        self.assertEqual(body[:4], b"glTF")
        self.assertGreater(len(body), 1000)

    def test_optimise_endpoint_returns_ranked_candidates(self):
        first = self.request("/api/optimise", {"objective": "weight", "budget": 5})
        second = self.request("/api/optimise", {"objective": "weight", "budget": 5})
        self.assertEqual(first["winner"], second["winner"])
        self.assertEqual(len(first["candidates"]), 5)
        self.assertEqual(first["baseline_checksum"],
                         api.STUDIO.solved.get("derived.checksum"))
        self.assertFalse(first["provenance"]["model_used"])
        self.assertEqual(first["objective"], "weight")


if __name__ == "__main__":
    unittest.main()
