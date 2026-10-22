"""QQ 面板路由（挂载到 /api/plugins/qq/*，随插件可拆卸）。

契约（插件零 server 依赖，纯鸭子类型）：
- PANEL = {"title", "icon"}                元数据（/api/plugins 声明 → 前端侧栏）
- routes(ctx, plugin) -> [{"method","path","handler"}]
  handler 为 async (req) -> Any（自动 JSON 化，沿用 {ok} 回执约定）
"""

from __future__ import annotations

from typing import Any

PANEL = {"title": "QQ", "icon": "comments"}


def routes(ctx, plugin) -> list[dict[str, Any]]:
    conf = plugin._conf  # 同插件包内访问（声明式配置视图）
    chatlog = plugin._chatlog

    async def status(req) -> dict:
        client = plugin._client
        return {
            "ok": True,
            "connected": bool(client and client.connected),
            "connected_since": (client.connected_since if client else 0.0),
            "bot_uin": conf.bot_uin,
            "listen_groups": conf.listen_groups,
            "unread": chatlog.unread_summary(conf.bot_uin) if chatlog else "",
        }

    async def chats(req) -> dict:
        if chatlog is None:
            return {"ok": False, "error": "chatlog 未初始化"}
        return {"ok": True, "chats": chatlog.chats(conf.bot_uin)}

    async def chatlog_view(req) -> dict:
        """某会话最近消息（不 mark_read——面板查看不算心智已读）。"""
        if chatlog is None:
            return {"ok": False, "error": "chatlog 未初始化"}
        chat = req.query.get("chat", "")
        if not chat:
            return {"ok": False, "error": "需要 chat 参数（如 group_123）"}
        try:
            n = max(1, min(int(req.query.get("n") or 50), 200))
        except ValueError:
            n = 50
        rows = chatlog.recent(chat, n)
        return {"ok": True, "count": len(rows), "rows": rows}

    return [
        {"method": "GET", "path": "status", "handler": status},
        {"method": "GET", "path": "chats", "handler": chats},
        {"method": "GET", "path": "chatlog", "handler": chatlog_view},
    ]
