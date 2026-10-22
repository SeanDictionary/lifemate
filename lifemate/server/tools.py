"""server 工具：webui_send（网页对话出口）。

约定：每个模块的工具住该模块的 tools.py，无例外。
总线（环形缓冲 + SSE 广播）在 outbound.py，此处只持工具工厂。
"""
from __future__ import annotations

import json

from ..kernel.contracts import Tool
from .outbound import ChatBus


def make_webui_tool(chatbus: ChatBus) -> Tool:
    async def _fn(args: dict) -> str:
        text = str(args.get("text") or "").strip()
        if not text:
            return json.dumps(
                {"ok": False, "error": "text 不能为空"},
                ensure_ascii=False)
        if not chatbus.running() or chatbus.connection_count == 0:
            return json.dumps(
                {"ok": False, "error": "webui 未运行（无连接）"},
                ensure_ascii=False)
        return json.dumps(chatbus.send(text), ensure_ascii=False)

    return Tool(schema={
        "name": "webui_send",
        "description": "回复网页对话窗口（主人通过浏览器跟你说话时，"
                       "用它回话；信封来源是 webui）。",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "要说的话"},
        }, "required": ["text"]}},
        func=_fn, interruptible=True)
