"""观测记录器：项目级日志基础设施（黑匣子，常开不可拆）。

- 定位：不是器官——host 无条件挂载；拔掉任何查看器不丢档案
- 数据源：内核事件流（唯一来源）+ host 侧直接事件（boot/stop/config）
- 存储：{data_dir}/observe/YYYY-MM-DD.jsonl，按日切、只追加、永不改写
- 行设计：msg 行沿用 session 消息字段 + ts/seq/sid/rid/env/执行语义扩展；
  事件行只记消息表达不了的（rotate/compact/queue_drop/…）
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..kernel.contracts import ASSISTANT_MSG, COMPACTED, ERROR, PERSONA_CHANGED, QUEUE_DROP, REST, SESSION_ROTATED, TOOL_RESULT, USER_MSG, WAKE, MindEvent

log = logging.getLogger("utils.recorder")

_TRUNCATED = "…[truncated]"


class Recorder:
    """订阅内核事件流，写全程档案。"""

    def __init__(self, directory: Path | str, *, sid_fn: Callable[[], str] | None = None, max_field_chars: int = 65_536) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sid_fn = sid_fn
        self._max = max_field_chars
        self._seq = 0
        self._day = ""
        self._fh: Any = None
        # 实时分接：落档同一行（同形状同 seq）同步推给订阅方（如 server 的
        # /api/records SSE 流）。零 IO 约束同订阅回调——只接受非阻塞接收方。
        # tap 异常绝不影响落档（黑匣子优先）。
        self.tap: Callable[[dict[str, Any]], None] | None = None

    # ── 写入 ────────────────────────────────────────────
    def event(self, type_: str, **data: Any) -> None:
        """host 侧直接事件（boot/host_stop/config_reload/session_load/…）。"""
        self._write(type_, **data)

    def _write(self, type_: str, **fields: Any) -> None:
        self._seq += 1
        line: dict[str, Any] = {
            "type": type_,
            "ts": round(time.time(), 3),
            "seq": self._seq,
            "sid": self._sid_fn() if self._sid_fn else "",
        }
        for k, v in fields.items():
            line[k] = self._cap(v)
        if self.tap is not None:
            try:
                self.tap(line)
            except Exception:  # noqa: BLE001
                log.exception("观测分接异常（不影响落档）")
        try:
            fh = self._file()
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.flush()
        except OSError:
            log.exception("观测写入失败（type=%s）", type_)

    def _file(self):
        day = time.strftime("%Y-%m-%d")
        if self._fh is None or day != self._day:
            if self._fh is not None:
                self._fh.close()
            self._day = day
            self._fh = (self._dir / f"{day}.jsonl").open("a", encoding="utf-8")
        return self._fh

    def _cap(self, v: Any) -> Any:
        """递归截断超长字符串（审计要全，但单行不能撑爆日志）。"""
        if isinstance(v, str):
            return v if len(v) <= self._max else v[: self._max] + _TRUNCATED
        if isinstance(v, list):
            return [self._cap(x) for x in v]
        if isinstance(v, dict):
            return {k: self._cap(x) for k, x in v.items()}
        return v

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def cleanup(self, retention_days: int) -> int:
        """保留期清理：删除超期的日档（0=永久保留）。返回删除数。"""
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for f in self._dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    # ── 内核事件 → 档案行 ──────────────────────────────
    def on_kernel_event(self, ev: MindEvent) -> None:
        d = ev.data
        rid = d.get("rid", "")
        if ev.type == WAKE:
            self._write("wake", rid=rid, trigger=d.get("trigger"), kind=d.get("kind"), source=d.get("source"))
        elif ev.type == REST:
            self._write("rest", rid=rid, reason=d.get("reason"), timeout_s=d.get("timeout_s"))
        elif ev.type == USER_MSG:
            self._write("msg", rid=rid, role="user", content=d.get("text", ""), env=d.get("env", {}))
        elif ev.type == ASSISTANT_MSG:
            self._write("msg", rid=rid, role="assistant", content=d.get("content", ""), thinking=d.get("thinking", ""), tool_calls=d.get("tool_calls", []), model=d.get("model", ""), stop_reason=d.get("stop_reason", ""), prompt_tokens=d.get("prompt_tokens", 0), completion_tokens=d.get("completion_tokens", 0), latency_ms=d.get("latency_ms", 0))
        elif ev.type == TOOL_RESULT:
            self._write("msg", rid=rid, role="tool", tool_call_id=d.get("tool_call_id", ""), name=d.get("name", ""), ok=bool(d.get("ok")), latency_ms=d.get("latency_ms", 0), content=d.get("result", ""))
        elif ev.type == SESSION_ROTATED:
            self._write("rotate", old=d.get("old"), new=d.get("new"), seed_chars=d.get("seed_chars"))
        elif ev.type == COMPACTED:
            self._write("compact", rid=rid, dropped=d.get("dropped"), kept=d.get("kept"), summary_chars=d.get("summary_chars"), prompt_tokens=d.get("prompt_tokens", 0), completion_tokens=d.get("completion_tokens", 0))
        elif ev.type == QUEUE_DROP:
            self._write("queue_drop", reason=d.get("reason"), count=len(d.get("texts", [])), texts=d.get("texts", []))
        elif ev.type == PERSONA_CHANGED:
            self._write("persona_changed", old=d.get("old_hash"), new=d.get("new_hash"), chars=d.get("chars"))
        elif ev.type == ERROR:
            self._write("error", rid=rid, where=d.get("error", ""), error=str(d)[:500])
        # TOOL_CALL 事件不落行：执行语义已并入 TOOL_RESULT 行


# ── 读取侧（查看器共用：server 面板 / replay / recall 工具）─────────
# 布局属主在此，读取方不各抄一份解析（坏行跳过：半写状态不炸查看器）。


def available_dates(observe_dir: Path | str) -> list[str]:
    """有档案的日期列表（升序，YYYY-MM-DD）。"""
    d = Path(observe_dir)
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.jsonl"))


def read_day(observe_dir: Path | str, date: str, rid: str = "", limit: int = 500, offset: int = 0, tail: int = 0) -> dict[str, Any] | None:
    """读一天档案（可按 rid 前缀过滤、分页或取尾部 N 条）。无档案 → None。

    tail>0 时从尾部翻页：取过滤后的倒数第 offset+1..offset+tail 条
    （offset=0 即最后 N 条；历史页倒序展示用，新记录到达时窗口滑动）。
    """
    path = Path(observe_dir) / f"{date}.jsonl"
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in raw_lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue  # 坏行跳过（半写状态）
        if rid and not str(rec.get("rid") or "").startswith(rid):
            continue
        rows.append(rec)
    if tail:
        end = max(0, len(rows) - offset)
        start = max(0, end - tail)
        return {"total": len(rows), "rows": rows[start:end]}
    return {"total": len(rows), "rows": rows[offset : offset + limit]}


def search_archive(observe_dir: Path | str, q: str, max_hits: int = 100) -> list[dict[str, Any]]:
    """全档案关键词搜索：新→旧扫日档，行内大小写不敏感包含匹配。

    返回命中行（新→旧序），每行附 date 溯源；够 max_hits 即停。
    """
    hits: list[dict[str, Any]] = []
    needle = q.lower()
    for date in reversed(available_dates(observe_dir)):
        path = Path(observe_dir) / f"{date}.jsonl"
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        day_rows: list[dict[str, Any]] = []
        for raw in raw_lines:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if needle in raw.lower():
                day_rows.append(rec)
        for rec in reversed(day_rows):  # 天内也是新→旧
            hits.append({"date": date, "row": rec})
            if len(hits) >= max_hits:
                return hits
    return hits


def _oneline(s, n: int) -> str:
    """空白归一 + 截短（单行摘要用）。"""
    s = " ".join(str(s or "").split())
    return s[:n] + "…" if len(s) > n else s


def row_digest(row: dict[str, Any]) -> str:
    """档案行 → 单行摘要（图标 + 事实头，不含时间——时间标注由调用方拼）。

    单行摘要的公共语言（qq /history、内核 recall 共用）；富视图
    （思考全文/用量/链路分组）各自实现（replay 开发者视图等）。
    """
    typ, role = row.get("type"), row.get("role")
    if typ == "msg":
        if role == "assistant":
            tcs = row.get("tool_calls") or []
            if tcs:
                calls = ", ".join(f"{tc.get('name', '?')}({_oneline(str(tc.get('arguments', '')), 30)})" for tc in tcs[:3])
                extra = f" …共{len(tcs)}个" if len(tcs) > 3 else ""
                return f"🔧 {calls}{extra}"
            return "💬 " + _oneline(row.get("content"), 60)
        if role == "user":
            return "📨 " + _oneline(row.get("content"), 60)
        if role == "tool":
            return f"📥 {row.get('name', '?')} {'✓' if row.get('ok') else '✗'} " + _oneline(str(row.get("content")), 40)
        return f"{role} " + _oneline(row.get("content"), 40)
    if typ == "rest":
        return f"🌙 休息 {_oneline(row.get('reason'), 40)}"
    if typ == "wake":
        return f"☀️ 唤醒 {row.get('trigger', '')}"
    if typ == "error":
        return "✗ " + _oneline(row.get("error"), 60)
    if typ == "compact":
        return f"🗜️ 压缩 丢{row.get('dropped', 0)} 保{row.get('kept', 0)}"
    if typ == "rotate":
        return "🔄 会话轮换"
    if typ == "persona_changed":
        return f"🐟 人格变更 {row.get('old', '?')} → {row.get('new', '?')}"
    return f"· {typ}"
