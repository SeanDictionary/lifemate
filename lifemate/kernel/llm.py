"""模型调用唯一入口（OpenAI 兼容 Chat Completions）。

内核内部表示（LLMResponse/ToolCall）与具体 API 方言解耦：
方言翻译（reasoning_content 剥离等）收在本文件，思维循环不感知。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol

from .contracts import LLMResponse, ToolCall, ToolSchema


class LLMClient(Protocol):
    """思维循环依赖的最小接口（测试可注入 FakeLLM）。"""

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[ToolSchema] | None = None,
    ) -> LLMResponse: ...


class OpenAILLM:
    """OpenAI 兼容客户端：持有活配置，签名变更时热重建连接。"""

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._client = None
        self._sig: tuple[str, str] | None = None

    def _get_client(self):
        sig = (self._cfg.llm_base_url, self._cfg.llm_api_key)
        if self._client is None or sig != self._sig:
            from openai import AsyncOpenAI  # 惰性导入，测试环境零依赖
            self._client = AsyncOpenAI(
                base_url=self._cfg.llm_base_url or "https://api.openai.com/v1",
                api_key=self._cfg.llm_api_key or "sk-unconfigured",
                timeout=300.0)
            self._sig = sig
        return self._client

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if tools:
            # MCP 形状 → OpenAI function 形状（唯一方言翻译点：
            # name/description/inputSchema → name/description/parameters）
            kwargs["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {"type": "object"}),
                }}
                for t in tools
            ]
        t0 = time.monotonic()
        resp = await self._get_client().chat.completions.create(
            model=self._cfg.llm_model, messages=messages, **kwargs)
        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                  arguments=_normalize_args(
                                      tc.function.arguments)))
        content = _strip_thinking(msg.content or "")
        u = resp.usage
        _ = time.monotonic() - t0  # 延迟供观测层使用（当前仅 debug 级）
        return LLMResponse(
            content=content, tool_calls=calls,
            prompt_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            completion_tokens=getattr(u, "completion_tokens", 0) if u else 0,
        )


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


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


def _strip_thinking(text: str) -> str:
    """剥离 Qwen 式 <think> 内嵌标签（避免污染回复）。"""
    return _THINK_RE.sub("", text).strip()
