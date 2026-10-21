"""observe 档案读取 + LLM 用量聚合（只读，坏行跳过）。

档案布局（recorder 契约）：{data_dir}/observe/YYYY-MM-DD.jsonl，一行一记录。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def available_dates(observe_dir: Path) -> list[str]:
    if not observe_dir.exists():
        return []
    return sorted(f.stem for f in observe_dir.glob("*.jsonl"))


def read_day(observe_dir: Path, date: str, rid: str = "",
             limit: int = 500, offset: int = 0) -> dict[str, Any] | None:
    """读一天档案（可按 rid 过滤、分页）。无档案 → None。"""
    path = observe_dir / f"{date}.jsonl"
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in raw_lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue  # 坏行跳过（半写状态）
        if rid and rec.get("rid") != rid:
            continue
        rows.append(rec)
    return {"total": len(rows), "rows": rows[offset:offset + limit]}


def usage_of_day(observe_dir: Path, date: str) -> dict[str, Any]:
    """LLM 用量分账：按模型聚合 calls/tokens/平均延迟（来自 assistant 记录）。"""
    path = observe_dir / f"{date}.jsonl"
    agg: dict[str, dict[str, float]] = {}
    if not path.exists():
        return {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("type") != "msg" or rec.get("role") != "assistant":
            continue
        if not rec.get("prompt_tokens"):
            continue
        m = str(rec.get("model") or "?")
        a = agg.setdefault(m, {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                               "latency_sum": 0})
        a["calls"] += 1
        a["tokens_in"] += rec.get("prompt_tokens") or 0
        a["tokens_out"] += rec.get("completion_tokens") or 0
        a["latency_sum"] += rec.get("latency_ms") or 0
    out: dict[str, Any] = {}
    for m, a in agg.items():
        out[m] = {"calls": a["calls"], "tokens_in": a["tokens_in"],
                  "tokens_out": a["tokens_out"],
                  "latency_ms_avg": round(a["latency_sum"] / a["calls"])}
    return out


def today() -> str:
    return time.strftime("%Y-%m-%d")
