"""对话出站总线：环形缓冲 + SSE 广播。

环形缓冲供 GET /api/chat 补拉（页面刷新/断线重连恢复对话pane）；
webui_send 工具在 tools.py（模块约定），由 Server.start() 注册，
能力可用性由回执表达（未运行 → ok:false），不随服务卸载摘除。
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

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

    def running(self) -> bool:
        """server 是否在运行（工具可用性同据）。"""
        return bool(self._running())

    @property
    def connection_count(self) -> int:
        """当前 SSE 连接数（无人在听时 webui_send 降级回执）。"""
        return self._bc.count

    def send(self, text: str) -> dict[str, Any]:
        """广播 webui_msg + 入缓冲。返回回执对象。"""
        n = self._bc.push({"type": "webui_msg", "ts": time.time(),
                           "data": {"text": text}})
        self.record("out", text)
        return {"ok": True, "delivered": n}
