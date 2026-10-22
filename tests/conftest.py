"""测试公共件：FakeLLM（脚本化响应，零网络）。"""

from __future__ import annotations

from typing import Any

from lifemate.kernel.contracts import LLMResponse, ToolCall
from lifemate.kernel.persona import Persona
from lifemate.kernel.session import Session
from lifemate.utils.config import Config


def make_config(tmp_path) -> Config:
    """隔离的活配置（data_dir 指向临时目录）。"""
    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.compact_threshold_tokens = 10**9  # 测试默认不压缩
    cfg.keep_recent_tokens = 4000
    cfg.max_turns = 5
    cfg.tool_timeout_s = 2.0
    return cfg


class FakeLLM:
    """按脚本顺序返回响应；记录每次收到的 messages 供断言。"""

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], tools: list | None = None) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        if not self.script:
            return LLMResponse(content="（脚本耗尽）")
        resp = self.script.pop(0)
        if tools is None and resp.tool_calls:
            # 无工具调用面时模拟真实模型：只剩文本
            return LLMResponse(content=resp.content or "（收束）")
        return resp


def text_reply(text: str) -> LLMResponse:
    return LLMResponse(content=text)


def tool_reply(name: str, args: str = "{}", cid: str = "call_1") -> LLMResponse:
    return LLMResponse(content="", tool_calls=[ToolCall(id=cid, name=name, arguments=args)])


def make_persona(tmp_path) -> Persona:
    """使用真实仓库的 soul/skills 目录（验证人格装配真实形态）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    return Persona(root / "soul", root / "skills")


def make_session(tmp_path) -> Session:
    return Session.create(tmp_path / "sessions")
