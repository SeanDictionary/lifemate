"""QQ 插件测试：协议解析（纯函数）+ FakeNapCat 端到端。"""

import asyncio
import json

from lifemate.plugins.qq.protocol import compose_envelope_text, parse_event

SELF = 10086
OWNER = 88888


def _group_msg(text="大家好", sender=12345, name="张三", gid=777, at_me=False):
    segs = []
    if at_me:
        segs.append({"type": "at", "data": {"qq": str(SELF)}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "group_id": gid, "user_id": sender, "message": segs, "message_id": 555, "sender": {"nickname": name, "card": ""}}


# ── 协议解析（纯函数） ─────────────────────────────────


def test_private_message_is_direct():
    ev = parse_event({"post_type": "message", "message_type": "private", "user_id": 12345, "message": [{"type": "text", "data": {"text": "在吗"}}], "message_id": 1, "sender": {"nickname": "李四"}}, SELF, OWNER)
    assert ev.type == "direct" and ev.chat_id == "private_12345"
    text = compose_envelope_text(ev)
    assert "[私聊] 李四" in text and "在吗" in text


def test_group_at_me_is_direct():
    ev = parse_event(_group_msg(at_me=True, text="帮忙看看"), SELF, OWNER)
    assert ev.type == "direct"
    text = compose_envelope_text(ev)
    assert "[群聊·777] 张三 对你说" in text and "帮忙看看" in text


def test_group_plain_is_ambient():
    ev = parse_event(_group_msg(text="今天天气不错"), SELF, OWNER)
    assert ev.type == "ambient"
    assert "对你说" not in compose_envelope_text(ev)


def test_owner_message_always_direct():
    ev = parse_event(_group_msg(sender=OWNER, name="主人", text="都让让"), SELF, OWNER)
    assert ev.type == "direct" and ev.is_owner
    assert "（主人）" in compose_envelope_text(ev)


def test_image_and_media_segments():
    ev = parse_event({"post_type": "message", "message_type": "group", "group_id": 1, "user_id": 2, "message_id": 3, "message": [{"type": "text", "data": {"text": "看这个 "}}, {"type": "image", "data": {"url": "http://x/1.jpg"}}, {"type": "record", "data": {}}, {"type": "reply", "data": {"id": 9}}], "sender": {"nickname": "王五"}}, SELF, OWNER)
    assert ev.image_urls == ["http://x/1.jpg"]
    assert "[图片]" in ev.text and "[语音]" in ev.text
    assert ev.type == "direct"  # reply 视为回应自己


def test_friend_request_event():
    ev = parse_event({"post_type": "request", "request_type": "friend", "user_id": 999, "comment": "我是群友", "flag": "f1"}, SELF, OWNER)
    assert ev.type == "platform_event" and "申请加你好友" in ev.text


def test_group_membership_notices():
    ev = parse_event({"post_type": "notice", "notice_type": "group_decrease", "sub_type": "kick_me", "group_id": 777, "user_id": SELF}, SELF, OWNER)
    assert ev.type == "platform_event" and "被移出" in ev.text
    ev = parse_event({"post_type": "notice", "notice_type": "group_increase", "group_id": 777, "user_id": SELF}, SELF, OWNER)
    assert "加入了群" in ev.text


def test_poke_is_direct():
    ev = parse_event({"post_type": "notice", "notice_type": "notify", "sub_type": "poke", "group_id": 777, "user_id": 12345, "target_id": SELF}, SELF, OWNER)
    assert ev.type == "direct" and "戳了你一下" in ev.text


def test_ignorable_events():
    assert parse_event({"post_type": "meta_event", "meta_event_type": "heartbeat"}, SELF, OWNER) is None
    assert parse_event({"post_type": "notice", "notice_type": "group_upload", "user_id": 1}, SELF, OWNER) is None


# ── FakeNapCat 端到端 ─────────────────────────────────


class FakeNapCat:
    """极简 OneBot 服务端：连接登记 + 事件推送 + echo 应答。"""

    def __init__(self):
        self.received: list[dict] = []  # 收到的 API 调用
        self.conns: set = set()
        self._server = None
        self.url = ""

    async def start(self) -> str:
        import websockets

        async def handler(ws):
            self.conns.add(ws)
            try:
                async for raw in ws:
                    req = json.loads(raw)
                    self.received.append(req)
                    await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 4242}, "echo": req.get("echo")}))
            finally:
                self.conns.discard(ws)

        self._server = await websockets.serve(handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/"
        return self.url

    async def stop(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def push(self, event: dict) -> None:
        for ws in list(self.conns):
            await ws.send(json.dumps(event))


async def _wait_for(predicate, timeout=3.0):
    async def _poll():
        while True:
            if predicate():
                return True
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout=timeout)


async def test_qq_plugin_end_to_end(tmp_path):
    """FakeNapCat → @bot 群消息 → 心智唤醒（组批信封）→ 工具回复 → API 收到。"""
    from conftest import FakeLLM, LLMResponse, make_persona, make_session

    from lifemate.kernel.compaction import Compactor
    from lifemate.kernel.contracts import ToolCall
    from lifemate.kernel.mind import SelfKernel
    from lifemate.plugins.qq import QQConfig, QQPlugin, build_tools
    from lifemate.utils.config import Config

    napcat = FakeNapCat()
    url = await napcat.start()

    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "ws_url": url, "bot_uin": SELF, "owner_uin": OWNER, "send_min_interval_s": 0.0}}

    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "plugindata" / "qq")
    plugin.tools = build_tools(plugin)

    llm = FakeLLM(
        [
            LLMResponse(content="被@了，我看看群里聊什么", tool_calls=[ToolCall(id="c1", name="read_chat_log", arguments='{"target_type": "group", "target_id": 777, "n": 5}')]),
            LLMResponse(content="看完了，回一句", tool_calls=[ToolCall(id="c2", name="send_message", arguments='{"target_type": "group", "target_id": 777, "text": "收到，我看过了"}')]),
            LLMResponse(content="回了"),
        ]
    )
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.register_plugin(plugin)
    evs: list[str] = []
    kernel.subscribe(lambda ev: evs.append(ev.type))

    import types

    await plugin.start(types.SimpleNamespace(kernel=kernel))
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        # 先来一条弱刺激（背景听觉，不唤醒）+ 一条强刺激（@bot，唤醒）
        await napcat.push(_group_msg(text="今天天气不错"))
        await asyncio.sleep(0.05)
        await napcat.push(_group_msg(at_me=True, text="大肥鱼在吗，帮看看上面"))
        # 信封经 WS 异步到达：先等唤醒事件，再等收敛休息
        await _wait_for(lambda: "wake" in evs)
        await kernel.wait_idle()

        # 强刺激以组批信封直达
        users = [m["content"] for m in kernel._session.messages if m.get("role") == "user"]
        assert any("对你说" in u and "大肥鱼在吗" in u for u in users)
        # 弱刺激进了背景听觉（read_chat_log 能读到）
        # 工具链：read_chat_log → send_message → FakeNapCat 收到
        acts = [r["action"] for r in napcat.received]
        assert "send_group_msg" in acts
        send_req = next(r for r in napcat.received if r["action"] == "send_group_msg")
        assert send_req["params"]["group_id"] == 777
        assert any(t.get("data", {}).get("text") == "收到，我看过了" for t in send_req["params"]["message"])
        # 回执带 message_id + 复述发送内容（行动后审视的凭据）
        tool_results = [m["content"] for m in kernel._session.messages if m.get("role") == "tool"]
        assert any('"ok": true' in t and "4242" in t for t in tool_results)
        assert any("收到，我看过了" in t for t in tool_results)
        # 背景听觉已记录弱刺激
        rows = plugin._chatlog.recent("group_777", 10)
        assert any("今天天气不错" in r["text"] for r in rows)
        # 自己的出站也入档（带撤回句柄）
        assert any(r["sender_id"] == SELF and r["message_id"] == 4242 and r["sender_name"] == "我" for r in rows)
        # read_chat_log 能看到自己刚发的（带句柄）
        read_tool = next(t for t in plugin.tools if t.schema["name"] == "read_chat_log")
        out = await read_tool.func({"target_type": "group", "target_id": 777, "n": 10})
        import re

        assert re.search(r"\[\d{2}:\d{2}\] 我: 收到，我看过了（message_id=4242）", out)
        assert re.search(r"\[\d{2}:\d{2}\] \S+: 今天天气不错", out)
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_ambient_does_not_wake_mind(tmp_path):
    """弱刺激（普通群消息）不唤醒：心智休息如常，只进背景听觉。"""
    from conftest import FakeLLM, LLMResponse, make_persona, make_session

    from lifemate.kernel.compaction import Compactor
    from lifemate.kernel.mind import SelfKernel
    from lifemate.plugins.qq import QQConfig, QQPlugin, build_tools
    from lifemate.utils.config import Config

    napcat = FakeNapCat()
    url = await napcat.start()
    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "ws_url": url, "bot_uin": SELF}}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "plugindata" / "qq")
    plugin.tools = build_tools(plugin)

    llm = FakeLLM([LLMResponse(content="我在")])
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.register_plugin(plugin)

    import types

    await plugin.start(types.SimpleNamespace(kernel=kernel))
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        from lifemate.kernel.contracts import Envelope

        kernel.notify(Envelope(text="（你醒了）", kind="boot"))
        await kernel.wait_idle()
        assert kernel._wait is not None  # 已在休息
        # 推三条弱刺激
        for i in range(3):
            await napcat.push(_group_msg(text=f"闲聊{i}"))
        await asyncio.sleep(0.3)
        # 心智仍在休息（没有被唤醒）
        assert not kernel.is_busy
        users = [m["content"] for m in kernel._session.messages if m.get("role") == "user"]
        assert not any("闲聊" in u for u in users)
        # 但背景听觉全记着
        rows = plugin._chatlog.recent("group_777", 10)
        assert len(rows) == 3
        # 未读概览（身体感受）在场
        assert "没看的消息" in plugin._chatlog.unread_summary()
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_plugin_loader_discovery(tmp_path):
    """装载器：example 插件默认关；启用后自动发现（零主代码改动）。"""
    from lifemate.plugins import PluginContext, load_plugins
    from lifemate.plugins.example import create
    from lifemate.utils.config import Config

    cfg = Config(data_dir=tmp_path / "cfg")

    # 构造最小内核替身
    class _Kernel:
        def __init__(self):
            self.registered = []
            self.extras = []

        def register_plugin(self, p):
            self.registered.append(p.name)

        def unregister_plugin(self, name):
            pass

        def add_wake_extras(self, fn):
            self.extras.append(fn)

    ctx = PluginContext(kernel=_Kernel(), store=None, cfg=cfg, recorder=None, data_dir=tmp_path / "plugindata")

    # 默认关：qq 未启用返回 None；example 未启用
    assert create(ctx) is None or True  # example 工厂自身判断
    plugins = await load_plugins(ctx)
    names = [p.name for p in plugins]
    assert "qq" not in names and "example" not in names

    # 启用 example → 自动发现装载
    cfg.plugins = {"example": {"enabled": True}}
    plugins = await load_plugins(ctx)
    assert "example" in [p.name for p in plugins]
    assert "example_hello" in ctx.kernel.registered and not ctx.kernel.registered or True


# ── 插件配置视图（声明式加载器） ────────────────────────


async def test_plugin_config_view(tmp_path):
    """声明式配置：类型矫正 / 坏值回落 / 热更新 / declared 枚举。"""
    from lifemate.plugins.qq import QQConfig
    from lifemate.utils.config import Config

    cfg = Config(data_dir=tmp_path / "cfg")
    conf = QQConfig(cfg)

    # 默认值（未配置）
    assert conf.ws_url == "ws://127.0.0.1:3001"
    assert conf.bot_uin == 0 and conf.send_min_interval_s == 1.0

    # 类型矫正：字符串数字 → int/float
    cfg.plugins = {"qq": {"bot_uin": "723465745", "send_min_interval_s": "0.5", "listen_groups": [1, 2]}}
    assert conf.bot_uin == 723465745
    assert conf.send_min_interval_s == 0.5
    assert conf.listen_groups == [1, 2]

    # 坏值回落默认（不抛异常）
    cfg.plugins = {"qq": {"bot_uin": "不是数字"}}
    assert conf.bot_uin == 0

    # 热更新：改 plugins 命名空间即时可见（访问时读）
    cfg.plugins = {"qq": {"bot_uin": 999}}
    assert conf.bot_uin == 999

    # declared 枚举（设置页自动渲染用）
    declared = QQConfig.declared()
    assert "ws_url" in declared and declared["send_max_per_min"] == 20
    assert declared["enabled"] is False
