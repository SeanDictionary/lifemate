"""工作记忆：消息列表 + JSONL 持久化 + 会话轮换。

会话是可抛弃的工作记忆（借签 pi 的 JSONL 会话格式，简化为线性追加）：
- 每个会话一个 .jsonl 文件：header 行 + msg 行 + summary 行
- 压缩时整体重写（保留 header/summary/最近消息；压缩是低频事件）
- 轮换 = 关闭当前文件、开新文件；旧文件保留（观测/回放用）
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

HEADER_TYPE = "header"
MSG_TYPE = "msg"
SUMMARY_TYPE = "summary"

# 每条消息的粗略 token 估算：内容字符数的一半 + 固定开销（中文≈1.5字/token 的折中）
_CHARS_PER_TOKEN = 2
_MSG_OVERHEAD = 8


def estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _cleanup_rotated(directory: Path, keep: int) -> None:
    """轮换后清理旧会话文件：保留最近 keep 个（含当前），更旧的删除。

    档案职责已移交 observe；旧会话文件只为崩溃回退留少量余量。
    """
    files = sorted(directory.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in files[1 + max(keep, 0) :]:  # [0] 是当前会话
        try:
            old.unlink()
        except OSError:
            pass


class Session:
    """一个会话实例（思维循环的当前工作记忆）。"""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.session_id = file.stem.split("_", 1)[-1]
        self.created = time.time()
        self.summary = ""
        self.span = ""  # 摘要覆盖的时间范围（人读，如"10-20 09:00 至 10-21 15:30"）
        self.messages: list[dict[str, Any]] = []
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "type": HEADER_TYPE,
                        "version": 1,
                        "session_id": self.session_id,
                        "created": self.created,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # ── 写入 ─────────────────────────────────────────────
    def append(self, msg: dict[str, Any]) -> None:
        """追加一条消息（内存 + 落盘）。"""
        self.messages.append(msg)
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": MSG_TYPE, "msg": msg}, ensure_ascii=False) + "\n")

    def apply_compaction(self, summary: str, kept: list[dict[str, Any]], span: str = "") -> None:
        """压缩提交：更新摘要、截断消息，整体重写文件。"""
        self.summary = summary
        self.span = span
        self.messages = kept
        lines = [
            {
                "type": HEADER_TYPE,
                "version": 1,
                "session_id": self.session_id,
                "created": self.created,
            },
            {"type": SUMMARY_TYPE, "content": summary, "span": span},
        ]
        for m in kept:
            lines.append({"type": MSG_TYPE, "msg": m})
        tmp = self.file.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        tmp.replace(self.file)

    # ── 读取 ─────────────────────────────────────────────
    def build_request(self) -> list[dict[str, Any]]:
        """构建发给模型的完整消息序列（人格由思维循环拼在最前）。

        消息内部的 ts（时间戳，给持久化/时间感用）不进 API——
        OpenAI 协议不接受额外字段。"""
        out: list[dict[str, Any]] = []
        if self.summary:
            span = f"（{self.span}）" if self.span else ""
            out.append({"role": "system", "content": f"[此前会话的摘要{span}]\n{self.summary}"})
        out.extend({k: v for k, v in m.items() if k != "ts"} for m in self.messages)
        return out

    def total_tokens(self) -> int:
        """粗略估算当前上下文 token（含摘要）。"""
        n = estimate_tokens(self.summary)
        for m in self.messages:
            n += _MSG_OVERHEAD
            n += estimate_tokens(str(m.get("content") or ""))
            for tc in m.get("tool_calls") or []:
                n += estimate_tokens(str(tc.get("function", {}).get("arguments", "")))
        return n

    # ── 轮换 ─────────────────────────────────────────────
    def rotate(self, keep: int = 2) -> Session:
        """开新会话（旧文件按 keep 清理——档案职责在 observe），返回新实例。"""
        new = Session.create(self.file.parent)
        _cleanup_rotated(self.file.parent, keep=keep)
        return new

    # ── 工厂 ─────────────────────────────────────────────
    @classmethod
    def create(cls, directory: Path) -> Session:
        directory.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        return cls(directory / f"{ts}_{uuid.uuid4().hex[:8]}.jsonl")

    @classmethod
    def load_latest(cls, directory: Path) -> Session:
        """恢复最近一个会话（7×24 重启连续性）；无则新建。"""
        if not directory.exists():
            return cls.create(directory)
        files = sorted((f for f in directory.glob("*.jsonl") if f.stat().st_size > 0), key=lambda f: f.stat().st_mtime)
        if not files:
            return cls.create(directory)
        sess = cls.__new__(cls)
        sess.file = files[-1]
        sess.session_id = sess.file.stem.split("_", 1)[-1]
        sess.summary = ""
        sess.span = ""
        sess.messages = []
        created_seen = 0.0
        with sess.file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue  # 坏行跳过（半写状态）
                t = entry.get("type")
                if t == HEADER_TYPE:
                    created_seen = float(entry.get("created") or 0)
                elif t == SUMMARY_TYPE:
                    sess.summary = str(entry.get("content") or "")
                    sess.span = str(entry.get("span") or "")
                elif t == MSG_TYPE:
                    sess.messages.append(entry.get("msg") or {})
        sess.created = created_seen or sess.file.stat().st_mtime
        return sess
