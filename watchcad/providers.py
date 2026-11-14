"""Bring-your-own-key provider presets, session key vault and capability probes.

Security model
--------------
API keys live only in this process, in :class:`ProviderVault`, keyed by the
browser session id sent with each request.  They are never written to the
settings JSON, never included in an API response and never logged.  The vault
is deliberately not persistent: restarting the server forgets every key.

Non-secret fields (provider, endpoint, model, vision model) are ordinary
settings and may be persisted.  A provider preset only supplies defaults; the
user can point any preset at a compatible endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

from . import llm
from .llm import LLMConfig, LLMError

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, dict] = {
    "gemini": {
        "id": "gemini",
        "name": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "vision_model": "gemini-2.0-flash",
        "models": ["gemini-2.0-flash", "gemini-2.0-flash-lite",
                   "gemini-1.5-pro", "gemini-1.5-flash"],
        "capabilities": {"streaming": True, "tools": True, "json": True,
                         "images": True},
        "note": "OpenAI-compatible endpoint; the key is sent as a bearer token.",
        "docs": "https://ai.google.dev/gemini-api/docs/openai",
    },
    "cloudflare": {
        "id": "cloudflare",
        "name": "Cloudflare Workers AI",
        "url": "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1",
        "model": "@cf/meta/llama-3.1-8b-instruct",
        "vision_model": "",
        "models": ["@cf/meta/llama-3.1-8b-instruct",
                   "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                   "@cf/qwen/qwen2.5-coder-32b-instruct"],
        "capabilities": {"streaming": True, "tools": True, "json": True,
                         "images": False},
        "note": "Replace <ACCOUNT_ID> in the endpoint with your Cloudflare account id.",
        "docs": "https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/",
    },
    "groq": {
        "id": "groq",
        "name": "Groq",
        "url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "vision_model": "",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                   "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct"],
        "capabilities": {"streaming": True, "tools": True, "json": True,
                         "images": False},
        "note": "Fast OpenAI-compatible inference.",
        "docs": "https://console.groq.com/docs/openai",
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "vision_model": "openai/gpt-4o-mini",
        "models": ["openai/gpt-4o-mini", "openai/gpt-4o",
                   "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash-001",
                   "meta-llama/llama-3.3-70b-instruct"],
        "capabilities": {"streaming": True, "tools": True, "json": True,
                         "images": True},
        "note": "One key for many model families; model ids include the vendor.",
        "docs": "https://openrouter.ai/docs",
    },
    "openai-compatible": {
        "id": "openai-compatible",
        "name": "OpenAI-compatible",
        "url": "",
        "model": "gpt-4o-mini",
        "vision_model": "",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "qwen2.5-32b-instruct"],
        "capabilities": {"streaming": True, "tools": True, "json": True,
                         "images": False},
        "note": "Any endpoint that implements /v1/chat/completions.",
        "docs": "",
    },
}

_VISION_HINTS = (
    "gpt-4o", "gpt-4.1", "gpt-5", "gemini", "claude-3", "claude-4",
    "vision", "llava", "pixtral", "qwen-vl", "llama-3.2-11b-vision",
    "llama-3.2-90b-vision", "llama-4-scout", "llama-4-maverick",
)


def vision_model_name(model: str) -> bool:
    text = str(model or "").lower()
    return any(hint in text for hint in _VISION_HINTS)


def preset_capabilities(provider: str, model: str = "") -> dict:
    preset = PROVIDERS.get(provider) or PROVIDERS["openai-compatible"]
    caps = dict(preset.get("capabilities") or {})
    if model:
        caps["images"] = bool(caps.get("images")) or vision_model_name(model)
    return {name: {"supported": bool(value), "source": "preset"}
            for name, value in caps.items()}


def catalog() -> list[dict]:
    """Provider presets safe to send to the browser (never a key)."""
    out = []
    for preset in PROVIDERS.values():
        item = {key: value for key, value in preset.items()}
        item["capabilities"] = preset_capabilities(preset["id"], preset["model"])
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Session key vault
# ---------------------------------------------------------------------------
class ProviderVault:
    """Process-memory API keys keyed by browser session id."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._keys: dict[str, str] = {}

    @staticmethod
    def normalize_session(session_id: str) -> str:
        value = str(session_id or "default").strip()
        return value[:120] or "default"

    def set(self, session_id: str, key: str) -> bool:
        key = str(key or "").strip()
        sid = self.normalize_session(session_id)
        with self._lock:
            if not key:
                self._keys.pop(sid, None)
                return False
            self._keys[sid] = key
            return True

    def get(self, session_id: str) -> str:
        with self._lock:
            return self._keys.get(self.normalize_session(session_id), "")

    def has(self, session_id: str) -> bool:
        return bool(self.get(session_id))

    def clear(self, session_id: str) -> bool:
        with self._lock:
            return self._keys.pop(self.normalize_session(session_id), None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._keys)


VAULT = ProviderVault()


# ---------------------------------------------------------------------------
# Configuration + probing
# ---------------------------------------------------------------------------
def config_from_payload(payload: dict, session_id: str) -> LLMConfig:
    payload = payload or {}
    provider = str(payload.get("provider") or "openai-compatible").strip()
    preset = PROVIDERS.get(provider) or PROVIDERS["openai-compatible"]
    url = str(payload.get("endpoint") or payload.get("url") or preset["url"]).strip()
    model = str(payload.get("model") or preset["model"]).strip()
    vision = str(payload.get("vision_model") or preset.get("vision_model") or "").strip()
    key = str(payload.get("key") or "").strip() or VAULT.get(session_id)
    return LLMConfig(
        url=url.rstrip("/"),
        key=key,
        model=model or "gpt-4o-mini",
        provider=provider,
        vision_model=vision,
        timeout_s=15.0,
        temperature=0.0,
        max_tokens=16,
        retries=0,
        retry_delay_s=0.0,
    )


def _redact(text: Any, key: str) -> str:
    value = str(text or "")
    if key:
        value = value.replace(key, "[redacted]")
    return value[:600]


def endpoint_scheme_ok(url: Any) -> bool:
    """Only plain HTTP(S) endpoints are accepted for probes and saves."""
    return str(url or "").strip().lower().startswith(("http://", "https://"))


def _probe_chat(cfg: LLMConfig, **kwargs) -> tuple[bool, str]:
    try:
        reply = llm.chat(
            [{"role": "user", "content": "Reply with the single word ok."}],
            config=cfg, timeout=15.0, **kwargs,
        )
        return True, _redact(reply.content, cfg.key)[:120]
    except Exception as exc:  # noqa: BLE001 - a failed probe is data, not a crash
        return False, _redact(f"{type(exc).__name__}: {exc}", cfg.key)


def _probe_stream(cfg: LLMConfig) -> tuple[bool, str]:
    body = llm._body(cfg, [{"role": "user", "content": "Reply with ok."}],
                     tools=None, tool_choice="auto", response_format=None,
                     stream=True)
    try:
        with llm._request(cfg, body, timeout=15.0) as response:
            ctype = str(response.headers.get("Content-Type") or "")
            response.read(2048)
        if "text/event-stream" in ctype:
            return True, ctype
        return False, f"endpoint answered {ctype or 'without a content type'}"
    except Exception as exc:  # noqa: BLE001
        return False, _redact(f"{type(exc).__name__}: {exc}", cfg.key)


def probe(payload: dict, session_id: str) -> dict:
    """Run a real connection test and capability probes.

    Nothing is stored.  The caller decides whether to keep the key after
    seeing the result.  Every probe is a tiny non-streaming request except the
    streaming probe, and all error text is redacted.
    """
    cfg = config_from_payload(payload, session_id)
    if not cfg.url:
        return {"ok": False, "error": "an endpoint URL is required",
                "capabilities": preset_capabilities(cfg.provider, cfg.model)}
    if not endpoint_scheme_ok(cfg.url):
        return {"ok": False, "error": "endpoint must be http:// or https://",
                "capabilities": preset_capabilities(cfg.provider, cfg.model)}
    if not cfg.key:
        return {"ok": False, "error": "an API key is required",
                "capabilities": preset_capabilities(cfg.provider, cfg.model)}
    if "cloudflare" in cfg.provider and "<ACCOUNT_ID>" in cfg.url:
        return {"ok": False,
                "error": "replace <ACCOUNT_ID> in the endpoint with your Cloudflare account id",
                "capabilities": preset_capabilities(cfg.provider, cfg.model)}

    ok, detail = _probe_chat(cfg)
    capabilities = preset_capabilities(cfg.provider, cfg.model)
    if not ok:
        return {"ok": False, "error": detail,
                "endpoint": cfg.url, "model": cfg.model,
                "capabilities": capabilities}

    streaming, stream_detail = _probe_stream(cfg)
    tools, tools_detail = _probe_chat(cfg, tools=[{
        "type": "function",
        "function": {
            "name": "watchcad_probe",
            "description": "Return a status value.",
            "parameters": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
            },
        },
    }], tool_choice="auto")
    json_ok, json_detail = _probe_chat(
        cfg, response_format={"type": "json_object"})

    capabilities = {
        "streaming": {"supported": streaming, "source": "probe",
                      "detail": stream_detail},
        "tools": {"supported": tools, "source": "probe",
                  "detail": tools_detail},
        "json": {"supported": json_ok, "source": "probe",
                 "detail": json_detail},
        "images": {"supported": vision_model_name(cfg.model) or bool(cfg.vision_model),
                   "source": "model-name",
                   "detail": cfg.vision_model or cfg.model},
    }
    return {
        "ok": True,
        "endpoint": cfg.url,
        "model": cfg.model,
        "detail": detail,
        "capabilities": capabilities,
    }
