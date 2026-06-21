"""
Persistent conversation store (Phase 1a).
==========================================
SQLite-backed history so chats survive restarts: save / list / resume / branch /
rename / delete. Stdlib only — the DB file is created on first use.

Schema
  conversations(id, title, created_at, updated_at)
  turns(id, conversation_id, seq, question, digest, answer, source, created_at)

`source` = 'sql' | 'graph' so the UI can tell which engine answered.
"""
from __future__ import annotations
import os
import sqlite3
import datetime as dt
from dataclasses import dataclass

DEFAULT_DB = os.environ.get(
    "LBS_CHAT_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chat_history.db"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    question        TEXT NOT NULL,
    digest          TEXT NOT NULL,
    answer          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'sql',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_turns_conv ON turns(conversation_id, seq);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class ConversationInfo:
    id: int
    title: str
    created_at: str
    updated_at: str
    turn_count: int


class ChatStore:
    def __init__(self, path: str = DEFAULT_DB):
        self.path = path
        self.cx = sqlite3.connect(path)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA foreign_keys = ON")
        self.cx.executescript(_SCHEMA)
        self.cx.commit()

    # --- conversations ---------------------------------------------------- #
    def create(self, title: str = "Untitled") -> int:
        ts = _now()
        cur = self.cx.execute(
            "INSERT INTO conversations(title, created_at, updated_at) VALUES (?,?,?)",
            (title, ts, ts))
        self.cx.commit()
        return cur.lastrowid

    def list(self) -> list[ConversationInfo]:
        rows = self.cx.execute("""
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = c.id) AS n
            FROM conversations c ORDER BY c.updated_at DESC""").fetchall()
        return [ConversationInfo(r["id"], r["title"], r["created_at"],
                                 r["updated_at"], r["n"]) for r in rows]

    def exists(self, conv_id: int) -> bool:
        return self.cx.execute("SELECT 1 FROM conversations WHERE id=?",
                               (conv_id,)).fetchone() is not None

    def rename(self, conv_id: int, title: str):
        self.cx.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                        (title, _now(), conv_id))
        self.cx.commit()

    def delete(self, conv_id: int):
        self.cx.execute("DELETE FROM turns WHERE conversation_id=?", (conv_id,))
        self.cx.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        self.cx.commit()

    # --- turns ------------------------------------------------------------ #
    def add_turn(self, conv_id: int, question: str, digest: str, answer: str,
                 source: str = "sql"):
        seq = (self.cx.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM turns WHERE conversation_id=?",
            (conv_id,)).fetchone()[0])
        self.cx.execute(
            "INSERT INTO turns(conversation_id, seq, question, digest, answer, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (conv_id, seq, question, digest, answer, source, _now()))
        self.cx.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                        (_now(), conv_id))
        self.cx.commit()

    def turns(self, conv_id: int) -> list[dict]:
        rows = self.cx.execute(
            "SELECT seq, question, digest, answer, source, created_at "
            "FROM turns WHERE conversation_id=? ORDER BY seq", (conv_id,)).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.cx.close()
