"""Optional OpenAI-compatible model client.

The deterministic CAD engine never depends on a model.  This module is a thin
standard-library HTTP client used by the conversational runtime when a provider
is configured.  It supports:

* native tool calling (including multi-round conversations),
* streaming text deltas and streamed tool-call fragments,
* token usage reporting when the provider returns it,
* retries for transient transport and server failures,
* provider-specific endpoint/header details isolated behind ``LLMConfig``,
* multimodal image content for vision-capable models.

Every failure is a typed :class:`LLMError`; callers must never pretend a model
answered when it did not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import settings as app_settings


class LLMError(RuntimeError):
    """The configured endpoint could not produce a usable answer."""

    def __init__(self, message: str, *, status: int | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class LLMNotConfigured(LLMError):
    """No provider is configured; this is not a successful model response."""


@dataclass
class LLMConfig:
    url: str = ""
    key: str = ""
    model: str = "gpt-4o-mini"
    timeout_s: float = 60.0
    temperature: float = 0.3
    max_tokens: int = 0
    retries: int = 1
    retry_delay_s: float = 0.4
    provider: str = "openai-compatible"
    vision_model: str = ""
    api_version: str = "2024-08-01-preview"
    search_url: str = ""
    search_key: str = ""
    search_provider: str = "generic"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        file_cfg = app_settings.load_settings()

        def pick(env: str, key: str, default: Any = "") -> Any:
            value = os.environ.get(env)
            if value is not None and str(value).strip() != "":
                return value
            return file_cfg.get(key, default)

        try:
            timeout = float(pick("WATCHCAD_LLM_TIMEOUT", "timeout_s", 60) or 60)
        except (TypeError, ValueError):
            timeout = 60.0
        try:
            temperature = float(pick("WATCHCAD_LLM_TEMPERATURE", "temperature", 0.3)
                                or 0.3)
        except (TypeError, ValueError):
            temperature = 0.3
        try:
            max_tokens = int(pick("WATCHCAD_LLM_MAX_TOKENS", "max_tokens", 0) or 0)
        except (TypeError, ValueError):
            max_tokens = 0
        try:
            retries = int(pick("WATCHCAD_LLM_RETRIES", "retries", 1) or 1)
        except (TypeError, ValueError):
            retries = 1
        try:
            delay = float(pick("WATCHCAD_LLM_RETRY_DELAY", "retry_delay_s", 0.4) or 0)
        except (TypeError, ValueError):
            delay = 0.4
        return cls(
            url=str(pick("WATCHCAD_LLM_URL", "url", "")).strip(),
            key=str(os.environ.get("WATCHCAD_LLM_KEY")
                    or os.environ.get("WATCHCAD_LLM_API_KEY") or "").strip(),
            model=str(pick("WATCHCAD_LLM_MODEL", "model", "gpt-4o-mini")).strip()
            or "gpt-4o-mini",
            timeout_s=max(3.0, timeout),
            temperature=max(0.0, min(2.0, temperature)),
            max_tokens=max(0, max_tokens),
            retries=max(0, min(5, retries)),
            retry_delay_s=max(0.0, min(5.0, delay)),
            provider=str(pick("WATCHCAD_LLM_PROVIDER", "provider",
                              "openai-compatible")).strip() or "openai-compatible",
            vision_model=str(pick("WATCHCAD_LLM_VISION_MODEL", "vision_model", "")).strip(),
            search_url=str(pick("WATCHCAD_SEARCH_URL", "search_url", "")).strip(),
            search_key=str(os.environ.get("WATCHCAD_SEARCH_KEY", "")).strip(),
            search_provider=str(pick("WATCHCAD_SEARCH_PROVIDER", "search_provider",
                                     "generic")).strip() or "generic",
        )

    @classmethod
    def from_dict(cls, values: dict | None) -> "LLMConfig":
        base = cls.from_env()
        values = values or {}
        for key in ("url", "key", "model", "provider", "vision_model",
                    "api_version", "search_url", "search_key", "search_provider"):
            if values.get(key) not in (None, ""):
                setattr(base, key, str(values[key]))
        for key in ("timeout_s", "temperature", "max_tokens", "retries",
                    "retry_delay_s"):
            if values.get(key) is not None:
                try:
                    setattr(base, key, type(getattr(base, key))(values[key]))
                except (TypeError, ValueError):
                    pass
        return base

    @property
    def available(self) -> bool:
        return bool(self.url)

    @property
    def endpoint(self) -> str:
        url = self.url.rstrip("/")
        provider = (self.provider or "").lower()
        if provider in ("azure", "azure-openai", "azure_openai"):
            base = url
            if "/openai/deployments/" in base:
                return base
            return (f"{base}/openai/deployments/{self.model}/chat/completions"
                    f"?api-version={self.api_version}")
        if url.endswith("/chat/completions"):
            return url
        if provider == "ollama" and not url.endswith("/v1"):
            url += "/v1"
        return url + "/chat/completions"

    @property
    def image_model(self) -> str:
        return self.vision_model or self.model

    def public_dict(self) -> dict:
        return {
            "configured": self.available,
            "provider": self.provider,
            "model": self.model,
            "vision_model": self.vision_model,
            "image_model": self.image_model,
            "url_configured": bool(self.url),
            "key_configured": bool(self.key),
            "timeout_s": self.timeout_s,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "retries": self.retries,
            "search_configured": bool(self.search_url),
            "search_provider": self.search_provider,
        }


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    model: str = ""
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    streamed: bool = False

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _headers(cfg: LLMConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    provider = (cfg.provider or "").lower()
    if provider in ("azure", "azure-openai", "azure_openai"):
        if cfg.key:
            headers["api-key"] = cfg.key
    elif cfg.key:
        headers["Authorization"] = f"Bearer {cfg.key}"
    return headers


def _body(cfg: LLMConfig, messages: list[dict], *, tools: list[dict] | None,
          tool_choice: str | dict, response_format: dict | None,
          stream: bool, model: str | None = None) -> dict:
    body: dict[str, Any] = {
        "model": model or cfg.model,
        "temperature": cfg.temperature,
        "stream": stream,
        "messages": messages,
    }
    if cfg.max_tokens:
        body["max_tokens"] = cfg.max_tokens
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if response_format:
        body["response_format"] = response_format
    return body


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:600]
    except Exception:  # noqa: BLE001 - best effort only
        return str(exc.reason)


def _request(cfg: LLMConfig, body: dict, *, timeout: float | None = None):
    data = json.dumps(body).encode()
    request = urllib.request.Request(cfg.endpoint, data=data,
                                     headers=_headers(cfg))
    attempts = max(0, int(cfg.retries)) + 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return urllib.request.urlopen(request, timeout=timeout or cfg.timeout_s)
        except urllib.error.HTTPError as exc:
            detail = _read_error(exc)
            last = LLMError(f"LLM HTTP {exc.code}: {detail or exc.reason}",
                            status=exc.code,
                            retryable=exc.code in (408, 409, 429) or exc.code >= 500)
            if not last.retryable or attempt + 1 >= attempts:
                raise last from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = LLMError(f"LLM unavailable: {type(exc).__name__}: {exc}",
                            retryable=True)
            if attempt + 1 >= attempts:
                raise last from exc
        if cfg.retry_delay_s:
            time.sleep(cfg.retry_delay_s * (attempt + 1))
    raise last or LLMError("LLM request failed")


def _content_text(content) -> str:
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) if isinstance(part, dict)
                       else str(part) for part in content)
    return str(content or "")


def _usage(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    out = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(usage.get(key), (int, float)):
            out[key] = int(usage[key])
    return out


def _tool_calls(message: dict) -> list[dict]:
    calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {"__raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {"value": arguments}
        calls.append({
            "id": call.get("id") or f"call_{index}",
            "name": function.get("name") or call.get("name") or "",
            "arguments": arguments,
        })
    return calls


def _reply_from_payload(payload: dict, model: str) -> LLMReply:
    try:
        choice = payload["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("LLM response did not contain a message") from exc
    return LLMReply(content=_content_text(message.get("content")),
                    tool_calls=_tool_calls(message), raw=payload, model=model,
                    usage=_usage(payload),
                    finish_reason=str(choice.get("finish_reason") or ""))


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         config: LLMConfig | None = None, tool_choice: str | dict = "auto",
         response_format: dict | None = None,
         timeout: float | None = None, model: str | None = None) -> LLMReply:
    """Call an OpenAI-compatible chat completion endpoint once."""
    cfg = config or LLMConfig.from_env()
    if not cfg.available:
        raise LLMNotConfigured(
            "no LLM endpoint configured (set WATCHCAD_LLM_URL or the model settings)")
    body = _body(cfg, messages, tools=tools, tool_choice=tool_choice,
                 response_format=response_format, stream=False, model=model)
    with _request(cfg, body, timeout=timeout) as response:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM returned invalid JSON: {exc}") from exc
    return _reply_from_payload(payload, model or cfg.model)


def _iter_sse(response):
    """Yield parsed JSON objects from an upstream text/event-stream."""
    buffer = ""
    while True:
        raw = response.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if buffer:
                yield buffer
                buffer = ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            chunk = line[5:].lstrip()
            if chunk == "[DONE]":
                return
            buffer += chunk
    if buffer.strip():
        yield buffer


def stream_chat(messages: list[dict], *, tools: list[dict] | None = None,
                config: LLMConfig | None = None, tool_choice: str | dict = "auto",
                timeout: float | None = None, model: str | None = None,
                on_text: Callable[[str], None] | None = None,
                on_tool: Callable[[str], None] | None = None) -> LLMReply:
    """Stream one completion, invoking ``on_text`` for each text delta.

    Tool-call fragments are accumulated by index and returned as a normal
    :class:`LLMReply`, so the rest of the application does not care whether the
    provider streamed or not.
    """
    cfg = config or LLMConfig.from_env()
    if not cfg.available:
        raise LLMNotConfigured(
            "no LLM endpoint configured (set WATCHCAD_LLM_URL or the model settings)")
    body = _body(cfg, messages, tools=tools, tool_choice=tool_choice,
                 response_format=None, stream=True, model=model)
    text_parts: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict = {}
    finish = ""
    with _request(cfg, body, timeout=timeout) as response:
        ctype = str(response.headers.get("Content-Type") or "")
        if "text/event-stream" not in ctype:
            # Some gateways ignore stream=true; accept a normal JSON reply.
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LLMError(f"LLM returned invalid JSON: {exc}") from exc
            return _reply_from_payload(payload, model or cfg.model)
        for raw in _iter_sse(response):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            usage.update(_usage(payload))
            choices = payload.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            finish = str(choice.get("finish_reason") or finish)
            delta = choice.get("delta") or choice.get("message") or {}
            content = _content_text(delta.get("content"))
            if content:
                text_parts.append(content)
                if callable(on_text):
                    on_text(content)
            for index, fragment in enumerate(delta.get("tool_calls") or []):
                slot = int(fragment.get("index", index))
                current = calls.setdefault(slot, {
                    "id": "", "name": "", "arguments": "",
                })
                if fragment.get("id"):
                    current["id"] = fragment["id"]
                function = fragment.get("function") or {}
                if function.get("name"):
                    current["name"] += function["name"]
                    if callable(on_tool):
                        on_tool(current["name"])
                if function.get("arguments"):
                    current["arguments"] += function["arguments"]
    parsed_calls = []
    for slot in sorted(calls):
        current = calls[slot]
        try:
            arguments = json.loads(current["arguments"]) \
                if current["arguments"].strip() else {}
        except json.JSONDecodeError:
            arguments = {"__raw": current["arguments"]}
        if not isinstance(arguments, dict):
            arguments = {"value": arguments}
        parsed_calls.append({
            "id": current["id"] or f"call_{slot}",
            "name": current["name"],
            "arguments": arguments,
        })
    return LLMReply(content="".join(text_parts), tool_calls=parsed_calls,
                    raw={"streamed": True}, model=model or cfg.model,
                    usage=usage, finish_reason=finish, streamed=True)


def complete_json(messages: list[dict], *, config: LLMConfig | None = None,
                  timeout: float | None = None) -> dict:
    """Ask for a JSON object and parse it, tolerating fenced output."""
    reply = chat(messages, config=config, timeout=timeout,
                 response_format={"type": "json_object"})
    return parse_json_object(reply.content)


def parse_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise LLMError("LLM returned an empty answer")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise LLMError("LLM JSON answer must be an object")
    return obj


def assistant_tool_message(reply: LLMReply) -> dict:
    """A provider-neutral assistant message carrying tool calls."""
    return {
        "role": "assistant",
        "content": reply.content,
        "tool_calls": [
            {"id": call["id"], "type": "function",
             "function": {"name": call["name"],
                          "arguments": json.dumps(call.get("arguments") or {})}}
            for call in reply.tool_calls
        ],
    }


def tool_result_message(call_id: str, name: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name,
            "content": json.dumps(payload, default=str)[:24000]}


def user_content(text: str, images: list[str] | None = None) -> dict | str:
    """An OpenAI-compatible user message with optional image parts."""
    images = [url for url in (images or []) if url]
    if not images:
        return {"role": "user", "content": text}
    parts: list[dict] = [{"type": "text", "text": text}]
    for url in images[:6]:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": parts}
