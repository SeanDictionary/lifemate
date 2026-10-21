"""对话出站：webui_send 工具 + webui 对话环形缓冲。

环形缓冲供 GET /api/chat 补拉（页面刷新/断线重连恢复对话pane）；
工具由 Server.start() 注册（宿主侧先例：read_skill/deep_sleep），
能力可用性由回执表达（未运行 → ok:false），不随服务卸载摘除。
"""
from __future__ import annotations

import json
import time
from collections import deque
from typing import Any

from ..kernel.contracts import Tool
from .events import Broadcaster


class ChatBus:
    """webui 对话总线：环形缓冲 + webui_send 工具。"""

    def __init__(self, broadcaster: Broadcaster,
                 is_running=lambda: True, maxlen: int = 200) -> None:
        self._bc = broadcaster
        self._running = is_running
        self._ring: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def record(self, dir_: str, text: str) -> None:
        self._ring.append({"dir": dir_, "text": text, "ts": time.time()})

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._ring)[-max(0, limit):]

    def send(self, text: str) -> dict[str, Any]:
        """广播 webui_msg + 入缓冲。返回回执对象。"""
        n = self._bc.push({"type": "webui_msg", "ts": time.time(),
                           "data": {"text": text}})
        self.record("out", text)
        return {"ok": True, "delivered": n}

    def make_tool(self) -> Tool:
        async def _fn(args: dict) -> str:
            text = str(args.get("text") or "").strip()
            if not text:
                return json.dumps(
                    {"ok": False, "error": "text 不能为空"},
                    ensure_ascii=False)
            if not self._running() or self._bc.count == 0:
                return json.dumps(
                    {"ok": False, "error": "webui 未运行（无连接）"},
                    ensure_ascii=False)
            return json.dumps(self.send(text), ensure_ascii=False)

        return Tool(schema={
            "name": "webui_send",
            "description": "回复网页对话窗口（主人通过浏览器跟你说话时，"
                           "用它回话；信封来源是 webui）。",
            "inputSchema": {"type": "object", "properties": {
                "text": {"type": "string", "description": "要说的话"},
            }, "required": ["text"]}},
            func=_fn, interruptible=True)
