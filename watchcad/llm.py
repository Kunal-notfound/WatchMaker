"""Optional OpenAI-compatible model client.

The CAD engine never depends on a model.  This module is a thin, standard
library HTTP client used by the agent when ``WATCHCAD_LLM_URL`` is configured.
It supports native tool calling and a plain JSON completion fallback.  Every
failure is reported as a typed exception so the agent can fall back to the
deterministic local planner instead of pretending a change succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    """The configured endpoint could not produce a usable answer."""


@dataclass
class LLMConfig:
    url: str = ""
    key: str = ""
    model: str = "gpt-4o-mini"
    timeout_s: float = 30.0
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            url=os.environ.get("WATCHCAD_LLM_URL", "").strip(),
            key=os.environ.get("WATCHCAD_LLM_KEY", "").strip(),
            model=os.environ.get("WATCHCAD_LLM_MODEL", "gpt-4o-mini").strip()
            or "gpt-4o-mini",
            timeout_s=float(os.environ.get("WATCHCAD_LLM_TIMEOUT", "30") or 30),
        )

    @property
    def available(self) -> bool:
        return bool(self.url)

    @property
    def endpoint(self) -> str:
        url = self.url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        return url


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         config: LLMConfig | None = None, tool_choice: str | dict = "auto",
         response_format: dict | None = None,
         timeout: float | None = None) -> LLMReply:
    """Call an OpenAI-compatible chat completion endpoint once."""
    cfg = config or LLMConfig.from_env()
    if not cfg.available:
        raise LLMError("no LLM endpoint configured (set WATCHCAD_LLM_URL)")
    body: dict[str, Any] = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "stream": False,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if response_format:
        body["response_format"] = response_format
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if cfg.key:
        headers["Authorization"] = f"Bearer {cfg.key}"
    request = urllib.request.Request(cfg.endpoint, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout or cfg.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - best effort only
            detail = ""
        raise LLMError(f"LLM HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"LLM unavailable: {type(exc).__name__}: {exc}") from exc

    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("LLM response did not contain a message") from exc
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part)
                          for part in content)
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
    return LLMReply(content=str(content), tool_calls=calls, raw=payload,
                    model=cfg.model)


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
