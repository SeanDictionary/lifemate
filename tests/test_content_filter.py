"""内容过滤防阻塞链路测试：分类（llm 层）+ 逃生梯（思维层）。

场景依据（2026-10-23 实测）：网关对含敏感内容的上下文拒绝服务；
拒绝不重试（同样内容重试同样被拦），直接墓碑机制解阻塞。
"""

from conftest import FakeLLM, text_reply
from test_mind import events_of, make_kernel, wait_event

from lifemate.kernel.contracts import ERROR, SESSION_ROTATED, Envelope
from lifemate.kernel.llm import (
    BadGatewayBody,
    ContentFiltered,
    _extract_err_message,
    _match_content_filter,
    chat_with_retry,
    is_retryable,
)
from lifemate.kernel.mind import SelfKernel

# ── llm 层：分类 ──────────────────────────────────────


def test_markers_match_various_providers():
    """白名单覆盖各家实测措辞；无关文本不命中。"""
    assert _match_content_filter('data: {"error":{"message":"The request contains sensitive content.","type":"content_filter"}}')
    assert _match_content_filter("Error code: 400 - {'detail': 'Content Exists Risk'}")
    assert _match_content_filter('"code": "data_inspection_failed"')
    assert _match_content_filter("The response was filtered due to the prompt triggering Azure OpenAI's content management policy.")
    assert not _match_content_filter("Upstream rate limit exceeded, please retry later")
    assert not _match_content_filter("connection refused")


def test_extract_err_message_from_sse_body():
    body = 'data: {"error":{"message":"The request contains sensitive content. Please modify your input and try again.","type":"content_filter"}}'
    assert _extract_err_message(body) == "The request contains sensitive content. Please modify your input and try again."
    assert _extract_err_message("纯乱码体") == "纯乱码体"


def test_classification_bad_gateway_vs_content_filter():
    assert is_retryable(BadGatewayBody("网关返回非标准响应体：xxx"))
    assert not is_retryable(ContentFiltered("上游内容过滤：xxx"))


async def test_content_filter_not_retried():
    """内容过滤：不重试——同样内容重试同样被拦，直接交思维层。"""
    calls = []

    async def fn():
        calls.append(1)
        raise ContentFiltered("上游内容过滤：sensitive content")

    try:
        await chat_with_retry(fn, max_retries=3, backoff_s=0.01)
        raise AssertionError("应上抛")
    except ContentFiltered:
        pass
    assert len(calls) == 1  # 一次即抛，零重试


# ── 思维层：逃生梯 ────────────────────────────────────


async def _drive(k: SelfKernel, evs, final_rest_reason: str = "converged"):
    k.notify(Envelope(text="（你醒了）", kind="boot"))
    await wait_event(evs, "rest", reason=final_rest_reason, timeout=5.0)


async def test_flaky_rejection_rolls_back_then_recovers(tmp_path):
    """花心渠道单次拒绝：直接墓碑回滚（简洁优先，不加重试层），下次恢复。

    代价：新入内容可能被误滚一次（档案未丢，墓碑说明原委）。
    """
    from conftest import tool_reply
    from test_mind import make_tool

    llm = FakeLLM(
        [
            tool_reply("echo", cid="c1"),  # 成功 → 检查点确立
            ContentFiltered("上游内容过滤：sensitive content"),  # 单次拒绝：直接墓碑
            text_reply("回滚后恢复"),
        ]
    )
    k = make_kernel(tmp_path, llm, tools=[make_tool("echo")])
    evs = events_of(k)
    k.notify(Envelope(text="（你醒了）", kind="boot"))
    await wait_event(evs, "rest", reason="converged", timeout=5.0)

    actions = [d.get("detail", {}).get("action") for t, d in evs if t == ERROR and d.get("error") == "llm_content_filtered"]
    assert actions == ["rollback"]  # 单次拒绝即回滚，恢复后梯子归零
    assert k._cf_stage == 0


async def test_rollback_tombstone_on_persistent_filter(tmp_path):
    """持续被拒：墓碑回滚——检查点后内容被移除，墓碑可见、tool 配对完整。"""
    from conftest import tool_reply
    from test_mind import make_tool

    llm = FakeLLM(
        [
            tool_reply("echo", cid="c1"),  # 第一轮工具调用：成功 → 检查点确立
            ContentFiltered("上游内容过滤：sensitive content"),  # 传播：墓碑回滚
            text_reply("回滚后恢复"),
        ]
    )
    k = make_kernel(tmp_path, llm, tools=[make_tool("echo")])
    evs = events_of(k)
    k.notify(Envelope(text="（你醒了）", kind="boot"))
    await wait_event(evs, "rest", reason="converged", timeout=5.0)

    # 墓碑回滚发生：会话 = 检查点前缀 + 墓碑 + 新 assistant（毒内容不在）
    errors = [d for t, d in evs if t == ERROR]
    assert any(d.get("error") == "llm_content_filtered" and d.get("detail", {}).get("action") == "rollback" for d in errors)
    msgs = k._session.messages
    assert len(msgs) == 3  # boot 前缀 + 墓碑 + 回复
    assert "已移出工作记忆" in str(msgs[1].get("content"))
    assert msgs[2].get("role") == "assistant"
    # tool 配对完整（无孤儿 tool 消息）
    answered = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    calls = {tc.get("id") for m in msgs if m.get("role") == "assistant" for tc in m.get("tool_calls") or []}
    assert answered <= calls
    assert k._cf_stage == 0  # 回滚后成功，梯子归零


async def test_escalate_to_rotation_when_rollback_insufficient(tmp_path):
    """回滚无效（毒在检查点前的历史里）：告警 → 系统代笔转身。"""
    from conftest import tool_reply
    from test_mind import make_tool

    llm = FakeLLM(
        [
            tool_reply("echo", cid="c1"),  # 建立检查点
            ContentFiltered("上游内容过滤：sensitive content"),  # ① 墓碑回滚
            ContentFiltered("上游内容过滤：sensitive content"),  # ② 深度告警
            ContentFiltered("上游内容过滤：sensitive content"),  # ③ 紧急转身
            text_reply("新生"),  # 新会话从 seed 开始，恢复
        ]
    )
    k = make_kernel(tmp_path, llm, tools=[make_tool("echo")])
    old_id = k.session_id
    evs = events_of(k)
    k.notify(Envelope(text="（你醒了）", kind="boot"))
    await wait_event(evs, "rest", reason="converged", timeout=8.0)

    actions = [d.get("detail", {}).get("action") for t, d in evs if t == ERROR and d.get("error") == "llm_content_filtered"]
    assert actions == ["rollback", "warn", "rotate"]
    assert any(t == SESSION_ROTATED for t, _ in evs)
    assert k.session_id != old_id
    users = [str(m.get("content")) for m in k._session.messages if m.get("role") == "user"]
    assert any("紧急转身" in u for u in users)  # 系统 seed 送达


async def test_no_validated_prefix_still_escapes(tmp_path):
    """无成功记录（带毒启动等价）：检查点=载入长度，首拒先回滚新入信封
    （无法预判毒在新内容还是历史里——先试便宜的），梯子照样三步逃生。
    """
    llm = FakeLLM(
        [
            ContentFiltered("上游内容过滤：sensitive content"),  # ① 回滚（仅新入信封）
            ContentFiltered("上游内容过滤：sensitive content"),  # ② 告警
            ContentFiltered("上游内容过滤：sensitive content"),  # ③ 转身
            text_reply("新生"),
        ]
    )
    k = make_kernel(tmp_path, llm)
    assert k._ok_len == len(k._session.messages)  # 无成功记录：检查点=载入长度
    old_id = k.session_id
    evs = events_of(k)
    k.notify(Envelope(text="（你醒了）", kind="boot"))
    await wait_event(evs, "rest", reason="converged", timeout=8.0)

    actions = [d.get("detail", {}).get("action") for t, d in evs if t == ERROR and d.get("error") == "llm_content_filtered"]
    assert actions == ["rollback", "warn", "rotate"]
    assert k.session_id != old_id
