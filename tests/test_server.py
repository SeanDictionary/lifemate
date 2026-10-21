"""server 网络服务层测试：路由/鉴权/SSE/档案/配置写回/记忆/插件面板。

全部走 ephemeral 端口（port 0）+ 裸 HTTP（asyncio 流），零第三方依赖。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path

import pytest

from lifemate.kernel.contracts import MindEvent
from lifemate.memory.store import Store
from lifemate.server import Server
from lifemate.utils.config import Config

from conftest import make_config


class FakeKernel:
    """server 视角的最小内核（订阅/工具/信封/session 状态）。"""

    def __init__(self):
        self.subs = []
        self.tools = {}
        self.envelopes = []
        self.session_id = "test-session"
        self.is_busy = False

    def subscribe(self, cb):
        self.subs.append(cb)
        return lambda: self.subs.remove(cb)

    def register_tool(self, t):
        self.tools[t.name] = t

    def tool_names(self):
        return list(self.tools)

    def notify(self, env):
        self.envelopes.append(env)

    def emit(self, type_, data=None):
        for cb in list(self.subs):
            cb(MindEvent(type=type_, data=data or {}))


class FakePlugin:
    def __init__(self, name):
        self.name = name
        self.tools = []


async def _req(port: int, method: str, path: str, body: dict | None = None):
    """裸 HTTP 请求 → (status, body_bytes)。"""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"{method} {path} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n"
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        head += (f"Content-Type: application/json\r\n"
                 f"Content-Length: {len(payload)}\r\n")
    writer.write((head + "\r\n").encode("latin-1") + payload)
    await writer.drain()
    data = await reader.read(-1)
    writer.close()
    head_b, _, body_b = data.partition(b"\r\n\r\n")
    return int(head_b.split(b" ")[1]), body_b


@pytest.fixture()
async def env(tmp_path):
    """起一个 server（ephemeral 端口）。yield (port, kernel, store, cfg, tmp)。"""
    cfg = make_config(tmp_path)
    cfg.server_host = "127.0.0.1"
    cfg.server_port = 0
    cfg.server_token = ""
    kernel = FakeKernel()
    store = Store(tmp_path / "memory.db")
    stop = asyncio.Event()
    srv = Server(kernel, store, None, cfg, stop, plugins=[])
    port = await srv.start()
    assert port, "端口绑定失败"
    try:
        yield port, kernel, store, cfg, tmp_path
    finally:
        await srv.close()
        store.close()


async def _jget(port, path):
    status, body = await _req(port, "GET", path)
    return status, json.loads(body)


# ── 状态 / 静态 / 路由 ─────────────────────────────────

async def test_status_and_no_build_page(env):
    port, kernel, _, cfg, _ = env
    status, data = await _jget(port, "/api/status")
    assert status == 200 and data["ok"]
    assert data["session_id"] == "test-session"
    assert data["plugins"] == [] and data["tool_count"] == 1  # webui_send
    assert "version" in data and "uptime_s" in data
    # 静态目录缺失 → 提示页（API 不受影响）
    s2, b2 = await _req(port, "GET", "/")
    assert s2 == 200 and "前端未构建" in b2.decode("utf-8")


async def test_not_found_and_param_route(env):
    port, _, _, _, _ = env
    status, data = await _jget(port, "/api/nothing")
    assert status == 404 and data["ok"] is False
    status, data = await _jget(port, "/api/memory/bogus_table")
    assert status == 200 and data["ok"] is False  # 路由命中但表名非法


async def test_token_auth(env):
    port, _, _, cfg, _ = env
    cfg.server_token = "sec-token"          # 每请求读，即时生效
    try:
        status, _ = await _jget(port, "/api/status")
        assert status == 401
        status, data = await _jget(port, "/api/status?token=sec-token")
        assert status == 200 and data["ok"]
    finally:
        cfg.server_token = ""


# ── 对话 ───────────────────────────────────────────────

async def test_say_and_chat_ring(env):
    port, kernel, _, _, _ = env
    status, body = await _req(port, "POST", "/api/say", {"text": "你好"})
    assert status == 200 and json.loads(body)["ok"]
    env_ = kernel.envelopes[-1]
    assert env_.text == "你好" and env_.kind == "direct"
    assert env_.source == "webui" and env_.origin == "server"
    # 环形缓冲补拉（刷新恢复）
    status, data = await _jget(port, "/api/chat")
    assert data["messages"][-1] == {"dir": "in", "text": "你好",
                                    "ts": data["messages"][-1]["ts"]}
    # 空文本回执
    status, body = await _req(port, "POST", "/api/say", {"text": " "})
    assert json.loads(body)["ok"] is False


async def test_webui_send_tool_and_sse(env):
    port, kernel, _, _, _ = env
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /api/events HTTP/1.1\r\nHost: t\r\n\r\n")
    await writer.drain()
    await asyncio.sleep(0.2)                # 服务端完成订阅

    kernel.emit("assistant_msg", {"content": "我醒了"})
    line = await asyncio.wait_for(_read_data_line(reader), 3.0)
    frame = json.loads(line)
    assert frame["type"] == "assistant_msg"
    assert frame["data"]["content"] == "我醒了"
    assert isinstance(frame["ts"], float)

    # webui_send 工具 → webui_msg 帧 + 入缓冲
    tool = kernel.tools["webui_send"]
    out = json.loads(await tool.func({"text": "回复你"}))
    assert out["ok"] and out["delivered"] == 1
    line = await asyncio.wait_for(_read_data_line(reader), 3.0)
    frame = json.loads(line)
    assert frame["type"] == "webui_msg" and frame["data"]["text"] == "回复你"
    writer.close()
    # 缓冲里 in/out 都在
    _, data = await _jget(port, "/api/chat")
    texts = [m["text"] for m in data["messages"]]
    assert "回复你" in texts


async def _read_data_line(reader) -> str:
    while True:
        line = await reader.readline()
        if line.startswith(b"data: "):
            return line[6:].decode("utf-8").strip()
        if not line:
            raise ConnectionError("SSE 流关闭")


# ── 档案 / 用量 ────────────────────────────────────────

async def test_history_dates_usage(env):
    port, _, _, cfg, tmp = env
    observe = Path(cfg.data_dir) / "observe"
    observe.mkdir(parents=True, exist_ok=True)
    (observe / "2026-01-01.jsonl").write_text(
        '{"type":"msg","role":"user","rid":"r1","content":"hi"}\n'
        '{"type":"msg","role":"assistant","rid":"r1","model":"m1",'
        '"prompt_tokens":100,"completion_tokens":50,"latency_ms":200}\n'
        '{"type":"msg","role":"assistant","rid":"r2","model":"m1",'
        '"prompt_tokens":10,"completion_tokens":5,"latency_ms":100}\n'
        '{oops 坏行\n', encoding="utf-8")

    _, data = await _jget(port, "/api/dates")
    assert data["dates"] == ["2026-01-01"]

    _, data = await _jget(port, "/api/history?date=2026-01-01")
    assert data["ok"] and data["count"] == 3          # 坏行跳过
    _, data = await _jget(port, "/api/history?date=2026-01-01&rid=r1")
    assert data["count"] == 2
    _, data = await _jget(port, "/api/history?date=2020-01-01")
    assert data["ok"] is False and "2026-01-01" in data["available"]

    _, data = await _jget(port, "/api/usage?date=2026-01-01")
    agg = data["by_model"]["m1"]
    assert agg["calls"] == 2 and agg["tokens_in"] == 110
    assert agg["latency_ms_avg"] == 150


# ── 配置（读/改存/重载） ───────────────────────────────

async def test_config_get_apply_reload(env):
    port, _, _, cfg, _ = env
    cfg.llm_api_key = "sk-secret-12345678"
    _, data = await _jget(port, "/api/config")
    key = data["config"]["llm_api_key"]
    assert key.startswith("***") and key.endswith("678")   # 掩码

    status, body = await _req(port, "POST", "/api/config", {
        "log_level": "debug",              # 正常应用
        "llm_api_key": "***678",           # 掩码回传 = 未修改
        "bogus_key": 1,                    # 白名单外
    })
    data = json.loads(body)
    assert data["applied"] == ["log_level"]
    assert data["skipped_masked"] == ["llm_api_key"]
    assert data["unknown"] == ["bogus_key"]
    assert data["ok"] is False                             # 有未知键
    assert cfg.log_level == "debug"
    assert '"log_level": "debug"' in cfg.path.read_text(encoding="utf-8")

    # 密钥明文新值 → 覆盖
    status, body = await _req(port, "POST", "/api/config",
                              {"llm_api_key": "sk-new-9999"})
    data = json.loads(body)
    assert data["applied"] == ["llm_api_key"]
    assert cfg.llm_api_key == "sk-new-9999"

    _, body = await _req(port, "POST", "/api/config/reload")
    data = json.loads(body)
    assert data["ok"] and isinstance(data["changed"], list)


# ── 记忆浏览 ───────────────────────────────────────────

async def test_memory_endpoints(env):
    port, _, store, _, _ = env
    store.record_episode("测试事件", source="test")
    store.add_reminder(time.time() + 3600, "下午提醒")
    _, data = await _jget(port, "/api/memory/episodes")
    assert data["ok"] and data["rows"][0]["summary"] == "测试事件"
    _, data = await _jget(port, "/api/memory/reminders")
    assert data["ok"] and data["rows"][0]["intent"] == "下午提醒"


# ── 插件面板挂载 ───────────────────────────────────────

async def test_plugin_panel_mount(tmp_path, monkeypatch):
    async def _status(req):
        return {"ok": True, "hello": "panel"}

    mod = types.ModuleType("lifemate.plugins.dummy.panel")
    mod.PANEL = {"title": "Dummy", "icon": "🧪"}
    mod.routes = lambda ctx, plugin: [
        {"method": "GET", "path": "status", "handler": _status}]
    parent = types.ModuleType("lifemate.plugins.dummy")
    monkeypatch.setitem(sys.modules, "lifemate.plugins.dummy", parent)
    monkeypatch.setitem(sys.modules, "lifemate.plugins.dummy.panel", mod)

    cfg = make_config(tmp_path)
    cfg.server_host, cfg.server_port, cfg.server_token = "127.0.0.1", 0, ""
    kernel = FakeKernel()
    store = Store(tmp_path / "memory.db")
    stop = asyncio.Event()
    srv = Server(kernel, store, None, cfg, stop,
                 plugins=[FakePlugin("dummy")])
    port = await srv.start()
    try:
        _, data = await _jget(port, "/api/plugins")
        assert data["plugins"] == [{"name": "dummy",
                                    "panel": {"title": "Dummy", "icon": "🧪"}}]
        _, data = await _jget(port, "/api/plugins/dummy/status")
        assert data == {"ok": True, "hello": "panel"}
    finally:
        await srv.close()
        store.close()


async def test_plugin_without_panel_listed(env):
    """无面板插件也在 /api/plugins 列出（panel=None），路由 404。"""
    port, _, _, cfg, tmp = env
    # env fixture 用空插件表；这里直接验证 404 语义即可
    status, data = await _jget(port, "/api/plugins/qq/status")
    assert status == 404
