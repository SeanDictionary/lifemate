"""记忆系统单测。"""
import time

from lifemate.memory.store import Store
from lifemate.memory.tools import build_memory_tools


def _store(tmp_path):
    return Store(tmp_path / "memory.db")


def test_episode_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.record_episode("今天开始 v2 重构", kind="life")
    rows = s.recent_episodes(n=5)
    assert len(rows) == 1 and "v2 重构" in rows[0]["summary"]


def test_curiosity_dedup_and_resolve(tmp_path):
    s = _store(tmp_path)
    assert s.add_curiosity("pi 的会话机制") is True
    assert s.add_curiosity("pi 的会话机制") is False  # 7 天去重
    rows = s.pending_curiosity()
    assert len(rows) == 1
    s.resolve_curiosity(rows[0]["id"], "已了解")
    assert s.pending_curiosity() == []


def test_need_dedup(tmp_path):
    s = _store(tmp_path)
    assert s.add_need("想要视觉能力", "想看图片") is True
    assert s.add_need("想要视觉能力", "") is False
    rows = s.pending_needs()
    s.mark_need_raised(rows[0]["id"])
    assert s.pending_needs() == []


def test_reminders_due_and_daily_roll(tmp_path):
    s = _store(tmp_path)
    s.add_reminder(time.time() - 10, "看一眼新消息")            # 已到期
    s.add_reminder(time.time() + 3600, "一小时后写作")          # 未到期
    s.add_reminder(time.time() - 10, "每晚复盘", repeat="daily")  # 到期但滚动
    due = s.due_reminders()
    assert len(due) == 2
    pend = s.pending_reminders()
    # 一次性已标记完成被移出；daily 滚到明天仍在
    assert {r["intent"] for r in pend} == {"一小时后写作", "每晚复盘"}
    assert pend[-1]["due_at"] > time.time()  # daily 已滚到未来


def test_rebuild_packet(tmp_path):
    s = _store(tmp_path)
    assert s.latest_packet() is None
    s.save_packet("我是大肥鱼，正在过 v2 的日子")
    assert "v2" in (s.latest_packet() or "")


async def test_memory_tools_callable(tmp_path):
    s = _store(tmp_path)
    tools = {t.name: t for t in build_memory_tools(s)}
    assert "record_episode" in tools and "save_rebuild_packet" in tools
    r = await tools["record_episode"].func({"summary": "工具链验证"})
    assert '"ok": true' in r
    r = await tools["recent_episodes"].func({})
    assert "工具链验证" in r
    r = await tools["set_reminder"].func(
        {"delay_minutes": 0.1, "intent": "测试"})
    assert '"ok": true' in r
    r = await tools["latest_rebuild_packet"].func({})
    assert '"ok": false' in r  # 还没有重建包
