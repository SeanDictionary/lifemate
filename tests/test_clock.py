"""主体时钟测试：时区解析 / 回落 / 换算（config.timezone 的消费端）。"""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lifemate.utils import clock


def test_empty_and_invalid_fall_back_to_local():
    """空名=None（本机时区）；非法名告警回落 None，不抛错。"""
    assert clock.tzinfo_for("") is None
    assert clock.tzinfo_for(None) is None
    assert clock.tzinfo_for("  ") is None
    assert clock.tzinfo_for("Mars/Olympus_Mons") is None  # 坏值缓存后仍 None


def test_valid_name_resolves_and_cache_survives_switch():
    pytest.importorskip("tzdata")  # Windows 本机需 pip tzdata
    assert clock.tzinfo_for("Asia/Shanghai") is not None
    assert clock.tzinfo_for("UTC") is not None
    # 单槽缓存交替访问仍正确（只是不复用）
    assert clock.tzinfo_for("Asia/Shanghai").key == "Asia/Shanghai"


def test_hm_timezone_shift():
    """同一时刻在不同时区的 HH:MM 表达；空=本机时区与旧行为一致。"""
    pytest.importorskip("tzdata")
    ts = datetime(2026, 10, 21, 15, 0,
                  tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    assert clock.hm(ts, "Asia/Shanghai") == "15:00"
    assert clock.hm(ts, "UTC") == "07:00"                 # 固定 8h 偏移
    assert clock.hm(ts) == time.strftime("%H:%M", time.localtime(ts))


def test_datetime_at_naive_local_equivalence():
    """tz=None → 本机时区 naive datetime（与 datetime.fromtimestamp 一致）。"""
    ts = time.time()
    assert clock.datetime_at(ts) == datetime.fromtimestamp(ts)
    pytest.importorskip("tzdata")
    utc = clock.datetime_at(ts, "UTC")
    assert utc.utcoffset().total_seconds() == 0


def test_format_rel_buckets():
    """相对时间分档：刚刚/分钟/小时/超窗退化绝对。"""
    now = time.time()
    assert clock.format_rel(now - 30, now) == "刚刚"
    assert clock.format_rel(now + 30, now) == "马上"
    assert clock.format_rel(now - 300, now) == "5 分钟前"
    assert clock.format_rel(now + 4200, now) == "1 小时后"  # 70 分钟 → 小时档
    assert clock.format_rel(now + 7200, now) == "2 小时后"
    far = clock.format_rel(now - 5 * 86400, now)
    assert "前" not in far and ":" in far  # 超窗 → 绝对标注
