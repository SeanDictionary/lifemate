"""网络服务层：API + SSE 实时流 + 前端静态托管 + 插件面板挂载。

与 Host（运行时服务）对等：只消费组合根（__main__）准备好的共享上下文
（kernel/store/recorder/cfg/stop），互不 import。始终启动；端口绑定失败
记 ERROR 降级（生命体照常运行，webui_send 回执"webui 未运行"）。

刻意不做：进程控制命令（退出/重启）——远程不操纵宿主。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .events import Broadcaster
from .http import Route, json_resp, no_build_page, read_request, static_resp
from .outbound import ChatBus
from .routes import build_core_routes
from .tools import make_webui_tool

if TYPE_CHECKING:
    from ..kernel.contracts import MindEvent
    from ..kernel.mind import SelfKernel
    from ..memory.store import Store
    from ..plugins import Plugin
    from ..utils.config import Config
    from ..utils.recorder import Recorder

log = logging.getLogger("server")

_HEARTBEAT_S = 15.0


class Server:
    """网络服务（生命周期：start → serve/close）。"""

    def __init__(self, kernel: SelfKernel, store: Store, recorder: Recorder, cfg: Config, stop: asyncio.Event, plugins: list[Plugin] | None = None) -> None:
        self.kernel = kernel
        self.store = store
        self.recorder = recorder
        self.cfg = cfg
        self.stop = stop
        self.plugins = plugins or []
        self.t0 = time.time()

        self.broadcaster = Broadcaster()
        self.chatbus = ChatBus(self.broadcaster, is_running=lambda: self._running)
        self._routes: list[Route] = []
        self._panels: list[dict[str, Any]] = []  # /api/plugins 声明
        self._plugin_route_count = 0
        self._server: asyncio.AbstractServer | None = None
        self._unsub: Any = None
        self._running = False

    # ── 生命周期 ───────────────────────────────────────
    async def start(self) -> int | None:
        """绑定 + 订阅 + 注册工具 + 装配路由。返回端口（失败 None，降级）。"""
        try:
            self._server = await asyncio.start_server(self._handle, self.cfg.server_host, self.cfg.server_port)
        except OSError as e:
            log.error("server 端口绑定失败（降级，生命体照常运行）：%s", e)
            return None
        port = self._server.sockets[0].getsockname()[1]
        self._unsub = self.kernel.subscribe(self._on_event)
        self.kernel.register_tool(make_webui_tool(self.chatbus))
        self._routes = build_core_routes(self) + self._plugin_routes()
        self._running = True
        log.info("server 启动：http://%s:%d（核心 %d + 插件面板 %d 路由）", self.cfg.server_host, port, len(self._routes) - self._plugin_route_count, self._plugin_route_count)
        return port

    async def serve(self) -> None:
        """运行至 stop（组合根调用；与 Host 并行）。"""
        if await self.start() is None:
            return
        try:
            await self.stop.wait()
        finally:
            await self.close()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                # 3.12+ wait_closed 会等所有连接 handler 结束：限时，
                # 防止朝着的 SSE/慢消费连接挂死整个停机流程
                await asyncio.wait_for(self._server.wait_closed(), timeout=3.0)
            except TimeoutError:
                log.warning("server：连接收尾超时（3s），剩余连接由进程收尾取消")
            self._server = None
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._running = False
        log.info("server 已停止")

    # ── 事件桥（订阅回调：零 IO，只广播） ──────────────
    def _on_event(self, ev: MindEvent) -> None:
        self.broadcaster.push({"type": ev.type, "ts": time.time(), "data": ev.data})

    # ── 插件面板 ──────────────────────────────────────
    def _plugin_routes(self) -> list[Route]:
        """扫描已装载插件的 panel.py，挂到 /api/plugins/<name>/*。

        契约（插件零 server 依赖）：panel.py 导出
        PANEL = {"title", "icon"} 与 routes(ctx, plugin) -> [{"method",
        "path", "handler"}]；handler 为 async (req) -> Any（自动 JSON 化）。
        拔插件 = 装载清单没有 = 路由不存在（404）。
        """
        routes: list[Route] = []
        self._panels = []
        self._plugin_route_count = 0
        for p in self.plugins:
            try:
                mod = importlib.import_module(f"lifemate.plugins.{p.name}.panel")
            except ImportError:
                continue  # 无面板
            except Exception:  # noqa: BLE001
                log.exception("插件面板导入失败（跳过）：%s", p.name)
                continue
            try:
                ctx = self._panel_ctx(p.name)
                entries = mod.routes(ctx, p) or []
            except Exception:  # noqa: BLE001
                log.exception("插件面板路由收集失败（跳过）：%s", p.name)
                continue
            for e in entries:
                handler = self._wrap_panel_handler(p.name, e["handler"])
                routes.append(Route(e.get("method", "GET"), f"/api/plugins/{p.name}/{e['path']}", handler))
                self._plugin_route_count += 1
            self._panels.append({"name": p.name, "panel": getattr(mod, "PANEL", None)})
        return routes

    def _panel_ctx(self, name: str):
        from ..plugins import PluginContext

        return PluginContext(kernel=self.kernel, store=self.store, cfg=self.cfg, recorder=self.recorder, data_dir=Path(self.cfg.data_dir) / "plugins" / name)

    @staticmethod
    def _wrap_panel_handler(name: str, handler):
        async def _run(req, _params):
            return await handler(req)

        return _run

    def plugin_panels(self) -> list[dict[str, Any]]:
        """/api/plugins 声明（无面板插件也列出，panel=None）。"""
        known = {pl["name"] for pl in self._panels}
        out = list(self._panels)
        for p in self.plugins:
            if p.name not in known:
                out.append({"name": p.name, "panel": None})
        return out

    # ── 连接处理 ──────────────────────────────────────
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await read_request(reader)
            if req is None:
                return
            # 鉴权：仅 /api/*（壳公开、数据保护——页面得先能加载才能输 token；
            # 静态资源是应用本身，无数据敏感性）
            if req.path.startswith("/api/") and self.cfg.server_token and req.query.get("token") != self.cfg.server_token:
                writer.write(json_resp({"ok": False, "error": "unauthorized"}, 401))
                await writer.drain()
                return
            # SSE：唯一流式端点，特判接管（多流需求出现再抽象）
            if req.path == "/api/events":
                await self._stream_sse(writer)
                return
            # 静态前端（非 /api/ 路径）
            if not req.path.startswith("/api/"):
                root = Path(self.cfg.server_static_dir)
                if not root.exists():
                    writer.write(no_build_page())
                else:
                    writer.write(static_resp(root, req.path))
                await writer.drain()
                return
            # API 路由分发
            for r in self._routes:
                params = r.match(req.method, req.path)
                if params is not None:
                    try:
                        out = await r.handler(req, params)
                    except Exception:  # noqa: BLE001
                        log.exception("API 异常：%s %s", req.method, req.path)
                        writer.write(json_resp({"ok": False, "error": "internal error"}, 500))
                    else:
                        writer.write(json_resp(out))
                    await writer.drain()
                    return
            writer.write(json_resp({"ok": False, "error": "not found"}, 404))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _stream_sse(self, writer: asyncio.StreamWriter) -> None:
        """SSE 流：事件推送 + 15s 心跳；断开或停机即清理。"""
        q = self.broadcaster.subscribe()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n")
        try:
            await writer.drain()
            while True:
                # 事件/停机/心跳三路等待：停机立即退流，不被 15s 心跳周期拖住
                get_f = asyncio.ensure_future(q.get())
                stop_f = asyncio.ensure_future(self.stop.wait())
                done, _ = await asyncio.wait({get_f, stop_f}, timeout=_HEARTBEAT_S, return_when=asyncio.FIRST_COMPLETED)
                if stop_f in done:
                    get_f.cancel()
                    break  # 停机：退出流（连接由 finally 关闭）
                if get_f in done:
                    stop_f.cancel()
                    data = json.dumps(get_f.result(), ensure_ascii=False)
                    writer.write(f"data: {data}\n\n".encode())
                    await writer.drain()
                    continue
                get_f.cancel()  # 心跳周期到点：证明流还活着
                stop_f.cancel()
                writer.write(b": ping\n\n")
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.broadcaster.unsubscribe(q)
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
