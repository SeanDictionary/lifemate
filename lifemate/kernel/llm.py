"""模型调用唯一入口（OpenAI 兼容 Chat Completions）。

内核内部表示（LLMResponse/ToolCall）与具体 API 方言解耦：
方言翻译（thinking 剥离等）收在本文件，思维循环不感知。
健壮性（借签 pi 自动重试）：瞬态错误指数退避重试，永久错误直抛，
连续失败达阈值触发告警回调（宿主/器官订阅）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .contracts import LLMResponse, ToolCall, ToolSchema

log = logging.getLogger("kernel.llm")

# 瞬态（可重试）异常类名（openai 未装时按名匹配；装了则 isinstance 优先）
_RETRYABLE_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "TimeoutError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "ReadTimeout",
    "RemoteProtocolError",
    "BadGatewayBody",
}


class BadGatewayBody(Exception):
    """网关 HTTP 200 但响应体非标准（如 new-api 把上游错误包成字符串体）。

    按瞬态错误处理：退避重试而非击穿思维循环。"""


def is_retryable(exc: BaseException) -> bool:
    """瞬态错误判定：连接/超时/限流/5xx 可重试；鉴权/参数/上下文溢出直抛。"""
    try:
        import openai

        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError)):
            return True
        if isinstance(exc, openai.APIStatusError):
            return exc.status_code >= 500
        if isinstance(exc, (openai.AuthenticationError, openai.BadRequestError, openai.NotFoundError, openai.PermissionDeniedError)):
            return False
    except ImportError:
        pass
    return type(exc).__name__ in _RETRYABLE_NAMES


async def chat_with_retry(
    fn: Callable[[], Awaitable[LLMResponse]],
    *,
    max_retries: int,
    backoff_s: float,
    fail_streak: _FailStreak | None = None,
) -> LLMResponse:
    """带重试的一次调用：瞬态错误指数退避重试，永久错误直抛。

    fail_streak 用于连续失败告警（成功即清零，阈值触发回调）。
    """
    last: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await fn()
            if fail_streak is not None:
                fail_streak.reset()
            return resp
        except Exception as e:  # noqa: BLE001
            last = e
            if not is_retryable(e) or attempt >= max_retries:
                if fail_streak is not None:
                    fail_streak.record(str(e))
                raise
            delay = backoff_s * (2**attempt)
            log.warning("LLM 调用失败（第 %d 次：%s），%.1fs 后重试", attempt + 1, e, delay)
            await asyncio.sleep(delay)
    raise last  # pragma: no cover（循环内必 raise/return）


class _FailStreak:
    """连续失败计数与告警（借签 v1：第 N 次连续失败触发一次回调）。"""

    def __init__(self, threshold: int, on_alert: Callable[[int, str], None] | None = None) -> None:
        self._threshold = threshold
        self._on_alert = on_alert
        self.count = 0
        self._alerted = False
        self.last = ""  # 最近一次失败原因（告警携带，档案可排查）

    def reset(self) -> None:
        self.count = 0
        self._alerted = False

    def record(self, reason: str) -> None:
        self.count += 1
        self.last = reason[:300]
        if self.count >= self._threshold:
            log.critical("LLM 连续失败 %d 次（最近：%s）——自主性已受影响", self.count, reason[:200])
            if not self._alerted:
                self._alerted = True
                if self._on_alert is not None:
                    try:
                        self._on_alert(self.count, self.last)
                    except Exception:  # noqa: BLE001
                        log.exception("LLM 失败告警回调异常")


class LLMClient(Protocol):
    """思维循环依赖的最小接口（测试可注入 FakeLLM）。"""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse: ...


class OpenAILLM:
    """OpenAI 兼容客户端：持有活配置，签名变更时热重建连接。"""

    def __init__(self, cfg, on_persistent_failure: Callable[[int], None] | None = None) -> None:
        self._cfg = cfg
        self._client = None
        self._sig: tuple[str, str] | None = None
        self._fail_streak = _FailStreak(cfg.llm_fail_alert_streak, on_persistent_failure)

    def _get_client(self):
        sig = (self._cfg.llm_base_url, self._cfg.llm_api_key)
        if self._client is None or sig != self._sig:
            from openai import AsyncOpenAI  # 惰性导入，测试环境零依赖

            self._client = AsyncOpenAI(base_url=self._cfg.llm_base_url or "https://api.openai.com/v1", api_key=self._cfg.llm_api_key or "sk-unconfigured", timeout=300.0)
            self._sig = sig
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        return await chat_with_retry(lambda: self._raw_chat(messages, tools), max_retries=self._cfg.llm_max_retries, backoff_s=self._cfg.llm_retry_backoff_s, fail_streak=self._fail_streak)

    async def vision(self, text: str, image_b64: str, media_type: str = "image/jpeg") -> LLMResponse:
        """看图（多模态感知）：文本提问 + 图像 base64 → 回答。

        要求 llm_vision_model 已配置（未配置直抛明确错误——look_image
        在入口已门控，此处是兑底防线）；纯感知调用不带工具面（不是思考轮）。
        供 look_image 工具使用，思维循环本身不调。
        """
        model = str(self._cfg.llm_vision_model or "").strip()
        if not model:
            raise ValueError("llm_vision_model 未配置（看图能力关闭）")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                ],
            }
        ]
        return await chat_with_retry(lambda: self._raw_chat(messages, None, model=model), max_retries=self._cfg.llm_max_retries, backoff_s=self._cfg.llm_retry_backoff_s, fail_streak=self._fail_streak)

    async def _raw_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if tools:
            # MCP 形状 → OpenAI function 形状（唯一方言翻译点：
            # name/description/inputSchema → name/description/parameters）
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {"type": "object"}),
                    },
                }
                for t in tools
            ]
        t0 = time.monotonic()
        resp = await self._get_client().chat.completions.create(model=model or self._cfg.llm_model, messages=messages, **kwargs)
        if not getattr(resp, "choices", None):
            # 网关 200+脏响应体（实测：new-api 中转把上游错误包成字符串体，
            # SDK 解析成 str）——按瞬态错误重试，不让它击穿思维循环
            raise BadGatewayBody(f"网关返回非标准响应体：{str(resp)[:120]}")
        choice = resp.choices[0]
        msg = choice.message
        thinking, content = _extract_thinking(msg)
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=_normalize_args(tc.function.arguments)))
        u = resp.usage
        return LLMResponse(
            content=content,
            thinking=thinking,
            tool_calls=calls,
            stop_reason=_map_stop(choice.finish_reason),
            model=getattr(resp, "model", "") or "",
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            completion_tokens=getattr(u, "completion_tokens", 0) if u else 0,
        )


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
_STOP_MAP = {"tool_calls": "toolUse", "function_call": "toolUse", "stop": "stop", "length": "length"}


def _normalize_args(raw: object) -> str:
    """方言兼容：有的网关返回 dict，有的返回字符串，有的返回空。"""
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    if isinstance(raw, str) and raw.strip():
        try:
            json.loads(raw)
            return raw
        except ValueError:
            return "{}"  # 坏 JSON 留给执行层兜底回执
    return "{}"


def _extract_thinking(msg: Any) -> tuple[str, str]:
    """三来源归一化（借签 pi）：reasoning_content / reasoning / <think>。

    思考从正文剥离——上下文只要干净正文，thinking 仅供观测档案。
    """
    t = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    c = msg.content or ""
    if isinstance(t, str) and t.strip():
        return t.strip(), c.strip()
    m = _THINK_RE.search(c)
    if m:
        return m.group(1).strip(), _THINK_RE.sub("", c).strip()
    return "", c.strip()


def _map_stop(raw: Any) -> str:
    if not raw:
        return ""
    return _STOP_MAP.get(str(raw), str(raw))
