"""QQ 背景听觉：聊天记录存储（按日分库，无上限）+ 未读追踪。

- 不限条数；保留期可配（plugins.qq.chatlog_retention_days，0=永久，默认 0）
- 按天切分：data/plugins/qq/chatlog/YYYY-MM-DD.db，当天写完即封存（不可变）
- 查询从最新库往回穿透，凑够 N 条为止；未读统计精确到已读线
- 已读位置在常驻的 chat_state.db（不参与分库）
- 职责边界：chatlog 只记 QQ 侧行为（强弱刺激全量磁带）；
  observe 只记主体侧（它注意/想/做了什么）
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
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
"""


def _day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class ChatLog:
    """按日分库的聊天记录（asyncio 单线程事件循环内使用）。

    写入走当日库（WAL）；读取按库开只读连接、用完即关——
    日库在非当天后天然不可变，读并发安全。
    """

    def __init__(self, data_dir: Path | str,
                 retention_days: int = 0) -> None:
        data_dir = Path(data_dir)
        self._dir = data_dir / "chatlog"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._retention = max(int(retention_days), 0)  # 0=永久
        self._state = sqlite3.connect(data_dir / "chat_state.db")
        self._state.row_factory = sqlite3.Row
        self._state.executescript("""
CREATE TABLE IF NOT EXISTS chat_state (
    chat_id TEXT PRIMARY KEY,
    last_read_ts REAL NOT NULL DEFAULT 0
);
""")
        self._state.commit()
        self._current_day = ""
        self._current: sqlite3.Connection | None = None

    def close(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None
        self._state.close()

    # ── 分库管理 ───────────────────────────────────────
    def _db_for_day(self, day: str) -> sqlite3.Connection:
        if self._current is None or day != self._current_day:
            if self._current is not None:
                self._current.close()
            con = sqlite3.connect(self._dir / f"{day}.db")
            con.executescript(_SCHEMA)
            con.execute("PRAGMA journal_mode=WAL")
            con.commit()
            self._current = con
            self._current_day = day
            self._prune()  # 日切时机顺手清理超期档案
        return self._current

    def _prune(self) -> None:
        """删除超保留期的日库（retention=0 时不动）。"""
        if self._retention <= 0:
            return
        try:
            cutoff = time.strptime(
                time.strftime("%Y-%m-%d",
                              time.localtime(time.time()
                                              - self._retention * 86400)),
                "%Y-%m-%d")
        except ValueError:
            return
        for path in self._dir.glob("*.db"):
            try:
                day = time.strptime(path.stem, "%Y-%m-%d")
            except ValueError:
                continue  # 非日库文件不动
            if day < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _day_files(self) -> list[Path]:
        """全部日库，新→旧。"""
        return sorted(self._dir.glob("*.db"), reverse=True)

    @staticmethod
    def _open_read(path: Path) -> sqlite3.Connection:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        return con

    # ── 写入 ───────────────────────────────────────────
    def append(self, chat_id: str, sender_id: int, sender_name: str,
               text: str, message_id: int = 0, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        con = self._db_for_day(_day_of(ts))
        con.execute(
            "INSERT INTO chat_log (ts, chat_id, sender_id, sender_name, text,"
            " message_id) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, chat_id, sender_id, sender_name, text, message_id))
        con.commit()

    # ── 查询 ───────────────────────────────────────────
    def recent(self, chat_id: str, n: int = 20) -> list[dict[str, Any]]:
        """最近 n 条：从最新日库往回穿透，凑够为止，时间正序返回。"""
        out: list[dict[str, Any]] = []
        for path in self._day_files():
            if len(out) >= n:
                break
            con = self._open_read(path)
            try:
                need = n - len(out)
                rows = con.execute(
                    "SELECT ts, sender_name, text FROM chat_log"
                    " WHERE chat_id = ? ORDER BY ts DESC LIMIT ?",
                    (chat_id, need)).fetchall()
                out.extend(dict(r) for r in rows)
            finally:
                con.close()
        out.reverse()
        return out

    # ── 已读 ───────────────────────────────────────────
    def mark_read(self, chat_id: str) -> None:
        self._state.execute(
            "INSERT INTO chat_state (chat_id, last_read_ts) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET"
            " last_read_ts = excluded.last_read_ts",
            (chat_id, time.time()))
        self._state.commit()

    def unread_summary(self, top: int = 3) -> str:
        """未读概览（身体感受）：24h 窗口内 ts > last_read_ts 的精确统计。"""
        cutoff = time.time() - 86400
        read_map = {r["chat_id"]: r["last_read_ts"] for r in
                    self._state.execute(
                        "SELECT chat_id, last_read_ts FROM chat_state").fetchall()}
        counts: dict[str, int] = {}
        # 24h 窗口最多涉及最近两个日库
        for path in self._day_files()[:2]:
            con = self._open_read(path)
            try:
                rows = con.execute(
                    "SELECT chat_id, ts FROM chat_log WHERE ts > ?",
                    (cutoff,)).fetchall()
                for r in rows:
                    if r["ts"] > read_map.get(r["chat_id"], 0):
                        counts[r["chat_id"]] = counts.get(r["chat_id"], 0) + 1
            finally:
                con.close()
        if not counts:
            return ""
        top_items = sorted(counts.items(), key=lambda x: -x[1])[:top]
        parts = [f"{cid.replace('group_', '群').replace('private_', '私聊')} {c} 条"
                 for cid, c in top_items]
        return (f"（你有 {sum(counts.values())} 条没看的消息："
                f"{'、'.join(parts)}。）")
