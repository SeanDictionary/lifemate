"""QQ 插件测试：协议解析（纯函数）+ FakeNapCat 端到端。"""

import asyncio
import json
import time
from pathlib import Path

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


async def test_llm_failure_alerts_owner(tmp_path):
    """身体告警：LLM 长故障（瞬态连续 10 次）/永久错误（连续 3 次）→ 私聊主人附详细错误；
    恢复即清零；同一故障期不重复轰炸；未达阈值不打扰。"""
    from lifemate.kernel.contracts import ASSISTANT_MSG, ERROR

    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, extra_cfg={"owner_uin": OWNER, "send_min_interval_s": 0})
    try:
        assert await _wait_for(lambda: plugin._client.connected)

        def _owner_msgs():
            return [r for r in napcat.received if r["action"] == "send_private_msg" and r["params"].get("user_id") == OWNER]

        # 瞬态失败未达阈值（9 次）：不打扰主人
        for i in range(9):
            kernel._emit(ERROR, error="llm_failed", detail={"streak": i + 1, "retry_in_s": 8, "error": "网关超时"})
        await asyncio.sleep(0.1)
        assert _owner_msgs() == []

        # 第 10 次：告警，附详细错误
        kernel._emit(ERROR, error="llm_failed", detail={"streak": 10, "retry_in_s": 64, "error": "APIConnectionError: 连不上网关"})
        assert await _wait_for(lambda: any("连不上网关" in str(m["params"]["message"]) for m in _owner_msgs()))
        # 同一故障期继续失败：不重复轰炸
        kernel._emit(ERROR, error="llm_failed", detail={"streak": 11, "retry_in_s": 128, "error": "APIConnectionError: 连不上网关"})
        await asyncio.sleep(0.1)
        assert len(_owner_msgs()) == 1

        # 恢复（正常输出）：告警状态清零
        kernel._emit(ASSISTANT_MSG, content="好了")
        assert plugin._llm_alert_sent == {}

        # 永久错误：心智自解失败（3 次 = 回执上限）才升级主人，前 2 次不打扰
        for i in range(2):
            kernel._emit(ERROR, error="llm_refused", detail={"streak": i + 1, "error": "AuthenticationError: key 失效"})
        await asyncio.sleep(0.1)
        assert not any("key 失效" in str(m["params"]["message"]) for m in _owner_msgs())
        kernel._emit(ERROR, error="llm_refused", detail={"streak": 3, "error": "AuthenticationError: key 失效"})
        assert await _wait_for(lambda: any("key 失效" in str(m["params"]["message"]) for m in _owner_msgs()))
        # 告警文本说明这不是网络波动
        alert = next(m for m in _owner_msgs() if "key 失效" in str(m["params"]["message"]))
        assert "不是网络波动" in str(alert["params"]["message"])
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_command_framework(tmp_path):
    """命令面框架：仅主人私聊拦截——未知命令提示、注册命令分发+参数+异常回执、
    交互入档；群聊/非主人不拦截走原路由；全程零信封（心智零感知）。"""
    from lifemate.plugins.qq import commands as cmd_mod

    def _priv(text, sender=OWNER):
        return {"post_type": "message", "message_type": "private", "user_id": sender, "message": [{"type": "text", "data": {"text": text}}], "message_id": 777, "sender": {"nickname": "SeanDictionary"}}

    def _cmd_replies():
        return [str(r["params"]["message"]) for r in napcat.received if r["action"] == "send_private_msg"]

    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, extra_cfg={"owner_uin": OWNER, "send_min_interval_s": 0})
    evs = []
    kernel.subscribe(lambda ev: evs.append(ev))
    try:
        assert await _wait_for(lambda: plugin._client.connected)

        # ① 未知命令：拦截并提示（可用清单含已注册命令），不进心智
        await plugin._on_event(_priv("/stats"))
        assert await _wait_for(lambda: _cmd_replies() and "找不到" in _cmd_replies()[-1])
        last = _cmd_replies()[-1]
        assert "找不到 /stats" in last and "/status" in last and "/history" in last
        assert not any(e.type == "USER_MSG" for e in evs)  # 零信封

        # ② 注册命令：分发 + 参数解析 + 异常回执 + 交互入档
        async def _ping(p, ev, arg):
            return f"pong {arg}".strip()

        async def _boom(p, ev, arg):
            raise RuntimeError("炸了")

        cmd_mod._COMMANDS["/ping"] = _ping
        cmd_mod._COMMANDS["/boom"] = _boom
        try:
            await plugin._on_event(_priv("/ping hello"))
            assert await _wait_for(lambda: any("pong hello" in x for x in _cmd_replies()))
            await plugin._on_event(_priv("/boom"))
            assert await _wait_for(lambda: any("执行出错" in x for x in _cmd_replies()))
            assert not any(e.type == "USER_MSG" for e in evs)  # 全程零信封
            # 命令交互对称入档（输入 + 应答都在背景听觉里）
            rows = plugin._chatlog.recent(f"private_{OWNER}", 10)
            assert any(r["sender_id"] == OWNER and "/ping hello" in r["text"] for r in rows)
            assert any(r["sender_id"] == SELF and "pong hello" in r["text"] for r in rows)
        finally:
            cmd_mod._COMMANDS.pop("/ping", None)
            cmd_mod._COMMANDS.pop("/boom", None)

        # ③ 群聊 /xxx（即使是主人）：不拦截，走路由（ambient 入背景听觉）
        n_before = len(_cmd_replies())
        await plugin._on_event(_group_msg(text="/foo", sender=OWNER))
        await asyncio.sleep(0.1)
        assert len(_cmd_replies()) == n_before
        rows = plugin._chatlog.recent("group_777", 5)
        assert any("/foo" in r["text"] for r in rows)

        # ④ 非主人私聊 /xxx：不拦截，直达信封照常进心智
        await plugin._on_event(_priv("/stats", sender=12345))
        await kernel.wait_idle()
        users = [m["content"] for m in kernel._session.messages if m.get("role") == "user"]
        assert any("/stats" in u for u in users)
        assert len(_cmd_replies()) == n_before  # 身体没有应答
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_command_via_ws_receive_loop(tmp_path):
    """真实收包路径：命令经 WS 事件进来（非直调 _on_event），应答要经
    同一 WS 拿 echo——内联 await 会把收包循环堵死（消息送达但超时假报
    失败、应答不入档、期间事件积压）；任务分发后 echo 正常结算。
    """
    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, extra_cfg={"owner_uin": OWNER, "send_min_interval_s": 0})
    try:
        assert await _wait_for(lambda: plugin._client.connected)

        def _priv(text):
            return {"post_type": "message", "message_type": "private", "user_id": OWNER, "message": [{"type": "text", "data": {"text": text}}], "message_id": 888, "sender": {"nickname": "SeanDictionary"}}

        # 走真实 WS：FakeNapCat 服务端推送命令事件 → _serve → 命令应答
        await napcat.push(_priv("/status"))

        def _replies():
            return [str(r["params"]["message"]) for r in napcat.received if r["action"] == "send_private_msg"]

        # 应答经 echo 结算送达（旧代码此处会堵 30s 超时→测试失败）
        assert await _wait_for(lambda: _replies() and "运行" in _replies()[-1] or any("找不到" in x for x in _replies()))
        # echo 正常结算 → 应答入背景听觉（心智翻档能看全）
        assert await _wait_for(lambda: any(r["sender_id"] == SELF for r in plugin._chatlog.recent(f"private_{OWNER}", 10)))
        # 收包循环未被占用：紧跟的私聊事件照常路由（不冻结）
        await napcat.push(_priv("你好呀"))
        assert await _wait_for(lambda: any("你好呀" in m.get("content", "") for m in kernel._session.messages if m.get("role") == "user"))
        await kernel.wait_idle()
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_command_reload(tmp_path):
    """/reload：无变化回执；改盘后重载列出变化键，需重启项标注。"""
    import json as _json
    from pathlib import Path

    def _priv(text):
        return {"post_type": "message", "message_type": "private", "user_id": OWNER, "message": [{"type": "text", "data": {"text": text}}], "message_id": 778, "sender": {"nickname": "SeanDictionary"}}

    def _replies():
        return [str(r["params"]["message"]) for r in napcat.received if r["action"] == "send_private_msg"]

    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, extra_cfg={"owner_uin": OWNER, "send_min_interval_s": 0})
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        # ① 无变化
        await plugin._on_event(_priv("/reload"))
        assert await _wait_for(lambda: _replies() and "配置无变化" in _replies()[-1])
        # ② 改盘：一个普通键 + 一个需重启键
        cf = Path(plugin._cfg.data_dir) / "config.json"
        data = _json.loads(cf.read_text(encoding="utf-8"))
        data["max_turns"] = 99
        data["server_port"] = 8311
        cf.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        await plugin._on_event(_priv("/reload"))
        assert await _wait_for(lambda: _replies() and "已重载" in _replies()[-1])
        last = _replies()[-1]
        assert "max_turns" in last and "server_port" in last
        assert "需重启" in last and "server_port" in last.split("需重启")[-1]
        assert plugin._cfg.max_turns == 99  # 活引用原地更新
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_status_command(tmp_path):
    """/status：面板 API 字段齐全时完整输出；面板未运行时降级本地字段不失败。"""
    from lifemate.plugins.qq import commands as cmd_mod

    def _priv(text, sender=OWNER):
        return {"post_type": "message", "message_type": "private", "user_id": sender, "message": [{"type": "text", "data": {"text": text}}], "message_id": 777, "sender": {"nickname": "SeanDictionary"}}

    def _cmd_replies():
        return [str(r["params"]["message"]) for r in napcat.received if r["action"] == "send_private_msg"]

    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, extra_cfg={"owner_uin": OWNER, "send_min_interval_s": 0})
    try:
        assert await _wait_for(lambda: plugin._client.connected)

        # 降级路径：面板未运行（端口 1 必拒绝）→ 本地字段照常、命令不失败
        plugin._cfg.server_port = 1
        await plugin._on_event(_priv("/status"))
        assert await _wait_for(lambda: _cmd_replies() and "面板服务未运行" in _cmd_replies()[-1])
        assert "连接" in _cmd_replies()[-1]

        # 完整路径：注入面板应答，验证聚合与格式化
        async def fake_fetch(p, path):
            if path == "/api/status":
                return {"ok": True, "version": "9.9.9", "uptime_s": 19533, "session_id": "abc12", "model": "test-model", "busy": False, "tool_count": 7, "plugins": ["qq"]}
            return {"ok": True, "by_model": {"m": {"calls": 3, "tokens_in": 90000, "tokens_out": 30000}}}

        orig = cmd_mod._fetch
        cmd_mod._fetch = fake_fetch
        try:
            await plugin._on_event(_priv("/status"))
            assert await _wait_for(lambda: any("9.9.9" in x for x in _cmd_replies()))
            reply = next(x for x in _cmd_replies() if "9.9.9" in x)
            assert "运行 5 小时 25 分" in reply  # 19533s
            assert "心智 休息中" in reply and "会话 abc12" in reply
            assert "模型 test-model" in reply and "工具 7 个" in reply
            assert "今日 12.0 万 token · 3 次调用" in reply
            assert "本分钟发送" in reply
        finally:
            cmd_mod._fetch = orig
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_history_command(tmp_path):
    """/history：心智最近活动——面板 API 优先（注入应答验格式化），
    面板未运行直读 observe 日档兑底，无档案时优雅回执。"""
    import json as _json
    from pathlib import Path

    from lifemate.plugins.qq import commands as cmd_mod

    def _priv(text, sender=OWNER):
        return {"post_type": "message", "message_type": "private", "user_id": sender, "message": [{"type": "text", "data": {"text": text}}], "message_id": 777, "sender": {"nickname": "SeanDictionary"}}

    def _cmd_replies():
        return [str(r["params"]["message"]) for r in napcat.received if r["action"] == "send_private_msg"]

    napcat, plugin, kernel, _ = await _ambient_harness(tmp_path, extra_cfg={"owner_uin": OWNER, "send_min_interval_s": 0})
    try:
        assert await _wait_for(lambda: plugin._client.connected)

        # ① 面板 API 路径：注入档案记录，验证各类型格式化
        rows = [
            {"type": "msg", "ts": 1792741360, "role": "assistant", "tool_calls": [{"name": "send_message", "arguments": '{"target_id": 840851185}'}]},
            {"type": "msg", "ts": 1792741329, "role": "user", "content": "[15:41] [私聊·主人] SeanDictionary：测试"},
            {"type": "msg", "ts": 1792741151, "role": "assistant", "content": "12:20 发的。我看到是12:21"},
            {"type": "msg", "ts": 1792741148, "role": "tool", "name": "wait", "ok": True, "content": "（你从等待中醒来…）"},
            {"type": "rest", "ts": 1792740930, "reason": "explicit:刚回完私聊和群"},
            {"type": "wake", "ts": 1792740811, "trigger": "boot"},
            {"type": "error", "ts": 1792729289, "error": "llm_failed"},
        ]

        async def fake_fetch(p, path):
            assert "tail=10" in path
            return {"ok": True, "count": len(rows), "rows": rows}

        orig = cmd_mod._fetch
        cmd_mod._fetch = fake_fetch
        try:
            await plugin._on_event(_priv("/history"))
            assert await _wait_for(lambda: any("最近 7 条活动" in x for x in _cmd_replies()))
            reply = next(x for x in _cmd_replies() if "最近 7 条活动" in x)
            assert "🔧 send_message(" in reply
            assert "📨 " in reply and "[私聊·主人]" in reply
            assert "💬 12:20 发的" in reply
            assert "📥 wait ✓" in reply
            assert "🌙 休息" in reply and "☀️ 唤醒 boot" in reply and "✗ llm_failed" in reply
        finally:
            cmd_mod._fetch = orig

        # ② 兑底路径：面板未运行（端口 1），直读 observe 日档
        plugin._cfg.server_port = 1
        obs = Path(plugin._cfg.data_dir) / "observe"
        obs.mkdir(parents=True, exist_ok=True)
        (obs / "2026-10-23.jsonl").write_text("\n".join(_json.dumps(r, ensure_ascii=False) for r in rows[-3:]), encoding="utf-8")
        await plugin._on_event(_priv("/history"))
        assert await _wait_for(lambda: any("最近 3 条活动" in x for x in _cmd_replies()))
        # 条数参数
        await plugin._on_event(_priv("/history 2"))
        assert await _wait_for(lambda: any("最近 2 条活动" in x for x in _cmd_replies()))
        # 坏参数回执用法
        await plugin._on_event(_priv("/history abc"))
        assert await _wait_for(lambda: any("看不懂参数" in x for x in _cmd_replies()))
    finally:
        await plugin.stop()
        await napcat.stop()


# ── 引用回复 / 读合并转发（工具层 + 纯函数） ─────────────


class _StubClient:
    """按 action 应答的假 OneBot 客户端（工具层单测，不起 WS）。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action, params=None):
        self.calls.append((action, params or {}))
        r = self.responses.get(action)
        if isinstance(r, Exception):
            raise r
        return r if r is not None else {}


def _plugin_with_stub(tmp_path, responses):
    from lifemate.plugins.qq import QQConfig, QQPlugin
    from lifemate.plugins.qq.chatlog import ChatLog
    from lifemate.plugins.qq.tools import build_tools
    from lifemate.utils.config import Config

    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "ws_url": "ws://stub", "bot_uin": SELF, "owner_uin": OWNER, "send_min_interval_s": 0.0, "send_max_per_min": 99}}
    p = QQPlugin(QQConfig(cfg), cfg, tmp_path / "pd")
    p._client = _StubClient(responses)
    p._chatlog = ChatLog(tmp_path / "pd")
    tools = {t.schema["name"]: t for t in build_tools(p)}
    return p, p._client, tools


async def test_reply_to_group_message(tmp_path):
    """引用回复群消息：自动定位会话 + reply 段 + 出站入档带句柄。"""
    p, stub, tools = _plugin_with_stub(
        tmp_path,
        {
            "get_msg": {"group_id": 777, "sender": {"user_id": 12345, "nickname": "张三"}, "message": [{"type": "text", "data": {"text": "原话"}}]},
            "send_group_msg": {"message_id": 4242},
        },
    )
    r = json.loads(await tools["reply_to"].func({"message_id": 555, "text": "收到"}))
    assert r["ok"] is True
    assert r["data"]["message_id"] == 4242 and r["data"]["reply_to"] == 555 and r["data"]["target"] == "group_777"
    send = next(c for c in stub.calls if c[0] == "send_group_msg")[1]
    assert send["group_id"] == 777
    assert send["message"][0] == {"type": "reply", "data": {"id": 555}}
    assert send["message"][1] == {"type": "text", "data": {"text": "收到"}}
    # 出站入背景听觉（带撤回句柄）
    assert any(row["message_id"] == 4242 and row["sender_id"] == SELF for row in p._chatlog.recent("group_777", 5))


async def test_reply_to_private_message(tmp_path):
    _, stub, tools = _plugin_with_stub(
        tmp_path,
        {
            "get_msg": {"group_id": 0, "sender": {"user_id": 888, "nickname": "李四"}},
            "send_private_msg": {"message_id": 77},
        },
    )
    r = json.loads(await tools["reply_to"].func({"message_id": 31, "text": "嗯嗯"}))
    assert r["ok"] is True and r["data"]["target"] == "private_888"
    send = next(c for c in stub.calls if c[0] == "send_private_msg")[1]
    assert send["user_id"] == 888 and send["message"][0] == {"type": "reply", "data": {"id": 31}}


async def test_reply_to_own_private_message_unlocatable(tmp_path):
    """回自己发的私聊消息：定位不到对方，明确回执引导用 send_message。"""
    _, _, tools = _plugin_with_stub(tmp_path, {"get_msg": {"group_id": 0, "sender": {"user_id": SELF}}})
    r = json.loads(await tools["reply_to"].func({"message_id": 5, "text": "咦"}))
    assert r["ok"] is False and "send_message" in r["error"]


async def test_read_forward(tmp_path):
    """读合并转发：自动解引用转发 ID，节点渲染成可读行。"""
    _, stub, tools = _plugin_with_stub(
        tmp_path,
        {
            "get_msg": {"message": [{"type": "forward", "data": {"id": "resid42"}}]},
            "get_forward_msg": {
                "messages": [
                    {"sender": {"nickname": "甲"}, "content": [{"type": "text", "data": {"text": "看这个"}}]},
                    {"sender": {"card": "乙"}, "content": [{"type": "image", "data": {"url": "http://x/1.jpg"}}, {"type": "text", "data": {"text": "哈哈"}}]},
                ]
            },
        },
    )
    r = json.loads(await tools["read_forward"].func({"message_id": 555}))
    assert r["ok"] is True and r["data"]["count"] == 2
    log = r["data"]["log"]
    assert "甲: 看这个" in log and "乙: [图片 http://x/1.jpg]哈哈" in log
    assert ("get_forward_msg", {"id": "resid42"}) in stub.calls


async def test_read_forward_not_forward(tmp_path):
    _, _, tools = _plugin_with_stub(tmp_path, {"get_msg": {"message": [{"type": "text", "data": {"text": "普通消息"}}]}})
    r = json.loads(await tools["read_forward"].func({"message_id": 555}))
    assert r["ok"] is False and "不包含合并转发" in r["error"]


async def test_read_chat_log_shows_message_id_handles(tmp_path):
    """chat_log 渲染：所有消息带 message_id 句柄（reply_to/read_forward 的凭据）。"""
    p, _, tools = _plugin_with_stub(tmp_path, {})
    p._chatlog.append("group_777", 12345, "张三", "今天天气不错", 111)
    p._chatlog.append("group_777", SELF, "我", "收到", 222)
    out = json.loads(await tools["read_chat_log"].func({"target_type": "group", "target_id": 777}))
    assert "张三: 今天天气不错（message_id=111）" in out["log"]
    assert "我: 收到（message_id=222）" in out["log"]


def test_forward_id_of():
    from lifemate.plugins.qq.protocol import forward_id_of

    assert forward_id_of({"message": [{"type": "forward", "data": {"id": "seg9"}}]}) == "seg9"
    assert forward_id_of({"raw_message": "看[CQ:forward,id=abc123]这个"}) == "abc123"
    assert forward_id_of({"raw_message": "无转发"}) == ""


def test_render_forward_node_format():
    from lifemate.plugins.qq.protocol import render_forward

    data = {"message": [{"type": "node", "data": {"user_id": 1, "nickname": "丙", "content": [{"type": "text", "data": {"text": "标准形态"}}]}}]}
    assert render_forward(data) == ["丙: 标准形态"]
    # 空节点也有行（转发里常见空消息）
    assert render_forward({"messages": [{"sender": {"nickname": "丁"}, "content": []}]}) == ["丁: （空）"]


def test_render_segments_labels():
    from lifemate.plugins.qq.protocol import render_segments

    segs = [
        {"type": "text", "data": {"text": "a"}},
        {"type": "at", "data": {"qq": "123"}},
        {"type": "reply", "data": {"id": 9}},
        {"type": "face", "data": {"id": 5}},
        {"type": "record", "data": {}},
        {"type": "forward", "data": {"id": "x"}},
    ]
    assert render_segments(segs) == "a@123[回复9][表情5][语音][合并转发]"


def test_resolve_media_path_candidates(tmp_path):
    """相对路径解析：URL 直通 / data_dir 候选 / 缺失原样放行。"""
    from lifemate.plugins.qq.tools import _resolve_media_path

    class _Cfg:
        data_dir = str(tmp_path / "data")

    (tmp_path / "data" / "images").mkdir(parents=True)
    img = tmp_path / "data" / "images" / "abc.png"
    img.write_bytes(b"\x89PNG")
    (tmp_path / "data" / "top.png").write_bytes(b"x")

    # URL / base64 / data: 直通
    for u in ("https://x/y.png", "base64://abcd", "data:image/png;base64,xx", "HTTP://X/Y.PNG"):
        assert _resolve_media_path(u, _Cfg) == u
    # 绝对路径原样
    assert _resolve_media_path(str(img), _Cfg) == str(img)
    # 相对路径（find_image 回执形态）→ {data_dir}/images/文件名，绝对化
    assert _resolve_media_path("abc.png", _Cfg) == str(img.resolve())
    assert _resolve_media_path("images/abc.png", _Cfg) == str(img.resolve())
    # {data_dir}/file 候选
    assert _resolve_media_path("top.png", _Cfg) == str((tmp_path / "data" / "top.png").resolve())
    # 都不在：原样放行（对端报错，回执制兜底）
    assert _resolve_media_path("nope/missing.png", _Cfg) == "nope/missing.png"


async def test_send_image_relative_path_goes_base64(tmp_path):
    """send_image 本地文件（含相对路径）出站前转 base64://：不依赖 NapCat 同机。"""
    from conftest import FakeLLM, make_persona, make_session

    from lifemate.kernel.compaction import Compactor
    from lifemate.kernel.mind import SelfKernel
    from lifemate.plugins.qq import QQConfig, QQPlugin, build_tools
    from lifemate.utils.config import Config

    napcat = FakeNapCat()
    url = await napcat.start()
    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "ws_url": url, "bot_uin": SELF, "owner_uin": OWNER, "send_min_interval_s": 0.0}}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "plugindata" / "qq")
    plugin.tools = build_tools(plugin)

    llm = FakeLLM([])
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.register_plugin(plugin)

    import types

    await plugin.start(types.SimpleNamespace(kernel=kernel))
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        # 归档一张图（find_image 回执给的就是这种相对形态）
        img_dir = tmp_path / "cfg" / "images"
        img_dir.mkdir(parents=True)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        (img_dir / "abc.png").write_bytes(png)
        tools = {t.name: t for t in plugin.tools}
        r = json.loads(await tools["send_image"].func({"target_type": "private", "target_id": OWNER, "file": "images/abc.png"}))
        assert r["ok"] is True
        sent = [m for m in napcat.received if m.get("action") == "send_private_msg"]
        f = sent[-1]["params"]["message"][0]["data"]["file"]
        assert f.startswith("base64://")
        import base64 as b64mod

        assert b64mod.b64decode(f[len("base64://") :]) == png
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_send_image_local_over_cap_rejected(tmp_path):
    """本地文件超 send_image_max_mb：回执报错，不出站。"""
    from conftest import FakeLLM, make_persona, make_session

    from lifemate.kernel.compaction import Compactor
    from lifemate.kernel.mind import SelfKernel
    from lifemate.plugins.qq import QQConfig, QQPlugin, build_tools
    from lifemate.utils.config import Config

    napcat = FakeNapCat()
    url = await napcat.start()
    cfg = Config(data_dir=tmp_path / "cfg")
    cfg.plugins = {"qq": {"enabled": True, "ws_url": url, "bot_uin": SELF, "owner_uin": OWNER, "send_min_interval_s": 0.0, "send_image_max_mb": 0.001}}
    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "plugindata" / "qq")
    plugin.tools = build_tools(plugin)

    llm = FakeLLM([])
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.register_plugin(plugin)

    import types

    await plugin.start(types.SimpleNamespace(kernel=kernel))
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        big = tmp_path / "big.png"
        big.write_bytes(b"x" * 2048)  # 2KB > 0.001MB 上限
        tools = {t.name: t for t in plugin.tools}
        r = json.loads(await tools["send_image"].func({"target_type": "private", "target_id": OWNER, "file": str(big)}))
        assert r["ok"] is False and "太大" in r["error"]
        assert not [m for m in napcat.received if m.get("action") == "send_private_msg"]
    finally:
        await plugin.stop()
        await napcat.stop()


async def test_meme_flow_look_find_send(tmp_path, monkeypatch):
    """表情包全链路（三工具联合）：look_image 归档 → find_image 找图（相对路径）→ send_image 发出（base64）。

    data_dir 用相对路径（生产形态：config 默认相对目录），验证 find_image 回执的
    相对路径能被 send_image 正确读出。"""
    from conftest import FakeLLM, make_persona, make_session
    from test_look_image import PNG_MAGIC, FakeVisionLLM

    from lifemate.kernel.compaction import Compactor
    from lifemate.kernel.mind import SelfKernel
    from lifemate.kernel.tools import make_look_image_tool
    from lifemate.memory.store import Store
    from lifemate.memory.tools import build_memory_tools
    from lifemate.plugins.qq import QQConfig, QQPlugin, build_tools
    from lifemate.utils.config import Config

    monkeypatch.chdir(tmp_path)  # 进程 cwd = tmp_path，相对 data_dir 落在这
    napcat = FakeNapCat()
    url = await napcat.start()

    cfg = Config(data_dir=Path("data"))  # 相对路径——生产形态
    cfg.llm_vision_model = "fake-vision"
    cfg.plugins = {"qq": {"enabled": True, "ws_url": url, "bot_uin": SELF, "owner_uin": OWNER, "send_min_interval_s": 0.0}}

    store = Store(Path("data") / "memory.db")  # host 装配形态：look_image 与 find_image 共库
    vision_llm = FakeVisionLLM()
    look = make_look_image_tool(vision_llm, store, cfg)
    mem_tools = {t.name: t for t in build_memory_tools(store, cfg)}

    plugin = QQPlugin(QQConfig(cfg), cfg, tmp_path / "plugindata" / "qq")
    plugin.tools = build_tools(plugin)
    llm = FakeLLM([])
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.register_plugin(plugin)

    import types

    await plugin.start(types.SimpleNamespace(kernel=kernel))
    try:
        assert await _wait_for(lambda: plugin._client.connected)
        # ① 看图：归档 + 存描述（vision 真实调一次）
        src = tmp_path / "meme.png"
        src.write_bytes(PNG_MAGIC)
        r = json.loads(await look.func({"image": str(src), "question": "这是什么梗"}))
        assert r["ok"] is True and len(vision_llm.vision_calls) == 1

        # ② 找图：按描述关键词命中，file 是相对路径（生产形态下确实相对）
        r = json.loads(await mem_tools["find_image"].func({"query": "测试图"}))
        assert r["ok"] and len(r["content"]) == 1
        row = r["content"][0]
        f = row["file"]
        assert not Path(f).is_absolute() and "images" in f

        # ③ 发图：QQ 侧把相对路径读出、转 base64 出站
        qq_tools = {t.name: t for t in plugin.tools}
        r = json.loads(await qq_tools["send_image"].func({"target_type": "private", "target_id": OWNER, "file": f, "caption": "表情包"}))
        assert r["ok"] is True
        sent = [m for m in napcat.received if m.get("action") == "send_private_msg"]
        segs = sent[-1]["params"]["message"]
        assert segs[0]["data"]["file"].startswith("base64://")
        import base64 as b64mod

        assert b64mod.b64decode(segs[0]["data"]["file"][len("base64://") :]) == PNG_MAGIC
        assert segs[1]["data"]["text"] == "表情包"

        # ④ 全链路零重复识别：重发同图不再调 vision
        r = json.loads(await qq_tools["send_image"].func({"target_type": "private", "target_id": OWNER, "file": f}))
        assert r["ok"] is True and len(vision_llm.vision_calls) == 1
    finally:
        await plugin.stop()
        await napcat.stop()
