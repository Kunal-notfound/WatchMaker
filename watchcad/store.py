"""Persistent conversation and design storage.

The CAD document and the conversation are deliberately separate:

* ``designs`` holds the latest verified specification for a design session.
* ``conversations`` holds the visible chat transcript.
* ``messages`` and ``tool_events`` hold what was said and what the assistant did.
* ``design_refs`` links a conversation to the design versions it touched.

Everything is SQLite from the Python standard library.  The store is safe to use
from the threaded HTTP server: one connection guarded by a re-entrant lock.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


def _now() -> float:
    return round(time.time(), 3)


def new_id(prefix: str = "conv") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return "{}"


def _loads(text: str | None, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


def default_db_path() -> Path:
    raw = os.environ.get("WATCHCAD_DB", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parent.parent / "out" / "watchcad.sqlite3"


class ConversationStore:
    """SQLite-backed conversations, messages, tool calls and design snapshots."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = str(path or default_db_path())
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # -- plumbing ---------------------------------------------------------
    @contextmanager
    def _cursor(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'New conversation',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    design_session TEXT NOT NULL DEFAULT 'default',
                    summary TEXT NOT NULL DEFAULT '',
                    summary_through INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    meta TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'text',
                    data TEXT NOT NULL DEFAULT '{}',
                    visible INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS messages_conversation
                    ON messages(conversation_id, position);
                CREATE TABLE IF NOT EXISTS tool_events (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    arguments TEXT NOT NULL DEFAULT '{}',
                    result TEXT NOT NULL DEFAULT '{}',
                    ok INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS tool_events_conversation
                    ON tool_events(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS designs (
                    session_id TEXT PRIMARY KEY,
                    spec TEXT NOT NULL,
                    checksum TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS design_refs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    checksum TEXT NOT NULL DEFAULT '',
                    label TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    through_position INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE CASCADE
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- conversations ----------------------------------------------------
    def create_conversation(self, *, conversation_id: str | None = None,
                            title: str = "", design_session: str = "default",
                            meta: dict | None = None) -> dict:
        cid = conversation_id or new_id("conv")
        stamp = _now()
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO conversations "
                "(id,title,created_at,updated_at,design_session,meta) "
                "VALUES (?,?,?,?,?,?)",
                (cid, title or "New conversation", stamp, stamp,
                 design_session or "default", _json(meta or {})),
            )
            cur.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                        (stamp, cid))
        return self.get_conversation(cid) or {}

    def get_conversation(self, conversation_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return self._conversation_row(row) if row else None

    def ensure_conversation(self, conversation_id: str, *,
                            design_session: str = "default",
                            title: str = "") -> dict:
        existing = self.get_conversation(conversation_id)
        if existing:
            return existing
        return self.create_conversation(conversation_id=conversation_id,
                                        title=title or "New conversation",
                                        design_session=design_session)

    def list_conversations(self, query: str = "", limit: int = 60) -> list[dict]:
        limit = max(1, min(200, int(limit or 60)))
        needle = f"%{(query or '').strip()}%"
        with self._lock:
            if (query or "").strip():
                rows = self._conn.execute(
                    "SELECT DISTINCT c.* FROM conversations c "
                    "LEFT JOIN messages m ON m.conversation_id=c.id "
                    "WHERE c.title LIKE ? OR m.content LIKE ? "
                    "ORDER BY c.updated_at DESC LIMIT ?",
                    (needle, needle, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._conversation_row(row) for row in rows]

    def rename_conversation(self, conversation_id: str, title: str) -> dict | None:
        title = (title or "").strip()[:160] or "Untitled conversation"
        with self._cursor() as cur:
            cur.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                        (title, _now(), conversation_id))
        return self.get_conversation(conversation_id)

    def autotitle(self, conversation_id: str, text: str) -> dict | None:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None
        title = (conversation.get("title") or "").strip()
        if title and title not in ("New conversation", "Untitled conversation"):
            return conversation
        cleaned = " ".join((text or "").split()).strip()
        if not cleaned:
            return conversation
        if len(cleaned) > 58:
            cleaned = cleaned[:55].rstrip() + "…"
        return self.rename_conversation(conversation_id, cleaned)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            deleted = cur.rowcount > 0
        return bool(deleted)

    def _conversation_row(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["meta"] = _loads(data.get("meta"), {})
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages "
                "WHERE conversation_id=? AND visible=1", (data["id"],)
            ).fetchone()
            last = self._conn.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE conversation_id=? AND visible=1 ORDER BY position DESC LIMIT 1",
                (data["id"],),
            ).fetchone()
        data["message_count"] = int(count["n"] if count else 0)
        data["last_message"] = dict(last) if last else None
        return data

    # -- messages ---------------------------------------------------------
    def add_message(self, conversation_id: str, role: str, content: str, *,
                    kind: str = "text", data: dict | None = None,
                    visible: bool = True, message_id: str | None = None) -> dict:
        mid = message_id or new_id("msg")
        stamp = _now()
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT COALESCE(MAX(position),0)+1 AS p FROM messages "
                "WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            position = int(row["p"] if row else 1)
            cur.execute(
                "INSERT INTO messages "
                "(id,conversation_id,position,role,content,kind,data,visible,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mid, conversation_id, position, role, content or "", kind,
                 _json(data or {}), 1 if visible else 0, stamp),
            )
            cur.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                        (stamp, conversation_id))
        return self.get_message(mid) or {}

    def get_message(self, message_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM messages WHERE id=?",
                                     (message_id,)).fetchone()
        return self._message_row(row) if row else None

    def messages(self, conversation_id: str, *, visible_only: bool = True,
                 limit: int = 400) -> list[dict]:
        sql = "SELECT * FROM messages WHERE conversation_id=?"
        args: list[Any] = [conversation_id]
        if visible_only:
            sql += " AND visible=1"
        sql += " ORDER BY position ASC LIMIT ?"
        args.append(max(1, min(2000, int(limit or 400))))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._message_row(row) for row in rows]

    def update_message(self, message_id: str, content: str,
                       data: dict | None = None) -> dict | None:
        with self._cursor() as cur:
            if data is None:
                cur.execute("UPDATE messages SET content=? WHERE id=?",
                            (content or "", message_id))
            else:
                cur.execute("UPDATE messages SET content=?, data=? WHERE id=?",
                            (content or "", _json(data), message_id))
        return self.get_message(message_id)

    def delete_message(self, message_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM messages WHERE id=?", (message_id,))
            return cur.rowcount > 0

    def delete_after(self, conversation_id: str, position: int,
                     *, inclusive: bool = False) -> int:
        op = ">=" if inclusive else ">"
        with self._cursor() as cur:
            cur.execute(
                f"DELETE FROM messages WHERE conversation_id=? AND position {op} ?",
                (conversation_id, int(position)),
            )
            return cur.rowcount

    def _message_row(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["data"] = _loads(data.get("data"), {})
        data["visible"] = bool(data.get("visible"))
        return data

    # -- tool events ------------------------------------------------------
    def add_tool_event(self, conversation_id: str, name: str,
                       arguments: dict | None = None, result: dict | None = None,
                       *, ok: bool = False, run_id: str = "") -> dict:
        event_id = new_id("tool")
        stamp = _now()
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO tool_events "
                "(id,conversation_id,run_id,name,arguments,result,ok,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (event_id, conversation_id, run_id, name,
                 _json(arguments or {}), _json(result or {}), 1 if ok else 0, stamp),
            )
            cur.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                        (stamp, conversation_id))
        return {"id": event_id, "conversation_id": conversation_id,
                "run_id": run_id, "name": name, "arguments": arguments or {},
                "result": result or {}, "ok": bool(ok), "created_at": stamp}

    def tool_events(self, conversation_id: str, limit: int = 80) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tool_events WHERE conversation_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (conversation_id, max(1, min(500, int(limit or 80)))),
            ).fetchall()
        out = []
        for row in reversed(rows):
            item = dict(row)
            item["arguments"] = _loads(item.get("arguments"), {})
            item["result"] = _loads(item.get("result"), {})
            item["ok"] = bool(item.get("ok"))
            out.append(item)
        return out

    # -- summaries --------------------------------------------------------
    def update_summary(self, conversation_id: str, summary: str,
                       through_position: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO summaries (conversation_id,content,through_position,created_at) "
                "VALUES (?,?,?,?)",
                (conversation_id, summary or "", int(through_position), _now()),
            )
            cur.execute(
                "UPDATE conversations SET summary=?, summary_through=? WHERE id=?",
                (summary or "", int(through_position), conversation_id),
            )
            # Keep the summary table bounded; the conversations row is canonical.
            cur.execute(
                "DELETE FROM summaries WHERE conversation_id=? AND id NOT IN "
                "(SELECT id FROM summaries WHERE conversation_id=? "
                " ORDER BY created_at DESC LIMIT 12)",
                (conversation_id, conversation_id),
            )

    # -- designs ----------------------------------------------------------
    def save_design(self, session_id: str, spec: dict,
                    checksum: str = "", label: str = "",
                    conversation_id: str | None = None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO designs (session_id,spec,checksum,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "spec=excluded.spec, checksum=excluded.checksum, "
                "updated_at=excluded.updated_at",
                (session_id, _json(spec), checksum or "", _now()),
            )
            if conversation_id:
                cur.execute(
                    "INSERT INTO design_refs "
                    "(conversation_id,session_id,checksum,label,created_at) "
                    "VALUES (?,?,?,?,?)",
                    (conversation_id, session_id, checksum or "", label or "", _now()),
                )
                cur.execute(
                    "DELETE FROM design_refs WHERE conversation_id=? AND id NOT IN "
                    "(SELECT id FROM design_refs WHERE conversation_id=? "
                    " ORDER BY created_at DESC LIMIT 40)",
                    (conversation_id, conversation_id),
                )

    def get_design(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM designs WHERE session_id=?", (session_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["spec"] = _loads(item.get("spec"), {})
        return item

    def design_refs(self, conversation_id: str, limit: int = 40) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM design_refs WHERE conversation_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (conversation_id, max(1, min(200, int(limit or 40)))),
            ).fetchall()
        return [dict(row) for row in rows]


def visible_transcript(store: ConversationStore, conversation_id: str) -> list[dict]:
    """Messages the user actually saw, in order, without tool protocol noise."""
    return [row for row in store.messages(conversation_id, visible_only=True)
            if row.get("role") in ("user", "assistant")]


def transcript_text(rows: Iterable[dict]) -> str:
    lines = []
    for row in rows:
        role = "User" if row.get("role") == "user" else "Assistant"
        content = " ".join(str(row.get("content") or "").split())
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)
