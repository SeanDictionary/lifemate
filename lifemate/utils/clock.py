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
