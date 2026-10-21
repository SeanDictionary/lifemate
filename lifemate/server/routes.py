"""核心路由表：全部 /api/* 处理器（闭包持有 Server 状态）。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..kernel.contracts import DIRECT, Envelope
from ..utils.config import _ENV_SEED
from .history import available_dates, read_day, today, usage_of_day
from .http import Route


def _int_q(val: str | None, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


def build_core_routes(srv) -> list[Route]:
    kernel, store, cfg = srv.kernel, srv.store, srv.cfg
    observe_dir = Path(cfg.data_dir) / "observe"

    # ── 状态 ──────────────────────────────────────────
    async def status(req, p):
        return {"ok": True, "version": __version__,
                "uptime_s": int(time.time() - srv.t0),
                "session_id": kernel.session_id, "model": cfg.llm_model,
                "busy": bool(getattr(kernel, "is_busy", False)),
                "plugins": [pl["name"] for pl in srv.plugin_panels()],
                "tool_count": len(kernel.tool_names()), "now": int(time.time())}

    # ── 档案 ──────────────────────────────────────────
    async def dates(req, p):
        return {"ok": True, "dates": available_dates(observe_dir)}

    async def history(req, p):
        date = req.query.get("date", "")
        if not date:
            return {"ok": False, "error": "需要 date=YYYY-MM-DD"}
        result = read_day(observe_dir, date,
                          rid=req.query.get("rid", ""),
                          limit=_int_q(req.query.get("limit"), 500, 1, 2000),
                          offset=_int_q(req.query.get("offset"), 0, 0, 10**9))
        if result is None:
            return {"ok": False, "error": f"无档案 {date}",
                    "available": available_dates(observe_dir)}
        return {"ok": True, "count": len(result["rows"]),
                "total": result["total"], "rows": result["rows"]}

    async def usage(req, p):
        return {"ok": True, "date": req.query.get("date") or today(),
                "by_model": usage_of_day(
                    observe_dir, req.query.get("date") or today())}

    # ── 对话 ──────────────────────────────────────────
    async def say(req, p):
        text = str(req.json().get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text 不能为空"}
        kernel.notify(Envelope(text=text, kind=DIRECT,
                               source="webui", origin="server"))
        srv.chatbus.record("in", text)
        return {"ok": True}

    async def chat(req, p):
        return {"ok": True, "messages": srv.chatbus.recent(
            _int_q(req.query.get("limit"), 100, 1, 200))}

    # ── 插件清单 ──────────────────────────────────────
    async def plugins(req, p):
        return {"ok": True, "plugins": srv.plugin_panels()}

    # ── 配置（读 / 改存 / 重载） ───────────────────────
    async def config_get(req, p):
        return {"ok": True, "config": cfg.masked()}

    async def config_post(req, p):
        result = cfg.apply_updates(req.json())
        return {"ok": not result["unknown"], **result,
                "env_locked": [k for k, e in _ENV_SEED.items()
                               if os.environ.get(e)]}

    async def config_reload(req, p):
        changed = cfg.reload()
        return {"ok": True, "changed": changed}

    # ── 记忆浏览（只读，复用 store 现成方法） ───────────
    async def memory(req, p):
        table = p.get("table", "")
        n = _int_q(req.query.get("limit"), 20, 1, 200)
        if table == "episodes":
            return {"ok": True, "rows": store.recent_episodes(n=n)}
        if table == "curiosity":
            return {"ok": True, "rows": store.pending_curiosity(n=n)}
        if table == "needs":
            return {"ok": True, "rows": store.pending_needs(n=n)}
        if table == "reminders":
            return {"ok": True, "rows": store.pending_reminders(n=n)}
        return {"ok": False,
                "error": "table ∈ episodes/curiosity/needs/reminders"}

    return [
        Route("GET", "/api/status", status),
        Route("GET", "/api/dates", dates),
        Route("GET", "/api/history", history),
        Route("GET", "/api/usage", usage),
        Route("POST", "/api/say", say),
        Route("GET", "/api/chat", chat),
        Route("GET", "/api/plugins", plugins),
        Route("GET", "/api/config", config_get),
        Route("POST", "/api/config", config_post),
        Route("POST", "/api/config/reload", config_reload),
        Route("GET", "/api/memory/{table}", memory),
    ]
