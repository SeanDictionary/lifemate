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
from pathlib import Path
from typing import Any, Callable

from ..kernel.contracts import (ASSISTANT_MSG, COMPACTED, ERROR, MindEvent,
                                QUEUE_DROP, RUN_ENDED, RUN_STARTED,
                                SESSION_ROTATED, TOOL_RESULT, USER_MSG)

log = logging.getLogger("utils.recorder")

_TRUNCATED = "…[truncated]"


class Recorder:
    """订阅内核事件流，写全程档案。"""

    def __init__(self, directory: Path | str, *,
                 sid_fn: Callable[[], str] | None = None,
                 max_field_chars: int = 65_536) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sid_fn = sid_fn
        self._max = max_field_chars
        self._seq = 0
        self._day = ""
        self._fh: Any = None

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

    # ── 内核事件 → 档案行 ──────────────────────────────
    def on_kernel_event(self, ev: MindEvent) -> None:
        d = ev.data
        rid = d.get("rid", "")
        if ev.type == RUN_STARTED:
            self._write("run", rid=rid, kind=d.get("kind"),
                        source=d.get("source"))
        elif ev.type == USER_MSG:
            self._write("msg", rid=rid, role="user",
                        content=d.get("text", ""), env=d.get("env", {}))
        elif ev.type == ASSISTANT_MSG:
            self._write("msg", rid=rid, role="assistant",
                        content=d.get("content", ""),
                        thinking=d.get("thinking", ""),
                        tool_calls=d.get("tool_calls", []),
                        model=d.get("model", ""),
                        stop_reason=d.get("stop_reason", ""),
                        prompt_tokens=d.get("prompt_tokens", 0),
                        completion_tokens=d.get("completion_tokens", 0),
                        latency_ms=d.get("latency_ms", 0))
        elif ev.type == TOOL_RESULT:
            self._write("msg", rid=rid, role="tool",
                        tool_call_id=d.get("tool_call_id", ""),
                        name=d.get("name", ""),
                        ok=bool(d.get("ok")),
                        latency_ms=d.get("latency_ms", 0),
                        content=d.get("result", ""))
        elif ev.type == RUN_ENDED:
            self._write("run_end", rid=rid, reason=d.get("reason", "stop"))
        elif ev.type == SESSION_ROTATED:
            self._write("rotate", old=d.get("old"), new=d.get("new"),
                        seed_chars=d.get("seed_chars"))
        elif ev.type == COMPACTED:
            self._write("compact", rid=rid,
                        dropped=d.get("dropped"), kept=d.get("kept"),
                        summary_chars=d.get("summary_chars"),
                        prompt_tokens=d.get("prompt_tokens", 0),
                        completion_tokens=d.get("completion_tokens", 0))
        elif ev.type == QUEUE_DROP:
            self._write("queue_drop", reason=d.get("reason"),
                        count=len(d.get("texts", [])),
                        texts=d.get("texts", []))
        elif ev.type == ERROR:
            self._write("error", rid=rid, where=d.get("error", ""),
                        error=str(d)[:500])
        # TOOL_CALL 事件不落行：执行语义已并入 TOOL_RESULT 行
