"""chatlog 按日分库测试：跨天穿透 / 保留期清理 / 未读精确性。"""
import time
from pathlib import Path

from lifemate.plugins.qq.chatlog import ChatLog

DAY = 86400


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
