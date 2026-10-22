"""主体时钟：时间感知的统一收口（utils 中立层，全系统可用）。

存储一律 Unix epoch（时区中立）；凡是给主体看的时间表达（唤醒信封的
"现在几点"、背景听觉的时间标注、日切边界）都经这里换算。

时区来源：config.timezone（IANA 名如 Asia/Shanghai，空 = 本机时区）。
调用方传活配置的字符串值进来——热重载后下一次调用即生效。
非法值告警并回落本机时区（不让配置错误拖垮时间感）。
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("utils.clock")

# 解析缓存：单槽 (名字, 结果)；空名不缓存，非法名也缓存（避免反复告警）
_cache: tuple[str, Optional[ZoneInfo]] = ("", None)


def tzinfo_for(name: str | None) -> Optional[ZoneInfo]:
    """IANA 时区名 → tzinfo；空/None → None（本机时区）；非法告警回落。"""
    name = (name or "").strip()
    if not name:
        return None
    global _cache
    if name != _cache[0]:
        try:
            tz = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError) as e:
            log.warning("时区 %r 不可用（回落本机时区）：%s", name, e)
            tz = None  # 坏值也缓存：不反复告警
        _cache = (name, tz)
    return _cache[1]


def datetime_at(ts: float, tz_name: str | None = "") -> datetime:
    """epoch → 主体时区的 datetime（tz=None → 本机时区，与旧行为一致）。"""
    return datetime.fromtimestamp(ts, tzinfo_for(tz_name))


def now(tz_name: str | None = "") -> datetime:
    """当前时刻（主体时区）。"""
    return datetime_at(_time.time(), tz_name)


def hm(ts: float, tz_name: str | None = "") -> str:
    """"HH:MM"（主体时区）。"""
    return datetime_at(ts, tz_name).strftime("%H:%M")


def format_ts(ts: float, now: float | None = None, tz_name: str = "") -> str:
    """人类可读时间标注（给心智建立时间感）：

    今天→HH:MM；昨天/前天→昨天 HH:MM；今年更早→MM-DD HH:MM；跨年带年份。
    tz_name：主体时区（IANA 名，空=本机）；now 也按同时区解释。
    """
    now = now if now is not None else _time.time()
    dt = datetime_at(ts, tz_name)
    today = datetime_at(now, tz_name).date()
    hm_ = dt.strftime("%H:%M")
    if dt.date() == today:
        return hm_
    gap = (today - dt.date()).days
    if gap == 1:
        return f"昨天 {hm_}"
    if gap == 2:
        return f"前天 {hm_}"
    if dt.year == today.year:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def format_rel(ts: float, now: float | None = None, tz_name: str = "") -> str:
    """相对时间（概览用）：刚刚/N 分钟前/N 小时前/N 分钟后/N 小时后；

    超出 ±48h 退化为绝对标注（format_ts）。tz 仅为退化时服务。"""
    now = now if now is not None else _time.time()
    diff = ts - now
    adiff = abs(diff)
    if adiff < 90:
        return "刚刚" if diff <= 0 else "马上"
    mins = adiff / 60
    if mins < 60:
        return f"{mins:.0f} 分钟{'后' if diff > 0 else '前'}"
    hours = mins / 60
    if hours < 48:
        return f"{hours:.0f} 小时{'后' if diff > 0 else '前'}"
    return format_ts(ts, now=now, tz_name=tz_name)
