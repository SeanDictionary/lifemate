"""长期记忆存储（SQLite）：episodes / curiosity / needs / reminders / 重建包。

记忆的持久性在这里，不在会话（架构铁律 6）。存储引擎可整体更换，
对外形状只经 memory.tools 暴露给心智。
schema 变更直接改 CREATE TABLE 删表重建（未上线，不迁移）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'life',
    summary TEXT NOT NULL,
    salience REAL NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS curiosity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    topic TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS needs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    what TEXT NOT NULL,
    why TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'filed',
    raised_at REAL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    due_at REAL NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none',
    intent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    done_at REAL
);
CREATE TABLE IF NOT EXISTS rebuild_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    content TEXT NOT NULL
);
"""


class Store:
    """单连接同步 SQLite（asyncio 单线程事件循环内使用，无需锁）。"""

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._con.execute(sql, params)
        self._con.commit()
        return cur

    # ── 近事流 episodes ─────────────────────────────────
    def record_episode(self, summary: str, *, source: str = "",
                       kind: str = "life", salience: float = 1.0) -> None:
        self._exec(
            "INSERT INTO episodes (ts, source, kind, summary, salience)"
            " VALUES (?, ?, ?, ?, ?)",
            (time.time(), source, kind, summary[:200], salience))

    def recent_episodes(self, n: int = 12, since_hours: float = 48.0,
                        exclude_source: str = "") -> list[dict[str, Any]]:
        cutoff = time.time() - since_hours * 3600
        if exclude_source:
            rows = self._exec(
                "SELECT ts, source, kind, summary FROM episodes"
                " WHERE ts > ? AND source != ? ORDER BY ts DESC LIMIT ?",
                (cutoff, exclude_source, n)).fetchall()
        else:
            rows = self._exec(
                "SELECT ts, source, kind, summary FROM episodes"
                " WHERE ts > ? ORDER BY ts DESC LIMIT ?",
                (cutoff, n)).fetchall()
        return [dict(r) for r in rows]

    # ── 好奇队列 curiosity ───────────────────────────────
    def add_curiosity(self, topic: str, source: str = "") -> bool:
        """7 天内同话题去重。"""
        topic = topic.strip()[:120]
        if not topic:
            return False
        dup = self._exec(
            "SELECT 1 FROM curiosity WHERE topic = ? AND ts > ?",
            (topic, time.time() - 7 * 86400)).fetchone()
        if dup:
            return False
        self._exec(
            "INSERT INTO curiosity (ts, topic, source) VALUES (?, ?, ?)",
            (time.time(), topic, source))
        return True

    def pending_curiosity(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, topic, source FROM curiosity"
            " WHERE status = 'pending' ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def resolve_curiosity(self, cid: int, result: str = "") -> None:
        self._exec(
            "UPDATE curiosity SET status = 'done', result = ? WHERE id = ?",
            (result[:800], cid))

    # ── 需求队列 needs ──────────────────────────────────
    def add_need(self, what: str, why: str = "") -> bool:
        """7 天内同需求去重。"""
        what = what.strip()[:150]
        if not what:
            return False
        dup = self._exec(
            "SELECT 1 FROM needs WHERE what = ? AND ts > ?",
            (what, time.time() - 7 * 86400)).fetchone()
        if dup:
            return False
        self._exec("INSERT INTO needs (ts, what, why) VALUES (?, ?, ?)",
                   (time.time(), what, why[:300]))
        return True

    def pending_needs(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, what, why FROM needs WHERE status = 'filed'"
            " ORDER BY ts LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def mark_need_raised(self, nid: int) -> None:
        self._exec("UPDATE needs SET status = 'raised', raised_at = ? WHERE id = ?",
                   (time.time(), nid))

    # ── 自我提醒 reminders（存储；触发由调度器官驱动）─────
    def add_reminder(self, due_at: float, intent: str,
                     repeat: str = "none") -> int:
        cur = self._exec(
            "INSERT INTO reminders (ts, due_at, repeat, intent)"
            " VALUES (?, ?, ?, ?)",
            (time.time(), due_at, repeat, intent[:200]))
        return int(cur.lastrowid or 0)

    def pending_reminders(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, due_at, repeat, intent FROM reminders"
            " WHERE status = 'pending' ORDER BY due_at LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def due_reminders(self, n: int = 5) -> list[dict[str, Any]]:
        """取出到期提醒（取出即标记，防重复触发；daily 类滚到明天同时刻）。"""
        now = time.time()
        out: list[dict[str, Any]] = []
        rows = self._exec(
            "SELECT id, due_at, repeat, intent FROM reminders"
            " WHERE status = 'pending' AND due_at <= ?"
            " ORDER BY due_at LIMIT ?", (now, n)).fetchall()
        for r in rows:
            if r["repeat"] == "daily":
                due = float(r["due_at"])
                while due <= now:
                    due += 86400
                self._exec("UPDATE reminders SET due_at = ? WHERE id = ?",
                           (due, r["id"]))
            else:
                self._exec(
                    "UPDATE reminders SET status = 'done', done_at = ?"
                    " WHERE id = ?", (now, r["id"]))
            out.append(dict(r))
        return out

    # ── 重建包 rebuild_packets ──────────────────────────
    def save_packet(self, content: str) -> None:
        self._exec("INSERT INTO rebuild_packets (ts, content) VALUES (?, ?)",
                   (time.time(), content))

    def latest_packet(self) -> str | None:
        row = self._exec(
            "SELECT content FROM rebuild_packets ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return row["content"] if row else None

    # ── 观测辅助 ────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for table in ("episodes", "curiosity", "needs", "reminders",
                      "rebuild_packets"):
            row = self._exec(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            out[table] = row["c"]
        return out


def rows_json(rows: list[dict[str, Any]]) -> str:
    """行列表 → 紧凑 JSON 回执文本。"""
    return json.dumps(rows, ensure_ascii=False)
