"""chatlog 按日分库测试：跨天穿透 / 保留期清理 / 未读精确性。"""

import time

import pytest

from lifemate.plugins.qq.chatlog import ChatLog, format_ts

DAY = 86400


def test_format_ts_time_sense():
    """时间标注分档：今天/昨天/前天/今年更早/跨年。"""
    import re

    now = time.mktime(time.strptime("2026-10-21 15:00", "%Y-%m-%d %H:%M"))
    assert format_ts(now, now) == "15:00"
    assert format_ts(now - 0.5 * DAY, now) == "03:00"  # 今天凌晨也不算昨天
    y = now - DAY
    assert format_ts(y, now) == f"昨天 {time.strftime('%H:%M', time.localtime(y))}"
    q = now - 2 * DAY
    assert format_ts(q, now).startswith("前天 ")
    old = time.mktime(time.strptime("2026-06-01 09:30", "%Y-%m-%d %H:%M"))
    assert format_ts(old, now) == "06-01 09:30"  # 今年更早：不带年份
    older = time.mktime(time.strptime("2025-06-01 09:30", "%Y-%m-%d %H:%M"))
    assert format_ts(older, now) == "2025-06-01 09:30"  # 跨年带年份
    assert re.fullmatch(r"\d{2}:\d{2}", format_ts(now, now))


def test_daily_partition_and_cross_day_query(tmp_path):
    """旧消息落旧日库；recent 跨库穿透凑够 N 条，正序返回。"""
    log = ChatLog(tmp_path)
    now = time.time()
    # 昨天两条 + 今天三条
    log.append("group_1", 1, "甲", "昨天的话1", ts=now - DAY + 100)
    log.append("group_1", 1, "甲", "昨天的话2", ts=now - DAY + 200)
    log.append("group_1", 2, "乙", "今天的话1", ts=now - 100)
    log.append("group_1", 2, "乙", "今天的话2", ts=now - 50)
    log.append("group_1", 2, "乙", "今天的话3", ts=now - 10)
    # 两个日库存在
    files = sorted((tmp_path / "chatlog").glob("*.db"))
    assert len(files) == 2
    # 跨天穿透：要 4 条 → 今天 3 条 + 昨天 1 条
    rows = log.recent("group_1", 4)
    assert len(rows) == 4
    texts = [r["text"] for r in rows]
    assert texts == ["昨天的话2", "今天的话1", "今天的话2", "今天的话3"]
    assert rows[0]["ts"] < rows[-1]["ts"]  # 正序
    log.close()


def test_no_count_cap(tmp_path):
    """不限量：写远超旧上限的条数，全部保留。"""
    log = ChatLog(tmp_path)
    now = time.time()
    for i in range(800):
        log.append("group_1", 1, "甲", f"第{i}条", ts=now + i * 0.001)
    assert len(log.recent("group_1", 1000)) == 800
    log.close()


def test_retention_prunes_old_days(tmp_path):
    """保留期：超期日库在日切时被清理；0=永久。"""
    now = time.time()
    log = ChatLog(tmp_path, retention_days=7)
    log.append("group_1", 1, "甲", "十天前", ts=now - 10 * DAY)
    # 日切（旧库 → 今日库）时触发 prune：十天前的库立即被清理
    log.append("group_1", 1, "甲", "今天", ts=now)
    days = sorted((tmp_path / "chatlog").glob("*.db"))
    assert len(days) == 1  # 只剩今日库
    log.close()
    # 五天前的库在保留期内：日切不清理
    log2 = ChatLog(tmp_path, retention_days=7)
    log2.append("group_1", 1, "甲", "五天前", ts=now - 5 * DAY)
    log2.append("group_1", 1, "甲", "次日", ts=now + DAY)
    assert any("十天前" not in p.stem for p in (tmp_path / "chatlog").glob("*.db"))
    assert len(list((tmp_path / "chatlog").glob("*.db"))) == 3  # 今日+次日+五天前
    log2.close()
    # retention=0 永久
    log3 = ChatLog(tmp_path, retention_days=0)
    log3.append("group_1", 1, "甲", "远古", ts=now - 100 * DAY)
    log3.append("group_1", 1, "甲", "次日", ts=now + 2 * DAY)
    assert len(list((tmp_path / "chatlog").glob("*.db"))) >= 2  # 没被清
    log3.close()


def test_format_ts_timezone():
    """时区指定：now 与 ts 同时区解释，分档不依赖本机时区。"""
    pytest.importorskip("tzdata")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def at(*args):
        return datetime(*args, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()

    now = at(2026, 10, 21, 15, 0)
    assert format_ts(at(2026, 10, 21, 3, 0), now, tz_name="Asia/Shanghai") == "03:00"
    assert format_ts(at(2026, 10, 20, 14, 32), now, tz_name="Asia/Shanghai") == "昨天 14:32"
    assert format_ts(at(2026, 10, 19, 9, 5), now, tz_name="Asia/Shanghai") == "前天 09:05"
    assert format_ts(at(2026, 6, 1, 9, 30), now, tz_name="Asia/Shanghai") == "06-01 09:30"
    assert format_ts(at(2025, 6, 1, 9, 30), now, tz_name="Asia/Shanghai") == "2025-06-01 09:30"


def test_unread_precision_after_read(tmp_path):
    """读过之后又来的新消息也要数进未读（精确到已读线）。"""
    log = ChatLog(tmp_path)
    now = time.time()
    log.append("group_1", 1, "甲", "a", ts=now - 100)
    log.append("group_1", 1, "甲", "b", ts=now - 90)
    log.mark_read("group_1")
    after_read = time.time()  # mark_read 之后才来的新消息
    log.append("group_1", 1, "甲", "读后新来的", ts=after_read + 1)
    summary = log.unread_summary()
    assert "1 条" in summary  # 读后新来的被精确计入
    log.close()


def test_unread_respects_24h_window(tmp_path):
    """超 24h 的未读不计入概览。"""
    log = ChatLog(tmp_path)
    now = time.time()
    log.append("group_2", 1, "甲", "两天前的", ts=now - 2 * DAY)
    assert log.unread_summary() == ""
    log.close()


def test_unread_excludes_self_outbound(tmp_path):
    """自己的出站消息不算未读（身体感受只数别人说的话）。"""
    log = ChatLog(tmp_path)
    now = time.time()
    log.append("group_1", 100, "别人", "hi", ts=now - 10)
    log.append("group_1", 100, "别人", "又一句", ts=now - 5)
    log.append("group_1", 7, "我", "我自己发的", ts=now - 1)
    summary = log.unread_summary(self_id=7)
    assert "2 条" in summary and "3 条" not in summary
    log.close()


def test_chats_listing(tmp_path):
    """会话清单：24h 活跃 + 未读数 + 自我出站不算未读。"""
    cl = ChatLog(tmp_path)
    try:
        now = time.time()
        cl.append("group_1", 100, "张三", "消息A", ts=now - 60)
        cl.append("group_1", 200, "self", "我说", ts=now - 30)
        cl.append("private_9", 100, "李四", "私聊", ts=now - 10)
        chats = cl.chats(self_id=200)
        by_id = {c["chat_id"]: c for c in chats}
        assert set(by_id) == {"group_1", "private_9"}
        assert by_id["group_1"]["unread"] == 1  # 自己发的不算
        assert by_id["private_9"]["unread"] == 1
        assert by_id["private_9"]["last_ts"] > by_id["group_1"]["last_ts"]
        cl.mark_read("group_1")
        assert {c["chat_id"]: c for c in cl.chats(200)}["group_1"]["unread"] == 0
    finally:
        cl.close()


def test_unread_summary_earliest_age(tmp_path):
    """未读概览带最早一条的相对时间（多老的消息心里有数）。"""
    log = ChatLog(tmp_path)
    now = time.time()
    log.append("group_1", 1, "甲", "一小时前的", ts=now - 3600)
    log.append("group_1", 1, "甲", "刚发的", ts=now - 60)
    s = log.unread_summary()
    assert "2 条" in s and "1 小时前" in s
    log.close()
