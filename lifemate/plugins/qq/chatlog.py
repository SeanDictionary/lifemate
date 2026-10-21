"""QQ 背景听觉：聊天记录存储 + 未读追踪（插件私有数据）。

弱刺激（普通群消息）不直达心智——落 chat_log，等它主动 read_chat_log
或下次唤醒时从身体感受里看到未读概览。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


class ChatLog:
    """单连接 sqlite（asyncio 单线程事件循环内使用）。"""

    def __init__(self, db_path: Path | str, max_per_chat: int = 500) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.row_factory = sqlite3.Row
        self._con.executescript("""
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    chat_id TEXT NOT NULL,
    sender_id INTEGER NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    message_id INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_log (chat_id, ts);
CREATE TABLE IF NOT EXISTS chat_state (
    chat_id TEXT PRIMARY KEY,
    last_read_ts REAL NOT NULL DEFAULT 0
);
""")
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.commit()
        self._max = max_per_chat

    def close(self) -> None:
        self._con.close()

    def append(self, chat_id: str, sender_id: int, sender_name: str,
               text: str, message_id: int = 0, ts: float | None = None) -> None:
        self._con.execute(
            "INSERT INTO chat_log (ts, chat_id, sender_id, sender_name, text,"
            " message_id) VALUES (?, ?, ?, ?, ?, ?)",
            (ts or time.time(), chat_id, sender_id, sender_name, text, message_id))
        # 容量裁剪（按会话）
        self._con.execute(
            "DELETE FROM chat_log WHERE chat_id = ? AND id NOT IN ("
            " SELECT id FROM chat_log WHERE chat_id = ?"
            " ORDER BY ts DESC LIMIT ?)", (chat_id, chat_id, self._max))
        self._con.commit()

    def recent(self, chat_id: str, n: int = 20) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT ts, sender_name, text FROM chat_log WHERE chat_id = ?"
            " ORDER BY ts ASC LIMIT ?", (chat_id, n)).fetchall()
        # 取最近 n 条后按时间正序呈现
        rows = rows[-n:] if len(rows) > n else rows
        return [dict(r) for r in rows]

    def mark_read(self, chat_id: str) -> None:
        self._con.execute(
            "INSERT INTO chat_state (chat_id, last_read_ts) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET last_read_ts = excluded.last_read_ts",
            (chat_id, time.time()))
        self._con.commit()

    def unread_summary(self, top: int = 3) -> str:
        """未读概览（身体感受用）：最近 24h 未读会话与条数。"""
        cutoff = time.time() - 86400
        rows = self._con.execute(
            "SELECT l.chat_id, COUNT(*) AS c FROM chat_log l"
            " LEFT JOIN chat_state s ON s.chat_id = l.chat_id"
            " WHERE l.ts > ? AND l.ts > COALESCE(s.last_read_ts, 0)"
            " GROUP BY l.chat_id ORDER BY c DESC LIMIT ?",
            (cutoff, top)).fetchall()
        if not rows:
            return ""
        parts = [f"{r['chat_id'].replace('group_', '群').replace('private_', '私聊')}"
                 f" {r['c']} 条" for r in rows]
        total = sum(r["c"] for r in rows)
        return f"（你有 {total} 条没看的消息：{'、'.join(parts)}。）"
