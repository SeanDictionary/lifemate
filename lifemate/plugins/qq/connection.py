"""OneBot v11 正向 WS 客户端：连 NapCat（/ 路径=API+事件同连接）。

- echo 关联的异步 RPC（借签 v1 的 WsClient 模式，方向倒转为主动外连）
- 断线指数退避重连（1s→60s 封顶）；重连期间 API 调用回执 unreachable
- access_token 走 Authorization: Bearer 头（协议 authorization 规范）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("plugins.qq.conn")


class OneBotClient:
    """单连接客户端：收包循环 + echo RPC。"""

    def __init__(self, ws_url: str, access_token: str = "", on_event: Callable[[dict], Awaitable[None]] | None = None, call_timeout: float = 30.0) -> None:
        self._url = ws_url
        self._token = access_token
        self._on_event = on_event
        self._call_timeout = call_timeout
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self.connected_since: float = 0.0

    # ── 生命周期 ───────────────────────────────────────
    def start(self) -> None:
        """启动连接循环（幂等）。"""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._fail_all(ConnectionError("连接已停止"))

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ── 连接循环 ───────────────────────────────────────
    async def _run_loop(self) -> None:
        import websockets  # 惰性导入

        backoff = 1.0
        while not self._stop.is_set():
            try:
                headers = []
                if self._token:
                    headers.append(("Authorization", f"Bearer {self._token}"))
                kw: dict[str, Any] = {}
                if headers:
                    kw["additional_headers"] = headers  # websockets>=14
                try:
                    ws = websockets.connect(self._url, **kw)
                except TypeError:  # 旧版 websockets 参数名
                    kw["extra_headers"] = headers
                    ws = websockets.connect(self._url, **kw)
                async with ws as conn:
                    self._ws = conn
                    self.connected_since = time.time()
                    backoff = 1.0
                    log.info("QQ 已连接：%s", self._url)
                    await self._serve(conn)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("QQ 连接断开（%s）", e)
            finally:
                self._ws = None
                self._fail_all(ConnectionError("连接断开"))
            if self._stop.is_set():
                break
            log.info("QQ 将在 %.0fs 后重连", backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)

    async def _serve(self, conn) -> None:
        """收包循环：echo 响应结算，事件交给回调。"""

        async for raw in conn:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            echo = data.get("echo")
            if echo is not None and echo in self._pending:
                fut = self._pending.pop(echo)
                if not fut.done():
                    fut.set_result(data)
                continue
            if "post_type" in data and self._on_event is not None:
                try:
                    await self._on_event(data)
                except Exception:  # noqa: BLE001
                    log.exception("QQ 事件处理异常")

    def _fail_all(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # ── RPC ────────────────────────────────────────────
    async def call(self, action: str, params: dict | None = None) -> dict:
        """OneBot 动作调用（echo 关联）。断线/超时抛 ConnectionError/TimeoutError。"""
        if self._ws is None:
            raise ConnectionError("QQ 未连接")
        echo = uuid.uuid4().hex[:16]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[echo] = fut
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            async with self._send_lock:
                await self._ws.send(json.dumps(payload, ensure_ascii=False))
            resp = await asyncio.wait_for(fut, timeout=self._call_timeout)
        finally:
            self._pending.pop(echo, None)
        if resp.get("retcode") not in (0, None) or resp.get("status") == "failed":
            raise RuntimeError(f"API {action} 失败: retcode={resp.get('retcode')} {resp.get('word') or ''}")
        return resp.get("data") or {}
