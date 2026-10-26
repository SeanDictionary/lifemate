"""记忆工具（MCP 形状）：心智读写长期记忆的唯一途径。

内核自带这些工具（记忆是认知自我的一部分，不是器官）。
"""

from __future__ import annotations

import json
import time

from ..kernel.contracts import Tool
from ..utils import clock
from .store import Store, rows_json


def _ok(**kw) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def build_memory_tools(store: Store, cfg=None) -> list[Tool]:
    """构建绑定到某个 Store 实例的记忆工具集。

    cfg：全局活配置（timezone——记忆是一生的时间轴，按主体时区给人读）；
    缺省 None = 本机时区（测试/剥夺模式）。
    """
    tools: list[Tool] = []

    def _tz() -> str:
        return getattr(cfg, "timezone", "") or ""

    def _readable(rows: list[dict]) -> list[dict]:
        """行内时间字段转人读：ts→time、due_at→due（相对）。

        裸 epoch 不进心智——"昨天记的"和"三个月前记的"不能是同一种数字串。"""
        out = []
        for r in rows:
            r = dict(r)
            if "ts" in r:
                r["time"] = clock.format_ts(r.pop("ts"), tz_name=_tz())
            if "due_at" in r:
                r["due"] = clock.format_rel(r.pop("due_at"), tz_name=_tz())
            out.append(r)
        return out

    async def record_episode(args: dict) -> str:
        summary = str(args.get("summary") or "").strip()
        if not summary:
            return _err("summary 不能为空")
        store.record_episode(summary, source=str(args.get("source") or ""), kind=str(args.get("kind") or "life"), salience=float(args.get("salience") or 1.0))
        return _ok()

    async def recent_episodes(args: dict) -> str:
        rows = store.recent_episodes(n=int(args.get("n") or 10), since_hours=float(args.get("hours") or 48), exclude_source=str(args.get("exclude_source") or ""))
        return rows_json(_readable(rows))

    async def add_curiosity(args: dict) -> str:
        added = store.add_curiosity(str(args.get("topic") or ""), str(args.get("source") or ""))
        return _ok(added=added) if added else _ok(added=False, note="7 天内已有同话题")

    async def list_curiosity(args: dict) -> str:
        return rows_json(_readable(store.pending_curiosity(int(args.get("n") or 10))))

    async def resolve_curiosity(args: dict) -> str:
        store.resolve_curiosity(int(args.get("id") or 0), str(args.get("result") or ""))
        return _ok()

    async def record_need(args: dict) -> str:
        added = store.add_need(str(args.get("what") or ""), str(args.get("why") or ""))
        return _ok(added=added)

    async def list_needs(args: dict) -> str:
        return rows_json(_readable(store.pending_needs(int(args.get("n") or 10))))

    async def set_reminder(args: dict) -> str:
        intent = str(args.get("intent") or "").strip()
        delay = float(args.get("delay_minutes") or 0)
        if not intent or delay <= 0:
            return _err("需要 intent 和正数 delay_minutes")
        delay = max(delay, 1.0)  # 最短延迟 1 分钟（钳制）
        rid = store.add_reminder(time.time() + delay * 60, intent, str(args.get("repeat") or "none"))
        return _ok(id=rid)

    async def list_reminders(args: dict) -> str:
        return rows_json(_readable(store.pending_reminders(int(args.get("n") or 10))))

    async def save_rebuild_packet(args: dict) -> str:
        content = str(args.get("content") or "").strip()
        if not content:
            return _err("content 不能为空")
        store.save_packet(content)
        return _ok()

    async def latest_rebuild_packet(args: dict) -> str:
        row = store.latest_packet()
        if row is None:
            return _err("还没有重建包")
        return json.dumps({"ok": True, "content": row["content"], "written": clock.format_ts(row["ts"], tz_name=_tz())}, ensure_ascii=False)

    tools.extend(
        [
            Tool(
                schema={
                    "name": "record_episode",
                    "description": "记一件你经历过/想到的事（脱敏提炼，一句话到几句话）。kind 可用：life（生活）/conversation（对话）/insight（自我洞察）。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "这件事的概括"},
                            "kind": {"type": "string", "description": "life/conversation/insight"},
                            "source": {"type": "string", "description": "来源会话标识，可空"},
                            "salience": {"type": "number", "description": "重要程度 0.1-2.0，默认 1.0"},
                        },
                        "required": ["summary"],
                    },
                },
                func=record_episode,
            ),
            Tool(
                schema={
                    "name": "recent_episodes",
                    "description": "回忆最近的经历（默认近 48 小时）。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "n": {"type": "number", "description": "条数，默认 10"},
                            "hours": {"type": "number", "description": "回溯小时数，默认 48"},
                        },
                    },
                },
                func=recent_episodes,
            ),
            Tool(
                schema={
                    "name": "add_curiosity",
                    "description": "记下一件你感兴趣的事：想弄明白的好奇、喜欢或想尝试的爱好、想深入了解的领域——都放这张兴趣清单。趁自己的时间去看去试，弄明白或告一段落后 resolve_curiosity 附上结果。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "兴趣/好奇的事（简短）"},
                        },
                        "required": ["topic"],
                    },
                },
                func=add_curiosity,
            ),
            Tool(
                schema={
                    "name": "list_curiosity",
                    "description": "看看兴趣清单上还有什么：想弄明白的、想继续的爱好。",
                    "inputSchema": {"type": "object", "properties": {"n": {"type": "number"}}},
                },
                func=list_curiosity,
            ),
            Tool(
                schema={
                    "name": "resolve_curiosity",
                    "description": "标记一件好奇的事已弄明白（附结果）。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "number"},
                            "result": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                },
                func=resolve_curiosity,
            ),
            Tool(
                schema={
                    "name": "record_need",
                    "description": "记下一条你想向主人提出的升级需求（能力缺口）。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "what": {"type": "string", "description": "想要什么"},
                            "why": {"type": "string", "description": "为什么需要"},
                        },
                        "required": ["what"],
                    },
                },
                func=record_need,
            ),
            Tool(
                schema={
                    "name": "list_needs",
                    "description": "看看攒了哪些还没提的升级需求。",
                    "inputSchema": {"type": "object", "properties": {"n": {"type": "number"}}},
                },
                func=list_needs,
            ),
            Tool(
                schema={
                    "name": "set_reminder",
                    "description": "给自己定一个提醒（自我时间）。到点系统会唤醒你去办。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "delay_minutes": {"type": "number", "description": "多少分钟后提醒（最短 1 分钟）"},
                            "intent": {"type": "string", "description": "到点要做什么"},
                            "repeat": {"type": "string", "description": "none 或 daily"},
                        },
                        "required": ["delay_minutes", "intent"],
                    },
                },
                func=set_reminder,
            ),
            Tool(
                schema={
                    "name": "list_reminders",
                    "description": "看看自己定了哪些待办提醒。",
                    "inputSchema": {"type": "object", "properties": {"n": {"type": "number"}}},
                },
                func=list_reminders,
            ),
            Tool(
                schema={
                    "name": "save_rebuild_packet",
                    "description": "保存一份重建包：你是谁、最近经历了什么、未办事项、心情状态。会话轮换后的新你会以它重新播种。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "写给下一个自己的信"},
                        },
                        "required": ["content"],
                    },
                },
                func=save_rebuild_packet,
            ),
            Tool(
                schema={
                    "name": "latest_rebuild_packet",
                    "description": "读上一个自己留下的重建包。",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                func=latest_rebuild_packet,
            ),
        ]
    )
    return tools
