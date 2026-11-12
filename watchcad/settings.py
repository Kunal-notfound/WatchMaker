"""Non-secret application settings.

Environment variables always win.  A small JSON file lets the desktop UI change
the model and research provider without editing the environment; secrets are
never written to that file by the API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path(
    os.environ.get("WATCHCAD_SETTINGS",
                   Path(__file__).resolve().parent.parent / "out" / "settings.json")
)

PUBLIC_KEYS = (
    "provider", "url", "model", "vision_model", "temperature", "max_tokens",
    "timeout_s", "retries", "retry_delay_s", "search_url", "search_provider",
)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(patch: dict) -> dict:
    data = load_settings()
    for key, value in (patch or {}).items():
        if key not in PUBLIC_KEYS:
            continue
        if value is None or value == "":
            data.pop(key, None)
        else:
            data[key] = value
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
    except OSError:
        pass
    return data


def public_settings() -> dict:
    """Settings safe to send to the browser (never includes secrets)."""
    data = load_settings()
    return {key: data.get(key) for key in PUBLIC_KEYS if key in data}


def setting(name: str, default: Any = "") -> Any:
    return load_settings().get(name, default)
