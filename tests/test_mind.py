"""思维循环语义测试（信封三语义/工具兜底/轮换/压缩/会话持久化）。"""
import asyncio

from lifemate.kernel.compaction import Compactor
from lifemate.kernel.contracts import (ASSISTANT_MSG, LLMResponse, RUN_ENDED,
                                       RUN_STARTED, SESSION_ROTATED, TOOL_CALL,
                                       TOOL_RESULT, Envelope, Tool)
from lifemate.kernel.mind import SelfKernel
from lifemate.kernel.persona import Persona
from lifemate.kernel.session import Session

from conftest import FakeLLM, make_persona, make_session, text_reply, tool_reply


def make_tool(name: str, result: str = '{"ok": true}',
              delay: float = 0.0) -> Tool:
    async def _fn(args: dict) -> str:
        if delay:
            await asyncio.sleep(delay)
        return result
    return Tool(schema={"name": name, "description": f"test tool {name}",
                        "inputSchema": {"type": "object", "properties": {}}},
                func=_fn)


def make_kernel(tmp_path, llm, *, persona: Persona | None = None,
                tools: list[Tool] | None = None,
                session: Session | None = None) -> SelfKernel:
    from lifemate.kernel.llm import LLMClient  # noqa: F401  类型仅注释用
    k = SelfKernel(
        llm=llm,  # type: ignore[arg-type]
        persona=persona or make_persona(tmp_path),
        session=session or make_session(tmp_path),
        compactor=Compactor(llm.chat, threshold_tokens=10**9,  # 测试默认不压缩
                            keep_recent_tokens=4000),
        max_turns=5, tool_timeout_s=2.0)
    for t in (tools or []):
        k.register_tool(t)
    return k


def events_of(kernel: SelfKernel) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    kernel.subscribe(lambda ev: out.append((ev.type, ev.data)))
    return out  # type: ignore[return-value]


async def test_prompt_runs_to_final_text(tmp_path):
    llm = FakeLLM([text_reply("我醒了")])
    k = make_kernel(tmp_path, llm)
    evs = events_of(k)
    await k.prompt(Envelope(text="（你醒了）", kind="boot"))
    await k.wait_idle()
    types = [t for t, _ in evs]
    assert types[0] == RUN_STARTED and types[-1] == RUN_ENDED
    assert any(t == ASSISTANT_MSG for t in types)
    assert any(m["role"] == "user" and "你醒了" in m["content"]
               for m in k._session.messages)


async def test_tool_call_gets_receipt(tmp_path):
    llm = FakeLLM([tool_reply("echo"), text_reply("做完了")])
    k = make_kernel(tmp_path, llm, tools=[make_tool("echo")])
    evs = events_of(k)
    await k.prompt(Envelope(text="用一下 echo"))
    await k.wait_idle()
    msgs = k._session.messages
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1 and '"ok": true' in tool_msgs[0]["content"]
    assert any(t == TOOL_CALL for t, _ in evs)
    assert any(t == TOOL_RESULT for t, _ in evs)


async def test_unknown_and_failing_tool_never_break_chain(tmp_path):
    llm = FakeLLM([
        tool_reply("ghost_tool"),                    # 不存在
        tool_reply("boom", cid="call_2"),            # 执行抛异常
        text_reply("两件都失败了，没关系"),
    ])
    boom = Tool(schema={"name": "boom", "description": "x",
                        "inputSchema": {"type": "object", "properties": {}}},
                func=_raise)
    k = make_kernel(tmp_path, llm, tools=[boom])
    await k.prompt(Envelope(text="试试两个工具"))
    await k.wait_idle()
    tool_msgs = [m for m in k._session.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2  # 每个 tool_call 都有回执，链未断
    assert "工具不存在" in tool_msgs[0]["content"]
    assert '"ok": false' in tool_msgs[1]["content"]


async def _raise(args: dict) -> str:
    raise RuntimeError("炸了")


async def test_steer_arrives_before_next_llm_call(tmp_path):
    """插话在工具批次后、下一次模型调用前送达。"""
    llm = FakeLLM([
        tool_reply("slow", cid="c1"),   # 第一次：调工具
        text_reply("好的，我听到了插话"),  # 第二次：应已看到插话文本
    ])
    slow = make_tool("slow", delay=0.2)

    async def _steer_during_tool() -> None:
        await asyncio.sleep(0.05)  # 等工具开始执行
        await k.steer(Envelope(text="（插话：主人又补了一句）"))

    k = make_kernel(tmp_path, llm, tools=[slow])
    await asyncio.gather(
        k.prompt(Envelope(text="先干个活")),
        _steer_during_tool(),
    )
    await k.wait_idle()
    # 第二次模型调用应包含插话文本
    assert any("主人又补了一句" in str(m.get("content"))
               for m in llm.calls[1])


async def test_follow_up_runs_sequentially(tmp_path):
    llm = FakeLLM([text_reply("第一封的回应"), text_reply("第二封的回应")])
    k = make_kernel(tmp_path, llm)
    await k.prompt(Envelope(text="第一封"))
    await k.prompt(Envelope(text="第二封"))  # 忙碌时自动降级 follow_up
    await k.wait_idle()
    # 两次模型调用各自以对应信封为起点
    assert any("第一封" in str(m.get("content")) for m in llm.calls[0])
    assert any("第二封" in str(m.get("content")) for m in llm.calls[1])
    assert len(llm.calls) == 2


async def test_rotate_seeds_new_session(tmp_path):
    llm = FakeLLM([text_reply("初始回应"),
                   text_reply("新会话的我收到播种")])
    old_session = make_session(tmp_path)
    k = make_kernel(tmp_path, llm, session=old_session)
    evs = events_of(k)
    await k.prompt(Envelope(text="旧会话内容"))
    await k.wait_idle()
    old_id = k.session_id
    await k.rotate(seed="（播种：你是小Q，刚从睡眠中轮换而来……）")
    await k.wait_idle()
    assert any(t == SESSION_ROTATED for t, _ in evs)
    assert k.session_id != old_id
    msgs = k._session.messages
    assert "播种" in msgs[0]["content"] and msgs[0]["role"] == "user"
    # 旧会话文件保留
    assert old_session.file.exists()


async def test_max_turns_converges(tmp_path):
    """轮次耗尽后强制无工具收束，不死循环。"""
    script = [tool_reply("echo", cid=f"c{i}") for i in range(5)]  # 恰好耗尽 max_turns=5
    script.append(text_reply("被叫停了"))
    llm = FakeLLM(script)
    k = make_kernel(tmp_path, llm, tools=[make_tool("echo")])
    await k.prompt(Envelope(text="无限循环吧"))
    await k.wait_idle()
    final = k._session.messages[-1]
    assert final["role"] == "assistant" and final["content"] == "被叫停了"


async def test_compaction_triggers_and_persists(tmp_path):
    """低阈值触发压缩：摘要入会话、消息截断、文件重写可恢复。"""
    big = "x" * 400
    llm = FakeLLM([
        tool_reply("echo", cid="c1"),
        LLMResponse(content="（这是压缩摘要）"),  # 第 2 轮的压缩调用
        text_reply("继续"),
    ])
    session = make_session(tmp_path)
    # keep_recent=40：第 2 轮时截断点落在首条大消息后（保留工具对完整）
    compactor = Compactor(llm.chat, threshold_tokens=50, keep_recent_tokens=40)
    k = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=session,
                   compactor=compactor, max_turns=5, tool_timeout_s=2.0)
    k.register_tool(make_tool("echo"))
    await k.prompt(Envelope(text=f"触发压缩{big}"))
    await k.wait_idle()
    assert session.summary == "（这是压缩摘要）"
    assert len(session.messages) == 3  # 一对工具消息 + 收束回复
    # 文件重写后可恢复：摘要与新会话起点
    reloaded = Session.load_latest(session.file.parent)
    assert reloaded.summary == "（这是压缩摘要）"
    assert len(reloaded.messages) == 3


async def test_session_restart_continuity(tmp_path):
    """重启恢复最近会话（7×24 连续性）。"""
    s1 = make_session(tmp_path)
    llm = FakeLLM([text_reply("好")])
    k = make_kernel(tmp_path, llm, session=s1)
    await k.prompt(Envelope(text="今天聊过的话题"))
    await k.wait_idle()
    s2 = Session.load_latest(s1.file.parent)
    assert any("今天聊过的话题" in str(m.get("content"))
               for m in s2.messages)
