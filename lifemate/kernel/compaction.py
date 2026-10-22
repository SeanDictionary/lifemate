"""上下文压缩：会话内兜底（轮换才是常态，压缩不应频繁触发）。

借签 pi 的 compaction 机制，简化为两步：
- 判定：上次模型调用的 prompt_tokens 超阈值（无用量数据时退化为字符估算）
- 压缩：从尾部保留最近 keep_recent 估算 token，其余连同旧摘要交给模型
  生成滚动摘要，整体提交回会话
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from .contracts import LLMResponse
from .session import Session, _MSG_OVERHEAD, estimate_tokens
from ..utils import clock

log = logging.getLogger("kernel.compaction")

_COMPACT_PROMPT = (
    "把以下对话历史压缩成一份摘要（保留：聊过的话题、对方的偏好与事实、"
    "未完成的事项、你的状态与打算；不超400字）。"
    "旧摘要仍有效的要点可以保留。\n\n旧摘要：\n{old}\n\n对话历史：\n{transcript}"
)


class _ChatFn(Protocol):
    async def __call__(
        self, messages: list[dict[str, Any]], tools: list | None = None,
    ) -> LLMResponse: ...


class Compactor:
    """阈值从活配置读取（热重载即时生效）。"""

    def __init__(self, chat: _ChatFn, cfg) -> None:
        self._chat = chat
        self._cfg = cfg

    def needs_compaction(self, session: Session,
                         last_prompt_tokens: int) -> bool:
        if last_prompt_tokens > 0:
            return last_prompt_tokens > self._cfg.compact_threshold_tokens
        return session.total_tokens() > self._cfg.compact_threshold_tokens

    async def compact(self, session: Session) -> dict | None:
        """执行压缩。返回压缩信息（失败/未触发返回 None，不阻塞思维循环）。"""
        cut = self._find_cut(session.messages, self._cfg.keep_recent_tokens)
        if cut <= 0:
            return None
        dropped = session.messages[:cut]
        kept = session.messages[cut:]
        transcript = "\n".join(
            f"[{m.get('role')}]: {str(m.get('content') or '')[:400]}" for m in dropped)
        try:
            resp = await self._chat(messages=[{
                "role": "user",
                "content": _COMPACT_PROMPT.format(
                    old=session.summary or "（无）", transcript=transcript),
            }])
            summary = (resp.content or "").strip()
            if not summary:
                return None
        except Exception:  # noqa: BLE001
            log.exception("压缩调用失败（跳过本轮）")
            return None
        span = self._span_of(dropped)
        session.apply_compaction(summary, kept, span=span)
        log.info("会话压缩：丢弃 %d 条，保留 %d 条，摘要 %d 字（%s）",
                 len(dropped), len(kept), len(summary), span or "无时间范围")
        return {
            "dropped": len(dropped), "kept": len(kept),
            "summary_chars": len(summary),
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
        }

    def _span_of(self, dropped: list[dict[str, Any]]) -> str:
        """被压缩部分的时间范围（首尾消息 ts，主体时区，人读）。"""
        stamps = [m["ts"] for m in dropped if m.get("ts")]
        if not stamps:
            return ""  # 旧格式消息无 ts：不标注，不臆造
        tz = self._cfg.timezone
        a, b = (clock.datetime_at(stamps[0], tz),
                clock.datetime_at(stamps[-1], tz))
        fmt = "%Y-%m-%d %H:%M" if (a.year != b.year
                                    or (b - a).days > 180) else "%m-%d %H:%M"
        return f"{a.strftime(fmt)} 至 {b.strftime(fmt)}"

    @staticmethod
    def _find_cut(messages: list[dict[str, Any]],
                  keep_recent_tokens: int) -> int:
        """从尾部累计 keep_recent token，返回截断点（不切在工具响应中间）。"""
        acc = 0
        cut = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            acc += _MSG_OVERHEAD + estimate_tokens(str(m.get("content") or ""))
            if acc > keep_recent_tokens:
                cut = i + 1
                break
        # 截断点若悬在 tool 响应上，向后推过整段 tool 消息
        # （其对应的 assistant tool_calls 消息在截断点之前，不可拆散）
        # 向前推进到不悬空 tool 消息的位置
        while cut < len(messages) and messages[cut].get("role") == "tool":
            cut += 1
        return cut if cut < len(messages) else 0
