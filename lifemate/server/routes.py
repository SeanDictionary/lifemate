"""核心路由表：全部 /api/* 处理器（闭包持有 Server 状态）。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .. import __version__
from ..kernel.contracts import DIRECT, Envelope
from ..kernel.persona import Persona
from ..utils.config import _ENV_SEED
from .history import available_dates, read_day, today, usage_of_day
from .http import _MIME, Route, json_resp, resp_bytes


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
        return {"ok": True, "version": __version__, "uptime_s": int(time.time() - srv.t0), "session_id": kernel.session_id, "model": cfg.llm_model, "busy": bool(getattr(kernel, "is_busy", False)), "plugins": [pl["name"] for pl in srv.plugin_panels()], "tool_count": len(kernel.tool_names()), "now": int(time.time())}

    # ── 档案 ──────────────────────────────────────────
    async def dates(req, p):
        return {"ok": True, "dates": available_dates(observe_dir)}

    async def history(req, p):
        # date 缺省 = 最新有档案的一天（概览尾部轮询用，跨日零点也拿得到最近记录）
        date = req.query.get("date", "")
        if not date:
            avail = available_dates(observe_dir)
            if not avail:
                return {"ok": False, "error": "无档案", "available": []}
            date = avail[-1]
        result = read_day(observe_dir, date, rid=req.query.get("rid", ""), limit=_int_q(req.query.get("limit"), 500, 1, 2000), offset=_int_q(req.query.get("offset"), 0, 0, 10**9), tail=_int_q(req.query.get("tail"), 0, 0, 2000))
        if result is None:
            return {"ok": False, "error": f"无档案 {date}", "available": available_dates(observe_dir)}
        return {"ok": True, "count": len(result["rows"]), "total": result["total"], "rows": result["rows"]}

    async def usage(req, p):
        return {"ok": True, "date": req.query.get("date") or today(), "by_model": usage_of_day(observe_dir, req.query.get("date") or today())}

    # ── 对话 ──────────────────────────────────────────
    async def say(req, p):
        text = str(req.json().get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text 不能为空"}
        kernel.notify(Envelope(text=text, kind=DIRECT, source="webui", origin="server"))
        srv.chatbus.record("in", text)
        return {"ok": True}

    async def chat(req, p):
        return {"ok": True, "messages": srv.chatbus.recent(_int_q(req.query.get("limit"), 100, 1, 200))}

    # ── 插件清单 ──────────────────────────────────────
    async def plugins(req, p):
        return {"ok": True, "plugins": srv.plugin_panels()}

    # ── 技能清单（人格装配的知情层：skills/ + 插件 SKILL.md） ──
    def _persona() -> Persona:
        """惰性单例（Persona 自带 mtime 缓存，热改 SKILL.md 即时可见）。"""
        if getattr(srv, "_persona", None) is None:
            srv._persona = Persona(cfg.soul_dir, cfg.skills_dir)
        return srv._persona

    async def skills(req, p):
        items = _persona().skills_catalog()
        return {"ok": True, "skills": [{"name": s["name"], "description": s["description"], "source": s["source"]} for s in items]}

    async def skill_detail(req, p):
        name = p.get("name", "")
        for s in _persona().skills_catalog():
            if s["name"] == name:
                return {"ok": True, "name": name, "content": s["content"], "source": s["source"]}
        return {"ok": False, "error": f"skill 不存在: {name}"}

    # ── 工具清单 ────────────────────────────────────
    async def tools(req, p):
        """全部已装载工具（含来源/参数 schema/可打断标记）。"""
        detail = getattr(kernel, "tools_detail", None)
        if callable(detail):
            return {"ok": True, "tools": detail()}
        return {"ok": True, "tools": [{"name": n, "description": "", "inputSchema": {}, "interruptible": False, "source": "内核"} for n in kernel.tool_names()]}

    # ── 配置（读 / 改存 / 重载 / 说明书） ───────────
    async def config_get(req, p):
        return {"ok": True, "config": cfg.masked()}

    async def config_schema(req, p):
        """配置说明书（声明投影）：前端设置页数据驱动渲染的依据。

        env_set：当前真被环境变量占用的主配置键（前端标"env 锁定"）。"""
        return {"ok": True, **cfg.schema_dict(), "env_set": [k for k, e in _ENV_SEED.items() if os.environ.get(e)]}

    async def config_post(req, p):
        result = cfg.apply_updates(req.json())
        return {"ok": not result["unknown"], **result, "env_locked": [k for k, e in _ENV_SEED.items() if os.environ.get(e)]}

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
        if table == "images":
            return {"ok": True, "rows": store.recent_images(n=n)}
        return {"ok": False, "error": "table ∈ episodes/curiosity/needs/reminders/images"}

    async def memory_image(req, p):
        """看过的原图（字节直出，img 标签直接可用；token 走查询参数）。"""
        import re as _re

        h = p.get("hash", "")
        if not _re.fullmatch(r"[0-9a-f]{64}", h):
            return json_resp({"ok": False, "error": "bad hash"}, 400)
        row = store.find_image(h)
        if row is None:
            return json_resp({"ok": False, "error": "not found"}, 404)
        name = Path(row["path"]).name  # 防穿越：只取文件名
        f = (Path(cfg.data_dir) / "images" / name).resolve()
        if not f.is_file():
            return json_resp({"ok": False, "error": "file gone"}, 404)
        ctype = _MIME.get(f.suffix.lower(), "application/octet-stream")
        # 内容寻址（hash 即文件名）：可长缓存
        return resp_bytes(200, ctype, f.read_bytes(), {"Cache-Control": "private, max-age=86400"})

    return [
        Route("GET", "/api/status", status),
        Route("GET", "/api/dates", dates),
        Route("GET", "/api/history", history),
        Route("GET", "/api/usage", usage),
        Route("POST", "/api/say", say),
        Route("GET", "/api/chat", chat),
        Route("GET", "/api/plugins", plugins),
        Route("GET", "/api/config", config_get),
        Route("GET", "/api/config/schema", config_schema),
        Route("POST", "/api/config", config_post),
        Route("POST", "/api/config/reload", config_reload),
        Route("GET", "/api/memory/{table}", memory),
        Route("GET", "/api/memory/image/{hash}", memory_image),
        Route("GET", "/api/tools", tools),
        Route("GET", "/api/skills", skills),
        Route("GET", "/api/skills/{name}", skill_detail),
    ]
