"""事件桥：内核事件 → SSE 广播（单点订阅，回调零 IO）。

广播器：每客户端一个有界队列，满则丢最旧并计数——慢客户端绝不反压
心智循环（订阅回调运行在思维循环里，只做 put_nowait）。
"""
from __future__ import annotations

import asyncio
from typing import Any


class Broadcaster:
    """SSE 客户端广播器（有界队列 / 丢最旧 / 客户端计数）。"""

    def __init__(self, maxq: int = 128) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._maxq = maxq
        self.dropped = 0

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxq)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def push(self, obj: dict[str, Any]) -> int:
        """非阻塞广播（订阅回调里调用）。返回送达客户端数。"""
        n = 0
        for q in list(self._clients):
            try:
                q.put_nowait(obj)
                n += 1
            except asyncio.QueueFull:
                try:
                    q.get_nowait()   # 丢最旧
                    q.put_nowait(obj)
                    self.dropped += 1
                    n += 1
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        return n

    @property
    def count(self) -> int:
        return len(self._clients)
