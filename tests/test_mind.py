"""思维循环语义测试（永续回合：唤醒/休息/插话/打断/闹钟/转身/恢复）。"""

import asyncio
import time

from conftest import FakeLLM, make_config, make_persona, make_session, text_reply, tool_reply

from lifemate.kernel.compaction import Compactor
from lifemate.kernel.contracts import AMBIENT, DIRECT, QUEUE_DROP, REST, WAKE, Envelope, LLMResponse, Tool
from lifemate.kernel.mind import SelfKernel
from lifemate.kernel.persona import Persona
from lifemate.kernel.session import Session


def make_tool(name: str, result: str = '{"ok": true}', delay: float = 0.0, interruptible: bool = False) -> Tool:
    async def _fn(args: dict) -> str:
        if delay:
            await asyncio.sleep(delay)
        return result

    return Tool(schema={"name": name, "description": f"test tool {name}", "inputSchema": {"type": "object", "properties": {}}}, func=_fn, interruptible=interruptible)


def make_kernel(tmp_path, llm, *, tools: list[Tool] | None = None, session: Session | None = None, persona: Persona | None = None) -> SelfKernel:
    cfg = make_config(tmp_path)
    k = SelfKernel(
        llm=llm,  # type: ignore[arg-type]
        persona=persona or make_persona(tmp_path),
        session=session or make_session(tmp_path),
        compactor=Compactor(llm.chat, cfg),
        cfg=cfg,
    )
    for t in tools or []:
        k.register_tool(t)
    return k


def events_of(kernel: SelfKernel) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    kernel.subscribe(lambda ev: out.append((ev.type, ev.data)))
    return out  # type: ignore[return-value]


async def wait_event(evs, type_: str, reason: str | None = None, timeout: float = 3.0) -> tuple[str, dict]:
    """轮询等待某事件出现。"""

    async def _poll():
        while True:
            for item in evs:
                if item[0] == type_ and (reason is None or item[1].get("reason") == reason):
                    return item
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout=timeout)


# ── 基础：唤醒 → 思考 → 休息 → 再唤醒 ──────────────────


async def test_wake_extras_appended_and_isolated(tmp_path):
    """身体感受：从 wait 醒来时多注册拼接、空串跳过、单个异常不拖垮其他。"""
    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 0.05, "reason": "小睡"}', cid="c1"),
            text_reply("醒来了"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)

    def _good() -> str:
        return "（你还有 2 条待办提醒。）"

    def _bad() -> str:
        raise RuntimeError("身体感受坏了")

    def _empty() -> str:
        return ""

    k.add_wake_extras(_good)
    k.add_wake_extras(_bad)
    k.add_wake_extras(_empty)
    k.notify(Envelope(text="设个闹钟小睡"))
    await wait_event(evs, REST, reason="converged")
    # 唤醒信封（wait 工具结果）里有好的那条；坏的被吞；唤醒正常完成
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert "2 条待办提醒" in tool_msgs[0]["content"]
    assert k.is_busy is False


async def test_event_wake_supersedes_alarm_intent(tmp_path):
    """外部事件唤醒取代未到点的闹钟：被取代的意图还给她自决。"""
    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 60, "reason": "潜水半小时"}', cid="c1"),
            text_reply("有人找我，那不潜了"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="去潜水"))
    await wait_event(evs, REST, reason="explicit:潜水半小时")
    # 60s 闹钟未到，事件先到：顶掉闹钟，唤醒信封要告知
    k.notify(Envelope(text="有人@你"))
    await k.wait_idle()
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert any("已被这次唤醒取代" in m["content"] and "潜水半小时" in m["content"] for m in tool_msgs)
    # 裸 wait（无超时，无未来承诺）被事件唤醒时不附注
    llm.script.append(tool_reply("wait", args='{"reason": "等真有话再说"}', cid="c2"))
    llm.script.append(text_reply("继续等"))
    k.notify(Envelope(text="再来一事"))
    await wait_event(evs, REST, reason="explicit:等真有话再说")
    k.notify(Envelope(text="又有人@你"))
    await k.wait_idle()
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert not any("已被这次唤醒取代" in m["content"] and "等真有话再说" in m["content"] for m in tool_msgs)


async def test_wait_expiry_returns_intent(tmp_path):
    """自然到期：意图在到点时还给她——"等这个是为了什么"正是最该想起的事。"""
    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 0.05, "reason": "等她回话，到点主动问一句"}', cid="c1"),
            text_reply("醒了"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="去休息"))
    await wait_event(evs, REST, reason="explicit:等她回话，到点主动问一句")  # wait 工具已挂起
    await wait_event(evs, REST, reason="converged")  # 到期→思考→收敛休息
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert any("到点了" in m["content"] and "等她回话" in m["content"] for m in tool_msgs)


async def test_do_wait_touches_activity_clock(tmp_path):
    """等待结束即刷新单调钟：长睡到期的瞬间关闭 watchdog 误报窗口。

    回归：长于 max_run_s 的 wait 自然到期后、首次模型响应前，
    heal() 曾因活动时间陈旧被误判 think_stuck 而中止自愈。
    """
    llm = FakeLLM([text_reply("x")])
    k = make_kernel(tmp_path, llm)
    k._last_activity_mono = time.monotonic() - (k._cfg.max_run_s + 100)
    await k._do_wait(0.05, "explicit", intent="小睡")
    assert time.monotonic() - k._last_activity_mono < 5  # 已刷新（改前仍是陈旧值）


async def test_notify_wakes_thinks_rests(tmp_path):
    llm = FakeLLM([text_reply("我醒了")])
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="（你醒了）", kind="boot"))
    await k.wait_idle()
    assert k.is_busy is False  # 休息中
    types = [t for t, _ in evs]
    assert types[0] == WAKE and REST in types
    assert evs[-1][0] == REST and evs[-1][1]["reason"] == "converged"
    # 永续：第二声敲门 → 新唤醒周期
    llm.script.append(text_reply("又醒了"))
    k.notify(Envelope(text="第二件事"))
    await k.wait_idle()
    assert sum(1 for t, _ in evs if t == WAKE) == 2
    assert any("第二件事" in str(m.get("content")) for m in k._session.messages)


# ── 插话：工具批次后、下次模型调用前送达 ────────────────


async def test_steer_during_tool(tmp_path):
    llm = FakeLLM([tool_reply("slow", cid="c1"), text_reply("收到插话")])
    slow = make_tool("slow", delay=0.2)
    k = make_kernel(tmp_path, llm, tools=[slow])
    k.notify(Envelope(text="先干个活", kind="ambient"))
    await asyncio.sleep(0.05)  # 等工具开始执行
    k.notify(Envelope(text="插话：主人补充了一句", kind="ambient"))
    await k.wait_idle()
    # 第二次模型调用前插话已进上下文
    assert any("主人补充" in str(m.get("content")) for m in llm.calls[1])


# ── 强刺激反射性打断可打断工具 ─────────────────────────


async def test_strong_stimulus_interrupts_interruptible_tool(tmp_path):
    llm = FakeLLM([tool_reply("long", cid="c1"), text_reply("先回主人")])
    long_tool = make_tool("long", delay=3.0, interruptible=True)
    k = make_kernel(tmp_path, llm, tools=[long_tool])
    k.notify(Envelope(text="开始长任务", kind="ambient"))
    await asyncio.sleep(0.05)
    k.notify(Envelope(text="主人的消息", kind=DIRECT, source="private_1"))
    await k.wait_idle()
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert "打断" in tool_msgs[0]["content"] and "未完成" in tool_msgs[0]["content"]
    # 主人消息随后送达（插话，带到达时间前缀）
    assert any(str(m.get("content")).endswith("主人的消息") and str(m.get("content")).startswith("[") for m in k._session.messages if m.get("role") == "user")
    # 总耗时远小于 3s（被打断）
    assert llm.calls  # 第二次调用已发生


async def test_uninterruptible_tool_not_cancelled(tmp_path):
    llm = FakeLLM([tool_reply("atomic", cid="c1"), text_reply("做完了")])
    atomic = make_tool("atomic", delay=0.2, interruptible=False)
    k = make_kernel(tmp_path, llm, tools=[atomic])
    k.notify(Envelope(text="原子任务", kind="ambient"))
    await asyncio.sleep(0.05)
    k.notify(Envelope(text="主人的消息", kind=DIRECT))
    await k.wait_idle()
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert "打断" not in tool_msgs[0]["content"]  # 正常完成


# ── wait 工具：自己定闹钟，唤醒作为工具结果同回合延续 ──


async def test_explicit_wait_returns_wake_as_tool_result(tmp_path):
    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 0.05, "reason": "小睡"}', cid="c1"),
            text_reply("闹钟醒了，继续"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="设个闹钟小睡"))
    # 显式 wait 期间 wait_idle 会提前返回——轮询等收敛休息
    await wait_event(evs, REST, reason="converged")
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert "醒来" in tool_msgs[0]["content"]  # 工具结果=唤醒信封
    assert len(llm.calls) == 2  # 同回合延续：醒后继续思考
    rests = [d for t, d in evs if t == REST]
    assert any(d["reason"].startswith("explicit") for d in rests)


# ── 组批唤醒：等待期间多个事件一次进入同一视野 ──────────


async def test_wake_batches_events(tmp_path):
    llm = FakeLLM([text_reply("看到了全部"), text_reply("处理完")])
    k = make_kernel(tmp_path, llm)
    k.notify(Envelope(text="第一件"))  # 周期1 开始
    await asyncio.sleep(0.05)  # 等它收敛进入休息
    # 同一事件循环节拍内投两封 → 组批进同一唤醒信封
    k.notify(Envelope(text="事件A", kind="ambient"))
    k.notify(Envelope(text="事件B", kind="ambient"))
    await k.wait_idle()
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    wake_msgs = [u for u in users if "期间收到" in u]
    assert wake_msgs and "事件A" in wake_msgs[0] and "事件B" in wake_msgs[0]


# ── 防不睡：轮次超限强制休息 ────────────────────────────


async def test_breaker_after_max_turns(tmp_path):
    """熔断：正常思考只有生理提醒（不强制）；到熔断阈值才拦（异常保护）。"""
    llm = FakeLLM([tool_reply("echo", cid=f"c{i}") for i in range(5)])  # 每轮都是工具调用
    llm.script.append(text_reply("被熔断后继续"))
    k = make_kernel(tmp_path, llm, tools=[make_tool("echo")])
    k._cfg.max_turns = 4  # 熔断阈值
    k._cfg.turns_rest_nudge = 2  # 每 2 轮生理提醒
    evs = events_of(k)
    k.notify(Envelope(text="无限循环吧"))
    await wait_event(evs, REST, reason="breaker")
    # 熔断前只有生理提醒，没有强制休息；提醒周期性出现
    nudges = [m for m in k._session.messages if m.get("role") == "user" and "身体提醒" in str(m.get("content"))]
    assert len(nudges) >= 1
    assert not any(d.get("reason") == "forced" for t, d in evs if t == REST)
    # 熔断也是休息——敲门后继续
    k.notify(Envelope(text="继续"))
    await k.wait_idle()
    assert any(d.get("reason") == "breaker" for t, d in evs if t == REST)


# ── 深睡转身：swap_session + rest ──────────────────────


async def test_session_messages_carry_ts_stripped_for_api(tmp_path):
    """消息时间戳：user/assistant/tool 全带、持久化可恢复、不进 API。"""
    llm = FakeLLM([tool_reply("调用", cid="c1"), text_reply("好")])
    probe = make_tool("probe", '{"ok": true}')
    k = make_kernel(tmp_path, llm, tools=[probe])
    k.notify(Envelope(text="几点了"))
    await k.wait_idle()
    msgs = k._session.messages
    roles = {m["role"] for m in msgs}
    assert {"user", "assistant", "tool"} <= roles
    assert all(m.get("ts") for m in msgs)  # 全类型带时间戳
    api = k._build_messages()
    assert all("ts" not in m for m in api)  # API 序列剥离
    # 重启恢复：时间戳随 JSONL 带回
    restored = Session.load_latest(k._session.file.parent)
    assert [m.get("ts") for m in restored.messages] == [m.get("ts") for m in msgs]


def test_compaction_span_roundtrip(tmp_path):
    """压缩摘要时间范围：入文件、可恢复、进 API 摘要行。"""
    cfg = make_config(tmp_path)
    chat = FakeLLM([text_reply("摘要")]).chat
    comp = Compactor(chat, cfg)
    dropped = [{"role": "user", "content": "a", "ts": 1_000_000.0}, {"role": "assistant", "content": "b", "ts": 1_000_100.0}]
    span = comp._span_of(dropped)
    assert "至" in span and "ts" not in span
    assert comp._span_of([{"role": "user", "content": "旧格式"}]) == ""
    s = make_session(tmp_path)
    s.append({"role": "user", "content": "留下的", "ts": 1_000_200.0})
    s.apply_compaction("滚动摘要", s.messages, span=span)
    restored = Session.load_latest(s.file.parent)
    assert restored.span == span
    req = restored.build_request()
    assert span in req[0]["content"] and "滚动摘要" in req[0]["content"]


async def test_swap_session_seeds_new_session(tmp_path):
    llm = FakeLLM([text_reply("旧会话的一天"), text_reply("新会话的我")])
    k = make_kernel(tmp_path, llm)
    k.notify(Envelope(text="旧会话内容"))
    await k.wait_idle()
    old_id = k.session_id
    llm.script.append(text_reply("新会话的我"))
    k.swap_session("（播种：你是大肥鱼，刚从深睡中醒来……）")
    k.notify(Envelope(text="唤醒"))
    await k.wait_idle()
    assert k.session_id != old_id
    msgs = k._session.messages
    assert "播种" in msgs[0]["content"] and msgs[0]["role"] == "user"
    assert "现在是" in msgs[0]["content"]  # 播种带时间头（新我知道昼夜）


async def test_deep_sleep_rotation_at_cycle_boundary(tmp_path):
    """deep_sleep 纯转身：回执落旧会话（无孤儿 tool 消息）、周期边界轮换、
    新会话从信开始，睡觉由新自己决定。"""
    from lifemate.kernel.contracts import SESSION_ROTATED
    from lifemate.kernel.tools import make_deep_sleep_tool
    from lifemate.memory.store import Store

    llm = FakeLLM(
        [
            tool_reply("deep_sleep", args='{"letter": "我是大肥鱼，今天过得平静"}'),
            text_reply("读完信了，挂长觉睡到明早"),
        ]
    )
    store = Store(tmp_path / "m.db")
    k = make_kernel(tmp_path, llm)
    k.register_tool(make_deep_sleep_tool(k, store))
    old_id = k.session_id
    evs = events_of(k)
    k.notify(Envelope(text="一天结束了"))
    await wait_event(evs, REST, reason="converged")  # 新会话读完信收敛休息
    # 信已存档 + 会话已轮换 + 新会话从信开始
    assert "大肥鱼" in (store.latest_packet() or {}).get("content", "")
    assert any(t == SESSION_ROTATED for t, _ in evs)
    assert k.session_id != old_id
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    assert any("留下的信" in u for u in users)
    # 回执落在旧会话：新会话无孤儿 tool 消息（tool 消息必有同会话的 assistant tool_calls）
    answered = {m.get("tool_call_id") for m in k._session.messages if m.get("role") == "tool"}
    calls = {tc.get("id") for m in k._session.messages if m.get("role") == "assistant" for tc in m.get("tool_calls") or []}
    assert answered <= calls


# ── 崩溃恢复：悬空工具调用修复 ─────────────────────────


async def test_dangling_tool_call_repaired(tmp_path):
    sess = make_session(tmp_path)
    sess.append({"role": "user", "content": "hi"})
    sess.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "record_episode", "arguments": "{}"}}]})
    llm = FakeLLM([text_reply("续上")])
    make_kernel(tmp_path, llm, session=sess)  # 装配本身触发悬挂 tool_calls 的修复回执
    msgs = sess.messages
    assert msgs[-1]["role"] == "tool" and "重启" in msgs[-1]["content"]


# ── 背压（仅插话队列） ─────────────────────────────────


async def test_steer_backpressure(tmp_path):
    llm = FakeLLM([text_reply("嗯")])
    k = make_kernel(tmp_path, llm)
    k._cfg.queue_max_steer = 3
    drops: list[dict] = []
    k.subscribe(lambda ev: drops.append(ev.data) if ev.type == QUEUE_DROP else None)
    for i in range(6):
        k._steer_q.append(Envelope(text=f"信封{i}"))
    k._trim_steer()
    assert len(k._steer_q) == 3
    assert [e.text for e in k._steer_q] == ["信封3", "信封4", "信封5"]
    assert drops and drops[0]["reason"] == "overflow_steer"


# ── 压缩（沿用） ───────────────────────────────────────


async def test_compaction_triggers_and_persists(tmp_path):
    big = "x" * 400
    llm = FakeLLM(
        [
            tool_reply("echo", cid="c1"),
            LLMResponse(content="（这是压缩摘要）"),
            text_reply("继续"),
        ]
    )
    session = make_session(tmp_path)
    cfg = make_config(tmp_path)
    cfg.compact_threshold_tokens = 50
    cfg.keep_recent_tokens = 40
    k = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=session, compactor=Compactor(llm.chat, cfg), cfg=cfg)
    k.register_tool(make_tool("echo"))
    k.notify(Envelope(text=f"触发压缩{big}"))
    await k.wait_idle()
    assert session.summary == "（这是压缩摘要）"
    reloaded = Session.load_latest(session.file.parent)
    assert reloaded.summary == "（这是压缩摘要）"


# ── 重启连续性 ────────────────────────────────────────


async def test_session_restart_continuity(tmp_path):
    s1 = make_session(tmp_path)
    llm = FakeLLM([text_reply("好")])
    k = make_kernel(tmp_path, llm, session=s1)
    k.notify(Envelope(text="今天聊过的话题"))
    await k.wait_idle()
    s2 = Session.load_latest(s1.file.parent)
    assert any("今天聊过的话题" in str(m.get("content")) for m in s2.messages)


async def test_envelope_origin_in_wake_and_archive(tmp_path):
    """信封溯源：origin 贯穿唤醒组批标签与观测档案。"""
    llm = FakeLLM([text_reply("嗯")])
    k = make_kernel(tmp_path, llm)
    k.notify(Envelope(text="第一件", kind="boot", origin="host"))
    await k.wait_idle()
    k.notify(Envelope(text="来自控制台", kind="direct", origin="console"))
    await k.wait_idle()
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    wake = [u for u in users if "期间收到" in u]
    assert wake and "[console·direct]" in wake[0]


async def test_llm_transient_failure_silent_retry(tmp_path):
    """LLM 瞬态故障：静默重试不进上下文——过程信息（故障/重试/醒来噪声）
    一律不投递，输入保留，恢复后直接续完（心智无感）。"""
    from lifemate.kernel.contracts import ERROR
    from lifemate.kernel.llm import BadGatewayBody

    class FlakyLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls <= 2:
                raise BadGatewayBody("网关返回非标准响应体")
            return LLMResponse(content="恢复了，继续处理")

    llm = FlakyLLM()
    k = make_kernel(tmp_path, llm)
    k._cfg.llm_retry_backoff_s = 0.05  # 测试加速：退避 0.1/0.2s
    evs = events_of(k)
    k.notify(Envelope(text="做点事"))
    await wait_event(evs, REST, reason="llm_retry", timeout=10)
    assert any(t == ERROR and d.get("error") == "llm_failed" for t, d in evs)  # 错误已记档

    # 退避到点自动醒 → LLM 恢复 → 继续处理未完成的输入（轮询到收敛）
    async def _done():
        assistants = [m["content"] for m in k._session.messages if m.get("role") == "assistant"]
        return "恢复了，继续处理" in assistants

    async def _poll():
        while not await _done():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_poll(), timeout=15)
    assert not any(t == ERROR and d.get("error") == "live_crashed" for t, d in evs)  # 没崩循环
    # 过程信息零进上下文：没有故障说明、没有退避醒来的空噪声信封
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    assert users == ["做点事"]


async def test_llm_permanent_failure_receipt_enters_context(tmp_path):
    """永久错误（如内容被拒/鉴权失败）：是结果不是过程——回执进上下文，
    处置权在心智；心智调整后重试成功，不需要人介入。"""
    from lifemate.kernel.contracts import ERROR

    class SensitiveRefused(Exception):
        pass

    class RefusingLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise SensitiveRefused("内容含敏感信息，被服务方拒绝")
            return LLMResponse(content="那我换个说法")

    llm = RefusingLLM()
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="说点敏感的"))
    await k.wait_idle()
    # 回执进了上下文，说明结果与求助选项
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    receipts = [u for u in users if u.startswith("（系统回执：")]
    assert receipts and "敏感信息" in receipts[0] and "主人" in receipts[0]
    assert any(t == ERROR and d.get("error") == "llm_refused" for t, d in evs)
    assert any(m["content"] == "那我换个说法" for m in k._session.messages if m.get("role") == "assistant")
    assert k._refuse_streak == 0  # 成功后清零


async def test_llm_content_filter_receipt(tmp_path):
    """输出被内容过滤拦截（有响应无内容）：同样是结果——回执给心智自决。"""
    llm = FakeLLM([LLMResponse(content="", stop_reason="content_filter"), text_reply("好吧，我换种方式说")])
    k = make_kernel(tmp_path, llm)
    k.notify(Envelope(text="说点什么"))
    await k.wait_idle()
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    receipts = [u for u in users if u.startswith("（系统回执：")]
    assert receipts and "内容过滤" in receipts[0]
    assert any(m["content"] == "好吧，我换种方式说" for m in k._session.messages if m.get("role") == "assistant")


async def test_llm_refuse_cap_stops_repeating_receipts(tmp_path):
    """连续被拒超过阈值：不再重复回执（心智已知情），改退避重试防烧 token。"""
    from lifemate.kernel.contracts import ERROR

    class AlwaysRefused(Exception):
        pass

    class RefusingLLM:
        async def chat(self, messages, tools=None):
            raise AlwaysRefused("一直被拒")

    k = make_kernel(tmp_path, RefusingLLM())
    k._cfg.llm_retry_backoff_s = 0.05
    k._cfg.llm_refuse_receipts = 2
    evs = events_of(k)
    k.notify(Envelope(text="说点敏感的"))
    await wait_event(evs, REST, reason="llm_refuse_retry", timeout=10)
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    receipts = [u for u in users if u.startswith("（系统回执：")]
    assert len(receipts) == 2  # 恰好回执阈值次，不再重复
    assert any(t == ERROR and d.get("error") == "llm_refused" for t, d in evs)
    await k.abort()  # 止性无限退避循环，干净收尾


async def test_dangling_input_recovery(tmp_path):
    """崩溃恢复：会话尾部悬空输入（user/tool 未回应）→ 复活后续处理。"""
    llm = FakeLLM([text_reply("续上了"), text_reply("处理完了")])
    k = make_kernel(tmp_path, llm)
    # 模拟崩溃遗留：会话尾部是未回应的 user 消息
    k._session.append({"role": "user", "content": "（被中断的任务：去群里说话）"})
    assert k._has_dangling_input() is True
    k._ensure_run()
    await k.wait_idle()
    users = [m["content"] for m in k._session.messages if m.get("role") == "user"]
    assert any("思考中断后恢复" in u for u in users)
    assert k.is_busy is False
    # 正常休息后无悬空 → 不再触发恢复
    k._session.append({"role": "assistant", "content": "done"})
    assert k._has_dangling_input() is False


async def test_live_crashed_event_carries_traceback(tmp_path):
    """live_crashed 事件必须带完整堆栈（黑匣子可独立排查）。"""
    from lifemate.kernel.contracts import ERROR

    llm = FakeLLM([text_reply("x")])
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)

    async def _boom():
        raise RuntimeError("循环里炸了")

    k._think = _boom  # 模拟非 LLM 路径的崩溃
    k.notify(Envelope(text="触发"))
    for _ in range(100):
        if k._task is not None and k._task.done():
            break
        await asyncio.sleep(0.02)
    errors = [d for t, d in evs if t == ERROR]
    assert any(d.get("error") == "live_crashed" and "循环里炸了" in d.get("detail", {}).get("traceback", "") for d in errors)


# ── 自主免打扰（DND）：wake_on="direct" ────────────────


async def test_dnd_ambient_accumulates_direct_penetrates(tmp_path):
    """DND：ambient 只累积不唤醒；直达穿透；自然醒后组批可见。"""
    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 0.3, "reason": "睡长觉", "wake_on": "direct"}', cid="c1"),
            text_reply("醒了"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="先睡一觉"))
    await wait_event(evs, REST, reason="explicit:睡长觉")
    # DND 期间：ambient 不唤醒
    for i in range(3):
        k.notify(Envelope(text=f"群聊水话{i}", kind=AMBIENT, origin="qq"))
        await asyncio.sleep(0.05)
    assert k._wait is not None and not k._wait.future.done()  # 还在睡
    # 直达穿透
    k.notify(Envelope(text="@你 有事", kind=DIRECT, origin="qq"))
    await k.wait_idle()
    tool_msgs = [m["content"] for m in k._session.messages if m.get("role") == "tool"]
    assert any("@你 有事" in x for x in tool_msgs)
    assert any("群聊水话" in x for x in tool_msgs)


async def test_dnd_reminder_kind_penetrates(tmp_path):
    """自定闹钟（REMINDER）在 DND 下照常穿透——自己的承诺不静音。"""
    from lifemate.kernel.contracts import REMINDER

    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 60, "reason": "睡到七点", "wake_on": "direct"}', cid="c1"),
            text_reply("七点了"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="睡了"))
    await wait_event(evs, REST, reason="explicit:睡到七点")
    k.notify(Envelope(text="（你自己定的提醒到点了）", kind=REMINDER, origin="host"))
    await k.wait_idle()
    tool_msgs = [m["content"] for m in k._session.messages if m.get("role") == "tool"]
    assert any("提醒到点" in x for x in tool_msgs)  # 穿透并出现在唤醒组批


async def test_dnd_wake_batch_ambient_capped(tmp_path):
    """DND 累积超限：ambient 截断到最近 12 条 + 计数提示。"""
    llm = FakeLLM(
        [
            tool_reply("wait", args='{"timeout_s": 0.3, "reason": "长觉", "wake_on": "direct"}', cid="c1"),
            text_reply("醒"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    k.notify(Envelope(text="睡"))
    await wait_event(evs, REST, reason="explicit:长觉")
    for i in range(30):
        k.notify(Envelope(text=f"水话{i}", kind=AMBIENT, origin="qq"))

    # 闹钟自然醒（0.3s 超时）→ 组批（轮询到周期完成）
    async def _batch_ready():
        msgs = [m["content"] for m in k._session.messages if m.get("role") == "tool"]
        return any("群消息未列出" in x for x in msgs)

    async def _poll():
        while not await _batch_ready():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout=10)
    tool_msgs = [m["content"] for m in k._session.messages if m.get("role") == "tool"]
    batch = [t for t in tool_msgs if "期间收到" in t or "群消息未列出" in t]
    assert any("另有 18 条群消息未列出" in t for t in batch)
    assert any("水话29" in t for t in batch)  # 保留最近的
    assert all("水话0\n" not in t and "水话11" not in t for t in batch)  # 早期的被截
