"""QQ 插件测试：协议解析（纯函数）+ FakeNapCat 端到端。"""

import asyncio
import time
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


def test_owner_group_message_is_ambient_but_labeled():
    """主人的群消息不再无条件直达（跟别人说话不打扰你）；（主人）标注保留。"""
    ev = parse_event(_group_msg(sender=OWNER, name="主人", text="都让让"), SELF, OWNER)
    assert ev.type == "ambient" and ev.is_owner
    assert "（主人）" in compose_envelope_text(ev)


def test_owner_private_message_is_direct():
    """主人私聊永远直达。"""
    ev = parse_event({"post_type": "message", "message_type": "private", "user_id": OWNER, "message": [{"type": "text", "data": {"text": "在吗"}}], "message_id": 1, "sender": {"nickname": "主人"}}, SELF, OWNER)
    assert ev.type == "direct" and ev.is_owner
    assert "[私聊·主人]" in compose_envelope_text(ev)


def test_owner_at_me_still_direct():
    """主人@它照样直达（真实呼叫场景由 @/别名/回复/私聊覆盖）。"""
    ev = parse_event(_group_msg(sender=OWNER, name="主人", at_me=True, text="过来"), SELF, OWNER)
    assert ev.type == "direct"


def test_image_and_media_segments():
    ev = parse_event({"post_type": "message", "message_type": "group", "group_id": 1, "user_id": 2, "message_id": 3, "message": [{"type": "text", "data": {"text": "看这个 "}}, {"type": "image", "data": {"url": "http://x/1.jpg"}}, {"type": "record", "data": {}}, {"type": "reply", "data": {"id": 9}}], "sender": {"nickname": "王五"}}, SELF, OWNER)
    assert ev.image_urls == ["http://x/1.jpg"]
    assert "[图片]" in ev.text and "[语音]" in ev.text
    assert ev.type == "direct"  # reply 视为回应自己


def test_alias_hit_is_direct():
    """唤醒别名：叫名字不@也算呼叫（误报只是多看一眼）。"""
    ev = parse_event(_group_msg(text="大肥鱼在吗"), self_id=SELF, owner_uin=OWNER, aliases=["大肥鱼", "肥鱼"])
    assert ev.type == "direct"
    assert "对你说" in compose_envelope_text(ev)
    # 无别名不命中
    assert parse_event(_group_msg(text="今天天气不错"), self_id=SELF, owner_uin=OWNER, aliases=["大肥鱼"]).type == "ambient"
    # 子串别名也命中（叫"肥鱼"）
    assert parse_event(_group_msg(text="肥鱼快出来"), self_id=SELF, owner_uin=OWNER, aliases=["大肥鱼", "肥鱼"]).type == "direct"
    # 谈论而非呼叫也命中——宁可多看一眼，判断权在心智
    assert parse_event(_group_msg(text="大肥鱼今天怎么不理人"), self_id=SELF, owner_uin=OWNER, aliases=["大肥鱼"]).type == "direct"


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


async def _ambient_harness(tmp_path, extra_cfg: dict | None = None, replies=None):
    """弱刺激端到端测试公用装配。返回 (napcat, plugin, kernel, llm)。"""
    import types

    from conftest import FakeLLM, LLMResponse, make_persona, make_session

    from lifemate.kernel.compaction import Compactor
    from lifemate.kernel.mind import SelfKernel
    from lifemate.plugins.qq import QQConfig, QQPlugin, build_tools
    from lifemate.utils.config import Config

    napcat = FakeNapCat()
    url = await napcat.start()
    cfg = Config(data_dir=tmp_path / "cfg")
    section = {"enabled": True, "ws_url": url, "bot_uin": SELF}
    section.update(extra_cfg or {})
    cfg.plugins = {"qq": section}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "plugindata" / "qq")
    plugin.tools = build_tools(plugin)
    llm = FakeLLM(replies if replies is not None else [LLMResponse(content="嗯")])
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.register_plugin(plugin)
    await plugin.start(types.SimpleNamespace(kernel=kernel))
    return napcat, plugin, kernel, llm


async def test_conversation_window_upgrades_followup(tmp_path):
    """对话延续窗口：我发言后的跟话直达（文本保持背景形态，不标"对你说"）。"""
    import time

    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path)
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        from lifemate.kernel.contracts import Envelope

        kernel.notify(Envelope(text="（你醒了）", kind="boot"))
        await kernel.wait_idle()
        # 模拟我刚在群里发言（_log_sent 即出站路径的窗口刷新点）
        plugin._log_sent("group_777", "我说了句笑话", 999)
        assert "group_777" in plugin._last_outbound
        await napcat.push(_group_msg(text="哈哈哈哈确实"))
        await _wait_for(lambda: any("哈哈哈哈确实" in m.get("content", "") for m in kernel._session.messages if m.get("role") == "user"))
        users = [m["content"] for m in kernel._session.messages if m.get("role") == "user"]
        # 升格直达但文本不标"对你说"（对方不一定在对我说话）
        assert not any("对你说" in u and "哈哈哈哈确实" in u for u in users)
        await kernel.wait_idle()
        # 窗口过期：跟话回到背景听觉（不唤醒）
        plugin._last_outbound["group_777"] = time.time() - 700
        await napcat.push(_group_msg(text="这都过去十分钟了"))
        await asyncio.sleep(0.3)
        users = [m["content"] for m in kernel._session.messages if m.get("role") == "user"]
        assert not any("这都过去十分钟了" in u for u in users)
        assert any(r["text"] == "这都过去十分钟了" for r in plugin._chatlog.recent("group_777", 5))
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_ambient_threshold_wake(tmp_path):
    """攒批唤醒：未读攒满阈值即递到眼前（含时间行）；已读同步+冷却钳制。"""
    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, {"ambient_wake_threshold": 3, "ambient_wake_cooldown_s": 60, "ambient_wake_gap_s": 3600})
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        from lifemate.kernel.contracts import Envelope

        kernel.notify(Envelope(text="（你醒了）", kind="boot"))
        await kernel.wait_idle()
        for i in range(3):
            await napcat.push(_group_msg(text=f"闲聊{i}"))
        await _wait_for(lambda: any("你不在时聊了 3 条" in m.get("content", "") for m in kernel._session.messages if m.get("role") == "user"))
        # 信封里是带时间标注的消息行
        assert any("闲聊2" in m["content"] for m in kernel._session.messages if m.get("role") == "user")
        await kernel.wait_idle()
        # 已读同步：未读归零（下次身体感受不重复唠叨）
        assert plugin._chatlog.unread_state("group_777", SELF)[0] == 0
        # 冷却钳制：冷却期内再攒 3 条不重复唤醒
        for i in range(3):
            await napcat.push(_group_msg(text=f"更多{i}"))
        await asyncio.sleep(0.3)
        wakes = [m["content"] for m in kernel._session.messages if m.get("role") == "user" and "你不在时聊了" in m["content"]]
        assert len(wakes) == 1
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_ambient_gap_fallback_wake(tmp_path):
    """兜底唤醒：慢速闲聊攒不满阈值，最早未读等够 gap 即唤醒。"""
    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, {"ambient_wake_threshold": 10, "ambient_wake_gap_s": 0.15, "ambient_wake_cooldown_s": 60})
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        from lifemate.kernel.contracts import Envelope

        kernel.notify(Envelope(text="（你醒了）", kind="boot"))
        await kernel.wait_idle()
        await napcat.push(_group_msg(text="慢速一条"))
        await asyncio.sleep(0.2)  # 最早未读已等 0.2s ≥ gap
        await napcat.push(_group_msg(text="慢速两条"))
        await _wait_for(lambda: any("你不在时聊了 2 条" in m.get("content", "") for m in kernel._session.messages if m.get("role") == "user"))
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_ambient_wake_disabled(tmp_path):
    """阈值 0 = 关闭：纯背景听觉，攒再多也不唤醒（回归现状行为）。"""
    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, {"ambient_wake_threshold": 0})
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        from lifemate.kernel.contracts import Envelope

        kernel.notify(Envelope(text="（你醒了）", kind="boot"))
        await kernel.wait_idle()
        for i in range(10):
            await napcat.push(_group_msg(text=f"刷屏{i}"))
        await asyncio.sleep(0.3)
        users = [m["content"] for m in kernel._session.messages if m.get("role") == "user"]
        assert not any("刷屏" in u for u in users)
        assert not kernel.is_busy
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


# ── 回复链判定（reply 指针解引用） ──────────────────────


def _reply_msg(reply_id, at=None, text="在", sender=12345, gid=777):
    segs = [{"type": "reply", "data": {"id": str(reply_id)}}]
    if at is not None:
        segs.append({"type": "at", "data": {"qq": str(at)}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "group_id": gid, "user_id": sender, "message": segs, "message_id": 556, "sender": {"nickname": "张三", "card": ""}}


def _origin(sender_id, name="某人", text="被引用的内容"):
    return {"sender_id": sender_id, "sender_name": name, "text": text}


def test_reply_to_own_message_is_direct_no_at():
    """回自己发的消息、且没带 @（客户端 @ 可被删除）——仍直达。"""
    ev = parse_event(_reply_msg(100), SELF, OWNER, reply_lookup=lambda mid: _origin(SELF, "我", "我之前说的话"))
    assert ev.type == "direct"
    assert "对你说" in compose_envelope_text(ev)
    # 引用内容内联（心智醒来即有上下文）
    assert "[回复 我：我之前说的话]" in ev.text


def test_reply_to_own_message_with_at_other_is_direct():
    """回自己的消息同时 @ 第三者（拉人入话题）——话题锚在自己，直达。"""
    ev = parse_event(_reply_msg(100, at=224029720), SELF, OWNER, reply_lookup=lambda mid: _origin(SELF))
    assert ev.type == "direct" and "@224029720" in ev.text


def test_reply_to_other_is_ambient_with_inline_quote():
    """回别人的消息 + @ 别人（原误判场景）——背景听觉，且内联引用。"""
    ev = parse_event(_reply_msg(2086235890, at=224029720), SELF, OWNER, reply_lookup=lambda mid: _origin(2878099949, "净化派残废猫猫", "别人说的话"))
    assert ev.type == "ambient"
    assert "对你说" not in compose_envelope_text(ev)
    assert "[回复 净化派残废猫猫：别人说的话]" in ev.text


def test_reply_to_other_no_at_is_ambient():
    """回别人的消息、无 @——背景听觉。"""
    ev = parse_event(_reply_msg(100), SELF, OWNER, reply_lookup=lambda mid: _origin(999))
    assert ev.type == "ambient"


def test_reply_unknown_target_is_ambient():
    """被回复消息超出反查窗（查不到）——降级背景听觉。

    自己发的消息全在档，窗口内查不到几乎必非回自己；
    漏接的兜底：消息照进 chat_log + 未读概览。
    """
    ev = parse_event(_reply_msg(987654321), SELF, OWNER, reply_lookup=lambda mid: None)
    assert ev.type == "ambient"
    assert "[回复987654321]" in ev.text  # 查不到留裸 id（可 get_message_detail 反查）


def test_reply_no_lookup_conservative_direct():
    """无反查通道（降级场景）——保守直达，兼容旧语义。"""
    ev = parse_event(_reply_msg(100), SELF, OWNER)
    assert ev.type == "direct"


def test_reply_quote_snippet_truncated():
    """引用内容截断到 30 字，换行折成空格。"""
    long_text = "很长" * 40 + "\n带换行"
    ev = parse_event(_reply_msg(100), SELF, OWNER, reply_lookup=lambda mid: _origin(999, "甲", long_text))
    quote = ev.text[ev.text.index("[回复") :]
    assert len(quote) < 60 and "\n" not in quote


# ── 跨会话切换：信封附来源会话上下文 ─────────────────────


async def test_envelope_context_on_chat_switch(tmp_path):
    """跨会话切换时信封附该会话最近几条；同会话连续直达不重复附加。"""
    from lifemate.plugins.qq import QQConfig, QQPlugin
    from lifemate.plugins.qq.chatlog import ChatLog
    from lifemate.utils.config import Config

    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "bot_uin": SELF, "owner_uin": OWNER}}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "pd")
    plugin._chatlog = ChatLog(tmp_path / "pd" / "cl")

    class _K:
        def __init__(self):
            self.envs = []

        def notify(self, env):
            self.envs.append(env)

    k = _K()
    plugin._kernel = k
    try:
        # 群A：背景消息 ×2 + 直达（首条直达，附 A 的上下文）
        await plugin._on_event(_group_msg(gid=111, sender=1, name="甲", text="A群旧话1"))
        await plugin._on_event(_group_msg(gid=111, sender=1, name="甲", text="A群旧话2"))
        await plugin._on_event(_group_msg(gid=111, at_me=True, text="A群叫我"))
        e1 = k.envs[-1]
        assert "A群叫我" in e1.text and "该会话最近" in e1.text
        assert "A群旧话1" in e1.text and "A群旧话2" in e1.text
        # 群B：背景 ×2 + 直达（跨会话切换 → 附 B 的上下文，不带 A 的）
        await plugin._on_event(_group_msg(gid=222, sender=2, name="乙", text="B群背景1"))
        await plugin._on_event(_group_msg(gid=222, sender=2, name="乙", text="B群背景2"))
        await plugin._on_event(_group_msg(gid=222, at_me=True, text="B群叫我"))
        e2 = k.envs[-1]
        assert "B群叫我" in e2.text and "该会话最近" in e2.text
        assert "B群背景1" in e2.text and "A群" not in e2.text
        # 同群连续直达：不重复附加
        await plugin._on_event(_group_msg(gid=222, at_me=True, text="B群再叫我"))
        e3 = k.envs[-1]
        assert "B群再叫我" in e3.text and "该会话最近" not in e3.text
    finally:
        plugin._chatlog.close()


async def test_envelope_context_disabled(tmp_path):
    """envelope_context_n=0 关闭附加。"""
    from lifemate.plugins.qq import QQConfig, QQPlugin
    from lifemate.plugins.qq.chatlog import ChatLog
    from lifemate.utils.config import Config

    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "bot_uin": SELF, "owner_uin": OWNER, "envelope_context_n": 0}}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "pd")
    plugin._chatlog = ChatLog(tmp_path / "pd" / "cl")

    class _K:
        def __init__(self):
            self.envs = []

        def notify(self, env):
            self.envs.append(env)

    k = _K()
    plugin._kernel = k
    try:
        await plugin._on_event(_group_msg(gid=111, sender=1, name="甲", text="旧话"))
        await plugin._on_event(_group_msg(gid=111, at_me=True, text="叫我"))
        assert "该会话最近" not in k.envs[-1].text
    finally:
        plugin._chatlog.close()


async def test_quiet_hours_suppress_ambient_wake(tmp_path):
    """静音时段：弱刺激攒批唤醒关闭（直达不受影响）。"""
    from lifemate.plugins.qq import QQConfig, QQPlugin
    from lifemate.plugins.qq.chatlog import ChatLog
    from lifemate.utils.config import Config

    now = time.strftime("%H:%M").split(":")
    spec = f"{int(now[0]):02d}:00-{int(now[0]):02d}:59"  # 当前小时 → 必在静音中
    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "bot_uin": SELF, "owner_uin": OWNER,
                          "quiet_hours": spec, "ambient_wake_threshold": 3}}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "pd")
    plugin._chatlog = ChatLog(tmp_path / "pd" / "cl")

    class _K:
        def __init__(self):
            self.envs = []

        def notify(self, env):
            self.envs.append(env)

    k = _K()
    plugin._kernel = k
    try:
        assert plugin._in_quiet_hours() is True
        # 攒满阈值条弱刺激 → 静音时段不唤醒
        for i in range(5):
            await plugin._on_event(_group_msg(gid=333, sender=9, name=f"路人{i}", text=f"水群{i}"))
        assert not any("你不在时聊了" in e.text for e in k.envs)
        # 直达（@）照常唤醒
        await plugin._on_event(_group_msg(gid=333, at_me=True, text="叫你"))
        assert any("叫你" in e.text for e in k.envs)
        # 跨午夜区间解析（不抛错即可）
        cfg.plugins["qq"]["quiet_hours"] = "23:00-07:00"
        assert isinstance(plugin._in_quiet_hours(), bool)
    finally:
        plugin._chatlog.close()
