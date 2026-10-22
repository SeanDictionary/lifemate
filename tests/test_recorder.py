"""观测记录完整性测试（黑匣子必须常绿）。"""

import json

from conftest import FakeLLM, make_config, make_persona, make_session, text_reply

from lifemate.kernel.compaction import Compactor
from lifemate.kernel.contracts import Envelope, LLMResponse, Tool
from lifemate.kernel.mind import SelfKernel
from lifemate.utils.recorder import Recorder


def make_tool(name: str, result: str = '{"ok": true}') -> Tool:
    async def _fn(args: dict) -> str:
        return result

    return Tool(schema={"name": name, "description": f"test {name}", "inputSchema": {"type": "object", "properties": {}}}, func=_fn)


def make_setup(tmp_path, llm, tools):
    cfg = make_config(tmp_path)
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=make_session(tmp_path), compactor=Compactor(llm.chat, cfg), cfg=cfg)
    for t in tools:
        kernel.register_tool(t)
    recorder = Recorder(tmp_path / "observe", sid_fn=lambda: kernel.session_id)
    kernel.subscribe(recorder.on_kernel_event)
    return kernel, recorder


def read_lines(recorder) -> list[dict]:
    day_file = list((recorder._dir).glob("*.jsonl"))[0]
    return [json.loads(x) for x in day_file.read_text(encoding="utf-8").splitlines()]


async def test_full_run_recorded(tmp_path):
    """一次运行的完整链路：run → 信封 → assistant(含思考) → 工具 → run_end。"""
    llm = FakeLLM(
        [
            LLMResponse(content="", tool_calls=[__import__("lifemate.kernel.contracts", fromlist=["ToolCall"]).ToolCall(id="c1", name="echo", arguments="{}")], thinking="用户在测试我，我应该调一下工具", stop_reason="toolUse", model="test-model", prompt_tokens=100, completion_tokens=20, latency_ms=50),
            text_reply("做完了"),
        ]
    )
    kernel, recorder = make_setup(tmp_path, llm, [make_tool("echo")])
    recorder.event("boot", version="test", tools=["echo"])

    kernel.notify(Envelope(text="用一下 echo", kind="direct", source="group_1"))
    await kernel.wait_idle()
    recorder.close()

    lines = read_lines(recorder)
    types = [x["type"] for x in lines]
    # 完整链路齐全且有序
    assert types[0] == "boot"
    i = types.index("wake")
    assert types[i + 1] == "msg" and lines[i + 1]["role"] == "user"
    # 信封行：投递语义在场
    env = lines[i + 1]["env"]
    assert env["kind"] == "direct" and env["source"] == "group_1"
    assert env["delivery"] == "notify" and env["arrived_ts"] > 0
    # assistant 行：思考与元数据在场
    a = next(x for x in lines if x.get("role") == "assistant")
    assert a["thinking"] == "用户在测试我，我应该调一下工具"
    assert a["model"] == "test-model" and a["stop_reason"] == "toolUse"
    assert a["prompt_tokens"] == 100 and a["latency_ms"] == 50
    # 工具行：执行语义在场
    t = next(x for x in lines if x.get("role") == "tool")
    assert t["name"] == "echo" and t["ok"] is True and t["tool_call_id"] == "c1"
    assert types[-1] == "rest" and lines[-1]["reason"] == "converged"
    # rid 贯穿：信封/助手/工具/收尾同一 rid
    rid = lines[i + 1]["rid"]
    assert a["rid"] == rid and t["rid"] == rid and lines[-1]["rid"] == rid
    # seq 单调递增
    seqs = [x["seq"] for x in lines]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_queue_drop_and_rotate_recorded(tmp_path):
    """被丢弃的插话 + 轮换：全宇宙唯一的存证。"""
    llm = FakeLLM([text_reply("嗯")])
    kernel, recorder = make_setup(tmp_path, llm, [])
    kernel.notify(Envelope(text="第一封"))
    await kernel.wait_idle()
    # 模拟一封永远无人处理的插话（直接塞进内核队列后被中止丢弃）
    kernel._steer_q.append(Envelope(text="被丢弃的插话", kind="direct"))
    await kernel.abort()
    recorder.close()

    lines = read_lines(recorder)
    drop = next(x for x in lines if x["type"] == "queue_drop")
    assert drop["reason"] == "abort" and drop["count"] == 1
    assert drop["texts"] == ["被丢弃的插话"]


async def test_field_truncation(tmp_path):
    """超长字段截断且带标记。"""
    r = Recorder(tmp_path / "observe", max_field_chars=100)
    r.event("error", where="x", error="y" * 500)
    r.close()
    line = read_lines(r)[0]
    assert line["error"].endswith("…[truncated]")
    assert len(line["error"]) <= 100 + len("…[truncated]")


async def test_session_cleanup_on_rotate(tmp_path):
    """轮换后旧会话文件按 keep 清理（档案职责在 observe）。"""
    sessions_dir = tmp_path / "sessions"
    s1 = make_session(tmp_path)
    llm = FakeLLM([])
    cfg = make_config(tmp_path)
    cfg.session_keep_rotated = 1
    kernel = SelfKernel(llm=llm, persona=make_persona(tmp_path), session=s1, compactor=Compactor(llm.chat, cfg), cfg=cfg)
    kernel.swap_session("s2")  # s1 → s2
    kernel.swap_session("s3")  # s2 → s3（s1 应被清理，保留 keep=1 个旧文件）
    files = sorted(sessions_dir.glob("*.jsonl"))
    assert len(files) == 2  # 当前 + 1 个旧文件
