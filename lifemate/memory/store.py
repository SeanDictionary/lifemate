"""长期记忆存储（SQLite）：episodes / curiosity / needs / reminders / 重建包 / 图像。

记忆的持久性在这里，不在会话（架构铁律 6）。存储引擎可整体更换，
对外形状只经 memory.tools 暴露给心智。
schema 变更直接改 CREATE TABLE 删表重建（未上线，不迁移）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("memory.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'life',
    summary TEXT NOT NULL,
    salience REAL NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS episode_supersedes (
    old_id INTEGER PRIMARY KEY,
    new_id INTEGER NOT NULL,
    ts REAL NOT NULL
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
CREATE TABLE IF NOT EXISTS images (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);
"""


class Store:
    """单连接同步 SQLite（asyncio 单线程事件循环内使用，无需锁）。"""

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.row_factory = sqlite3.Row
        # WAL：读（replay/备份）与写并发不阻塞；进程崩溃自动恢复
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        # 强杀残留吸收：上次非正常退出留下的 wal 在打开时主动合并进主库
        # （崩溃一致性由 WAL 保证，这里避免工作文件无限期滞留）
        try:
            row = self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row and row[2] > 0:
                log.info("上次残留的 WAL 已合并（%d 帧）", row[2])
        except sqlite3.OperationalError:
            pass
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def backup(self, dest: Path | str) -> Path:
        """在线备份（sqlite backup API，写库不停）；返回备份文件路径。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(dest)
        try:
            self._con.backup(dst)
        finally:
            dst.close()
        return dest

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._con.execute(sql, params)
        self._con.commit()
        return cur

    # ── 近事流 episodes（含修正链：新认识顶替旧认识，防固化） ──
    def record_episode(self, summary: str, *, source: str = "", kind: str = "life", salience: float = 1.0) -> int:
        cur = self._exec("INSERT INTO episodes (ts, source, kind, summary, salience) VALUES (?, ?, ?, ?, ?)", (time.time(), source, kind, summary[:200], salience))
        return int(cur.lastrowid or 0)

    def episode_by_id(self, eid: int) -> dict[str, Any] | None:
        row = self._exec("SELECT id, ts, source, kind, summary FROM episodes WHERE id = ?", (eid,)).fetchone()
        return dict(row) if row else None

    def supersede_episode(self, old_id: int, new_id: int) -> None:
        """新认识顶替旧认识：旧条不再出现在回忆里（档案保全，不删除）。"""
        self._exec("INSERT OR REPLACE INTO episode_supersedes (old_id, new_id, ts) VALUES (?, ?, ?)", (old_id, new_id, time.time()))

    def revert_episode(self, new_id: int) -> int | None:
        """回滚一次修正：旧认识重新可见（修正记录保留为普通记忆）。返回旧条 id。"""
        row = self._exec("SELECT old_id FROM episode_supersedes WHERE new_id = ?", (new_id,)).fetchone()
        if row is None:
            return None
        self._exec("DELETE FROM episode_supersedes WHERE new_id = ?", (new_id,))
        return int(row["old_id"])

    def recent_episodes(self, n: int = 12, since_hours: float = 48.0, exclude_source: str = "") -> list[dict[str, Any]]:
        cutoff = time.time() - since_hours * 3600
        # 被顶替的旧认识不再出现在回忆里（带 id 供修正引用）
        base = "SELECT e.id, e.ts, e.source, e.kind, e.summary FROM episodes e WHERE e.ts > ? AND NOT EXISTS (SELECT 1 FROM episode_supersedes s WHERE s.old_id = e.id)"
        if exclude_source:
            rows = self._exec(base + " AND e.source != ? ORDER BY e.ts DESC LIMIT ?", (cutoff, exclude_source, n)).fetchall()
        else:
            rows = self._exec(base + " ORDER BY e.ts DESC LIMIT ?", (cutoff, n)).fetchall()
        return [dict(r) for r in rows]

    # ── 兴趣清单 curiosity（好奇/爱好/想深入了解，共用通道） ──
    def add_curiosity(self, topic: str, source: str = "") -> bool:
        """7 天内同话题去重（仅计还在清单上的：弄明白过的可重开/重深）。"""
        topic = topic.strip()[:120]
        if not topic:
            return False
        dup = self._exec("SELECT 1 FROM curiosity WHERE topic = ? AND ts > ? AND status = 'pending'", (topic, time.time() - 7 * 86400)).fetchone()
        if dup:
            return False
        self._exec("INSERT INTO curiosity (ts, topic, source) VALUES (?, ?, ?)", (time.time(), topic, source))
        return True

    def pending_curiosity(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec("SELECT id, topic, source, ts FROM curiosity WHERE status = 'pending' ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def all_curiosity(self, n: int = 50) -> list[dict[str, Any]]:
        """全量兴趣（面板用，含已弄明白的）：主人看它对什么感兴趣、弄明白了什么。"""
        rows = self._exec("SELECT id, topic, source, status, result, ts FROM curiosity ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def resolve_curiosity(self, cid: int, result: str = "") -> None:
        self._exec("UPDATE curiosity SET status = 'done', result = ? WHERE id = ?", (result[:800], cid))

    def reopen_curiosity(self, cid: int) -> bool:
        """重开一条已弄明白的兴趣（resolve 的回滚；原结果保留在案）。"""
        cur = self._exec("UPDATE curiosity SET status = 'pending' WHERE id = ? AND status = 'done'", (cid,))
        return cur.rowcount > 0

    # ── 需求队列 needs ──────────────────────────────────
    def add_need(self, what: str, why: str = "") -> bool:
        """7 天内同需求去重。"""
        what = what.strip()[:150]
        if not what:
            return False
        dup = self._exec("SELECT 1 FROM needs WHERE what = ? AND ts > ?", (what, time.time() - 7 * 86400)).fetchone()
        if dup:
            return False
        self._exec("INSERT INTO needs (ts, what, why) VALUES (?, ?, ?)", (time.time(), what, why[:300]))
        return True

    def pending_needs(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec("SELECT id, what, why, ts FROM needs WHERE status = 'filed' ORDER BY ts LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def all_needs(self, n: int = 50) -> list[dict[str, Any]]:
        """全量需求（面板用，含已提出）：主人要看它提过什么、兑现没有。"""
        rows = self._exec("SELECT id, what, why, status, raised_at, ts FROM needs ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def mark_need_raised(self, nid: int) -> None:
        self._exec("UPDATE needs SET status = 'raised', raised_at = ? WHERE id = ?", (time.time(), nid))

    # ── 自我提醒 reminders（存储；触发由调度器官驱动）─────
    def add_reminder(self, due_at: float, intent: str, repeat: str = "none") -> int:
        cur = self._exec("INSERT INTO reminders (ts, due_at, repeat, intent) VALUES (?, ?, ?, ?)", (time.time(), due_at, repeat, intent[:200]))
        return int(cur.lastrowid or 0)

    def pending_reminders(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec("SELECT id, due_at, repeat, intent FROM reminders WHERE status = 'pending' ORDER BY due_at LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def all_reminders(self, n: int = 50) -> list[dict[str, Any]]:
        """全量提醒（面板用，含已兑现）：主人看它给自己定过什么、兑现没有。"""
        rows = self._exec("SELECT id, intent, repeat, status, due_at, done_at, ts FROM reminders ORDER BY due_at DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def due_reminders(self, n: int = 5) -> list[dict[str, Any]]:
        """取出到期提醒（取出即标记，防重复触发；daily 类滚到明天同时刻）。"""
        now = time.time()
        out: list[dict[str, Any]] = []
        rows = self._exec("SELECT id, due_at, repeat, intent FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at LIMIT ?", (now, n)).fetchall()
        for r in rows:
            if r["repeat"] == "daily":
                due = float(r["due_at"])
                while due <= now:
                    due += 86400
                self._exec("UPDATE reminders SET due_at = ? WHERE id = ?", (due, r["id"]))
            else:
                self._exec("UPDATE reminders SET status = 'done', done_at = ? WHERE id = ?", (now, r["id"]))
            out.append(dict(r))
        return out

    # ── 重建包 rebuild_packets ──────────────────────────
    def save_packet(self, content: str) -> None:
        self._exec("INSERT INTO rebuild_packets (ts, content) VALUES (?, ?)", (time.time(), content))

    def latest_packet(self) -> dict[str, Any] | None:
        """最近一份重建包：{"content", "ts"}（写给新我的信 + 写下时刻）。"""
        row = self._exec("SELECT content, ts FROM rebuild_packets ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # ── 图像档案 images（感知记忆：看过的图及其内容）──
    def record_image(self, h: str, path: str, size: int, description: str = "") -> None:
        """登记/更新一张图（hash 主键）；description 空串不清盖已有描述。"""
        now = time.time()
        self._exec(
            "INSERT INTO images (hash, path, size, first_seen, last_seen, description) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(hash) DO UPDATE SET path=excluded.path, size=excluded.size, last_seen=excluded.last_seen, description=CASE WHEN excluded.description != '' THEN excluded.description ELSE images.description END",
            (h, path, size, now, now, description[:1500]),
        )

    def find_image(self, h: str) -> dict[str, Any] | None:
        row = self._exec("SELECT hash, path, size, first_seen, last_seen, hit_count, description FROM images WHERE hash = ?", (h,)).fetchone()
        return dict(row) if row else None

    def touch_image(self, h: str) -> None:
        """看过一次：刷新 last_seen、计数。"""
        self._exec("UPDATE images SET last_seen = ?, hit_count = hit_count + 1 WHERE hash = ?", (time.time(), h))

    def stale_images(self, before_ts: float) -> list[dict[str, Any]]:
        """超过 before_ts 未再看的图（保留期淘汰用）。"""
        rows = self._exec("SELECT hash, path FROM images WHERE last_seen < ?", (before_ts,)).fetchall()
        return [dict(r) for r in rows]

    def forget_image(self, h: str) -> None:
        self._exec("DELETE FROM images WHERE hash = ?", (h,))

    def recent_images(self, n: int = 50) -> list[dict[str, Any]]:
        """最近看过的图（面板/记忆浏览用，按最后查看时间倒序）。"""
        rows = self._exec("SELECT hash, size, first_seen, last_seen, hit_count, description FROM images ORDER BY last_seen DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    # ── 观测辅助 ────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for table in ("episodes", "curiosity", "needs", "reminders", "rebuild_packets", "images"):
            row = self._exec(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            out[table] = row["c"]
        return out


def rows_json(rows: list[dict[str, Any]]) -> str:
    """行列表 → 紧凑 JSON 回执文本。"""
    return json.dumps(rows, ensure_ascii=False)
