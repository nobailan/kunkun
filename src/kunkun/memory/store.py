"""FTS5 消息存储 — SQLite 全文搜索跨会话历史.

借鉴 Hermes hermes_state.py (SQLite + FTS5):
- messages 表: 存储每条消息
- messages_fts: FTS5 虚拟表, 支持中文全文搜索
- sessions 表: 会话元数据

零外部依赖, 使用 Python 内置 sqlite3.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    ended_at TEXT NOT NULL DEFAULT '',
    turns INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    turn_number INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tokenize='unicode61'
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
"""


class MessageStore:
    """SQLite + FTS5 消息存储.

    Usage:
        store = MessageStore(".kun/messages.db")
        store.save_session("abc", "分析架构", "deepseek-v4-pro")
        store.save_message("abc", "user", "帮我分析", 0)
        results = store.search("Docker 部署")
    """

    def __init__(self, db_path: str = ".kun/messages.db"):
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    # ─── Session ────────────────────────────────────

    def save_session(
        self,
        session_id: str,
        prompt: str = "",
        model: str = "",
        turns: int = 0,
        total_tokens: int = 0,
        tool_calls: int = 0,
    ) -> None:
        """保存/更新会话元数据."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO sessions (id, prompt, model, started_at, ended_at, turns, total_tokens, tool_calls)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, prompt[:500], model,
                datetime.now().isoformat(), datetime.now().isoformat(),
                turns, total_tokens, tool_calls,
            ),
        )
        conn.commit()

    def update_session_end(
        self, session_id: str, turns: int = 0, total_tokens: int = 0, tool_calls: int = 0,
    ) -> None:
        """会话结束时更新统计."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE sessions SET ended_at=?, turns=?, total_tokens=?, tool_calls=? WHERE id=?",
            (datetime.now().isoformat(), turns, total_tokens, tool_calls, session_id),
        )
        conn.commit()

    # ─── Message ────────────────────────────────────

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        turn_number: int = 0,
        timestamp: str = "",
    ) -> None:
        """保存单条消息."""
        conn = self._get_conn()
        ts = timestamp or datetime.now().isoformat()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, turn_number) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content[:4000], ts, turn_number),
        )
        # 同步 FTS5
        conn.execute(
            "INSERT INTO messages_fts (rowid, content) VALUES (last_insert_rowid(), ?)",
            (content[:4000],),
        )
        conn.commit()

    def save_messages_batch(self, messages: list[dict]) -> None:
        """批量保存消息."""
        conn = self._get_conn()
        for msg in messages:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, turn_number) VALUES (?, ?, ?, ?, ?)",
                (
                    msg["session_id"], msg["role"], msg["content"][:4000],
                    msg.get("timestamp", ""), msg.get("turn_number", 0),
                ),
            )
            conn.execute(
                "INSERT INTO messages_fts (rowid, content) VALUES (last_insert_rowid(), ?)",
                (msg["content"][:4000],),
            )
        conn.commit()

    # ─── Search ─────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        session_id: str = "",
    ) -> list[dict]:
        """FTS5 全文搜索.

        Args:
            query: 搜索关键词
            limit: 返回条数上限
            session_id: 可选, 限定某个会话

        Returns:
            [{session_id, role, content, timestamp, turn_number}, ...]
        """
        conn = self._get_conn()
        # FTS5 查询: 用双引号包裹短语, 支持 OR
        terms = " OR ".join(f'"{w}"' for w in query.split() if len(w) >= 2)
        if not terms:
            terms = f'"{query}"'

        try:
            rows = conn.execute(
                f"""SELECT m.session_id, m.role, m.content, m.timestamp, m.turn_number,
                           s.prompt
                    FROM messages_fts f
                    JOIN messages m ON f.rowid = m.id
                    LEFT JOIN sessions s ON m.session_id = s.id
                    WHERE messages_fts MATCH ?
                    {'AND m.session_id = ?' if session_id else ''}
                    ORDER BY rank
                    LIMIT ?""",
                (terms, session_id, limit) if session_id else (terms, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 查询语法错误 → 退化为 LIKE 搜索
            like_query = f"%{query}%"
            rows = conn.execute(
                """SELECT m.session_id, m.role, m.content, m.timestamp, m.turn_number, s.prompt
                   FROM messages m
                   LEFT JOIN sessions s ON m.session_id = s.id
                   WHERE m.content LIKE ?
                   ORDER BY m.timestamp DESC
                   LIMIT ?""",
                (like_query, limit),
            ).fetchall()

        return [
            {
                "session_id": r[0],
                "role": r[1],
                "content": r[2][:500],
                "timestamp": r[3],
                "turn_number": r[4],
                "prompt": r[5] or "",
            }
            for r in rows
        ]

    def search_sessions(self, query: str, limit: int = 10) -> list[dict]:
        """搜索会话 (按 prompt 匹配)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, prompt, started_at, turns, total_tokens FROM sessions WHERE prompt LIKE ? ORDER BY started_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [
            {"id": r[0], "prompt": r[1], "started_at": r[2], "turns": r[3], "total_tokens": r[4]}
            for r in rows
        ]

    # ─── List ───────────────────────────────────────

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """列出最近的会话."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, prompt, started_at, turns, total_tokens, tool_calls FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0], "prompt": r[1][:200], "started_at": r[2],
                "turns": r[3], "total_tokens": r[4], "tool_calls": r[5],
            }
            for r in rows
        ]

    def session_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        """获取指定会话的消息."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT role, content, timestamp, turn_number FROM messages WHERE session_id=? ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            {"role": r[0], "content": r[1][:500], "timestamp": r[2], "turn_number": r[3]}
            for r in rows
        ]

    # ─── Stats ──────────────────────────────────────

    @property
    def stats(self) -> dict:
        conn = self._get_conn()
        total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"total_sessions": total_sessions, "total_messages": total_messages}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
