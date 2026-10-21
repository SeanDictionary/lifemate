"""剥夺测试（架构验收，必须常绿）：

拔掉全部器官，只剩内核（思维+记忆+人格）——它必须存活：
能被唤醒、能自决使用记忆工具、能收敛、能把经历写入长期记忆。
做不到 = 聊天机器人，不是生命。
"""
from lifemate.kernel.compaction import Compactor
from lifemate.kernel.contracts import ASSISTANT_MSG, REST, WAKE, Envelope
from lifemate.kernel.mind import SelfKernel
from lifemate.kernel.persona import Persona
from lifemate.kernel.session import Session
from lifemate.utils.config import Config
from lifemate.memory.store import Store
from lifemate.memory.tools import build_memory_tools

from conftest import FakeLLM, text_reply, tool_reply

BOOT = "（你醒了。此刻你没有任何外界器官，只有你的记忆和你自己。）"


def build_bare_life(tmp_path, llm) -> tuple[SelfKernel, Store]:
    """最小生命：内核+记忆+人格，零器官。"""
    import json
    root_dir = tmp_path  # 记忆放临时目录
    store = Store(root_dir / "memory.db")
    session = Session.create(root_dir / "sessions")
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    persona = Persona(repo / "soul", repo / "skills")
    cfg = Config(data_dir=root_dir / "cfg")
    cfg.max_turns = 8
    cfg.compact_threshold_tokens = 10**9
    kernel = SelfKernel(
        llm=llm, persona=persona, session=session,
        compactor=Compactor(llm.chat, cfg),
        cfg=cfg)
    for t in build_memory_tools(store):
        kernel.register_tool(t)
    return kernel, store


async def test_deprivation_boot_survives(tmp_path):
    """出生信封 → 自省（读记忆）→ 记录经历 → 收敛存活。"""
    llm = FakeLLM([
        tool_reply("recent_episodes", args='{"n": 5}', cid="c1"),
        tool_reply("record_episode",
                   args=json_dump({"summary": "我在无器官状态下醒来了",
                                   "kind": "life"}), cid="c2"),
        text_reply("我醒了，看了看记忆，记下了这一刻。现在安静地待着。"),
    ])
    kernel, store = build_bare_life(tmp_path, llm)
    evs: list[tuple[str, dict]] = []
    kernel.subscribe(lambda ev: evs.append((ev.type, ev.data)))

    kernel.notify(Envelope(text=BOOT, kind="boot"))
    await kernel.wait_idle()

    # 运行完整：唤醒 → 助手消息（含工具轮）→ 收敛
    types = [t for t, _ in evs]
    assert WAKE in types and REST in types
    assert types.count(ASSISTANT_MSG) == 3

    # 经历已写入长期记忆（它记得自己醒过）
    rows = store.recent_episodes()
    assert any("无器官状态" in r["summary"] for r in rows)

    # 剥夺语义：工具列表里没有任何器官工具（只有记忆工具）
    names = set(kernel._tools)
    assert "send_message" not in names and "exec" not in names
    assert "record_episode" in names  # 记忆是内核的一部分，必须在

    # 它可以给自己安排未来（自我时间的存储面）
    store.close()


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


async def test_deprivation_rotation_rebirth(tmp_path):
    """无器官下完成一次完整轮换：写重建包 → 轮换 → 播种 → 新我读信。"""
    llm = FakeLLM([
        tool_reply("save_rebuild_packet",
                   args=json_dump({"content": "我是大肥鱼，v2 重构日的自己"})),
        text_reply("信写好了，可以睡了。"),
        tool_reply("latest_rebuild_packet", cid="c1"),
        text_reply("读到了上一个我的信。我记得我是谁。"),
    ])
    kernel, store = build_bare_life(tmp_path, llm)

    kernel.notify(Envelope(text=BOOT, kind="boot"))
    await kernel.wait_idle()
    old_id = kernel.session_id
    kernel.swap_session("（播种：你刚从深睡中醒来，先读读上一个你留下的信。）")
    kernel.notify(Envelope(text="（读到了上一个我的信）"))
    await kernel.wait_idle()

    assert kernel.session_id != old_id
    assert "大肥鱼" in (store.latest_packet() or "")
    store.close()


async def test_persona_has_identity_and_skills(tmp_path):
    """人格装配真实形态：identity + skills 索引（无器官知识）。"""
    llm = FakeLLM([text_reply("嗯")])
    kernel, _ = build_bare_life(tmp_path, llm)
    prompt = kernel._persona.system_prompt()
    assert "大肥鱼" in prompt               # 身份在
    assert "重建包" in prompt             # 内置机制指引常驻在场（保证在场，非按需）
    assert "memory-keeping" not in prompt  # 已不是 skill（第三方索引里没有）
    assert kernel._persona.read_skill("memory-keeping").startswith('{"ok"')  # skill 已移除
