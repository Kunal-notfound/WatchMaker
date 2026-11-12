"""Optional live research tools with explicit provenance.

The assistant may always use the curated local knowledge base.  Live web search
and source retrieval are only attempted when a search provider is configured
(``WATCHCAD_SEARCH_URL``); otherwise the result says so instead of inventing a
source.  Fetched pages are reduced to text and every result carries its URL and
retrieval timestamp.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

from .. import settings as app_settings
from .knowledge import lookup


MAX_FETCH_BYTES = 600_000
MAX_TEXT_CHARS = 14_000
USER_AGENT = "WatchMaker/2.0 (local design assistant; research tool)"


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _search_config() -> tuple[str, str, str]:
    try:
        from ..llm import LLMConfig

        cfg = LLMConfig.from_env()
        return cfg.search_url, cfg.search_key, cfg.search_provider
    except Exception:  # noqa: BLE001 - research must never break a conversation
        data = app_settings.load_settings()
        return (str(data.get("search_url") or ""),
                str(__import__("os").environ.get("WATCHCAD_SEARCH_KEY") or ""),
                str(data.get("search_provider") or "generic"))


def _normalise_result(item: dict) -> dict:
    url = str(item.get("url") or item.get("link") or item.get("href") or "")
    snippet = (item.get("snippet") or item.get("content")
               or item.get("description") or item.get("text") or "")
    return {
        "title": str(item.get("title") or item.get("name") or url)[:240],
        "url": url,
        "snippet": re.sub(r"\s+", " ", str(snippet))[:900],
        "provider": "web",
    }


def web_search(query: str, limit: int = 5) -> dict:
    """Search a configured JSON search endpoint, honestly reporting absence."""
    query = (query or "").strip()
    limit = max(1, min(10, int(limit or 5)))
    url, key, provider = _search_config()
    if not query:
        return {"configured": bool(url), "query": query, "results": [],
                "note": "empty query"}
    if not url:
        return {
            "configured": False,
            "query": query,
            "results": [],
            "retrieved_at": _iso_now(),
            "note": ("Live web search is not configured. Set WATCHCAD_SEARCH_URL "
                     "to an endpoint that returns JSON results. No sources were "
                     "invented for this answer."),
        }
    separator = "&" if "?" in url else "?"
    target = f"{url}{separator}{urllib.parse.urlencode({'q': query, 'limit': limit})}"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(target, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(MAX_FETCH_BYTES)
        payload = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"configured": True, "query": query, "results": [],
                "retrieved_at": _iso_now(),
                "error": f"search HTTP {exc.code}: {exc.reason}",
                "provider": provider}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"configured": True, "query": query, "results": [],
                "retrieved_at": _iso_now(),
                "error": f"search unavailable: {type(exc).__name__}: {exc}",
                "provider": provider}
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = (payload.get("results") or payload.get("items")
                 or payload.get("web") or payload.get("data") or [])
        if isinstance(items, dict):
            items = items.get("value") or items.get("results") or []
    else:
        items = []
    results = [_normalise_result(item) for item in items
               if isinstance(item, dict) and (item.get("url") or item.get("link"))]
    return {
        "configured": True,
        "query": query,
        "provider": provider,
        "results": results[:limit],
        "retrieved_at": _iso_now(),
        "note": "Search results are unverified snippets. Open a source before "
                "treating a number as a manufacturer specification.",
    }


def _public_host(hostname: str) -> bool:
    """Reject loopback/private addresses so the tool cannot probe the LAN."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _html_to_text(html: str) -> tuple[str, str]:
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if match:
        title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()[:240]
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return title, text.strip()[:MAX_TEXT_CHARS]


def fetch_source(url: str) -> dict:
    """Retrieve one public http(s) page and return text plus provenance."""
    url = (url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "url": url,
                "error": "only public http(s) URLs can be retrieved"}
    if not _public_host(parsed.hostname):
        return {"ok": False, "url": url,
                "error": "the host resolves to a private or local address"}
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
    })
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            ctype = str(response.headers.get("Content-Type") or "")
            raw = response.read(MAX_FETCH_BYTES)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        return {"ok": False, "url": url,
                "error": f"HTTP {exc.code}: {exc.reason}", "retrieved_at": _iso_now()}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "url": url,
                "error": f"{type(exc).__name__}: {exc}", "retrieved_at": _iso_now()}
    if "text/" not in ctype and "html" not in ctype and "json" not in ctype:
        return {"ok": False, "url": url,
                "error": f"unsupported content type {ctype or 'unknown'}",
                "retrieved_at": _iso_now()}
    text = raw.decode("utf-8", "replace")
    if "html" in ctype or text.lstrip().startswith("<"):
        title, body = _html_to_text(text)
    else:
        title, body = "", text.strip()[:MAX_TEXT_CHARS]
    return {
        "ok": True,
        "url": final_url,
        "title": title,
        "text": body,
        "content_type": ctype,
        "retrieved_at": _iso_now(),
        "bytes": len(raw),
        "note": "Retrieved text is unverified. Quote the URL and timestamp; do "
                "not treat an estimate as a manufacturer specification.",
    }


def research(query: str, category: str | None = None, *,
             include_web: bool = True, limit: int = 5) -> dict:
    """Combine the curated knowledge base with optional live search results."""
    local = lookup(query, category=category)
    out: dict[str, Any] = {
        "query": query,
        "category": category or "",
        "local": local.get("matches", []),
        "local_note": local.get("note", ""),
        "web": {"configured": False, "results": []},
        "retrieved_at": _iso_now(),
    }
    if include_web:
        out["web"] = web_search(query, limit=limit)
    if not out["local"] and not out["web"].get("results"):
        out["note"] = ("Nothing authoritative matched. Ask the user for the "
                       "manufacturer or keep the value labelled as an estimate.")
    else:
        out["note"] = ("Curated entries and web snippets are different evidence "
                       "classes. Cite source URLs; never present an estimate or a "
                       "snippet as a verified manufacturer dimension.")
    return out
