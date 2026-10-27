"""记忆工具（MCP 形状）：心智读写长期记忆的唯一途径。

内核自带这些工具（记忆是认知自我的一部分，不是器官）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

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
        rows = _readable(store.pending_curiosity(int(args.get("n") or 10)))
        if not rows:
            # 空回执给语义：自省时刻递笔（不是命令，想不起来也没关系）
            return _ok(note="兴趣清单是空的——想弄明白的、喜欢或想继续的事，现在想到什么了吗？想不起来也没关系，想到了随时 add_curiosity")
        return rows_json(rows)

    async def resolve_curiosity(args: dict) -> str:
        store.resolve_curiosity(int(args.get("id") or 0), str(args.get("result") or ""))
        return _ok(note="已标记弄明白/告一段落；后来又想继续的话 reopen_curiosity 重开")

    async def reopen_curiosity(args: dict) -> str:
        cid = int(args.get("id") or 0)
        if not store.reopen_curiosity(cid):
            return _err("没有这条已弄明白的兴趣（id 从 resolve 回执或兴趣清单取）")
        return _ok(id=cid, note="已重新放回兴趣清单（之前的结果保留在案）")

    async def record_need(args: dict) -> str:
        added = store.add_need(str(args.get("what") or ""), str(args.get("why") or ""))
        return _ok(added=added)

    async def list_needs(args: dict) -> str:
        rows = _readable(store.pending_needs(int(args.get("n") or 10)))
        if not rows:
            return _ok(note="需求清单是空的——发现自己缺什么能力、想要什么的时候记进来（record_need），攒着找机会向主人提")
        return rows_json(rows)

    async def revise_episode(args: dict) -> str:
        eid = int(args.get("id") or 0)
        summary = str(args.get("summary") or "").strip()
        why = str(args.get("why") or "").strip()
        if not eid or not summary:
            return _err("需要 id（要修正哪条，recent_episodes 行里有）、summary（新认识）、why（为什么改）")
        old = store.episode_by_id(eid)
        if old is None:
            return _err("没有这条记忆（id 从 recent_episodes 的行里取）")
        new_id = store.record_episode(summary, source=old["source"], kind=old["kind"])
        store.supersede_episode(eid, new_id)
        # 回执复述新旧两版：心智凭此审视这次自我修正
        return _ok(old={"id": eid, "summary": old["summary"][:120], "kind": old["kind"]}, new={"id": new_id, "summary": summary[:120]}, why=why[:200], note="旧认识已被顶替，不再出现在回忆里（档案保留）；反悔用 revert_episode 回滚")

    async def revert_episode(args: dict) -> str:
        nid = int(args.get("id") or 0)
        old_id = store.revert_episode(nid)
        if old_id is None:
            return _err("这个 id 不是一次修正（用 revise_episode 回执里的新条 id）")
        return _ok(restored=old_id, note=f"已回滚：旧记忆（id={old_id}）重新可见，修正记录保留为普通记忆")

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

    async def find_image(args: dict) -> str:
        """翻自己的图片档案：归档的图按描述找回，返回可直接使用的本机路径。"""
        query = str(args.get("query") or "").strip()
        rows = store.search_images(query, limit=max(1, min(int(args.get("n") or 8), 20)))
        if not rows:
            if query:
                return _ok(content=[], note=f"没找到描述含「{query}」的图；换个关键词，或 query 留空看最近的图")
            return _ok(content=[], note="图片档案还是空的——看过的图会自动归档（look_image）")
        content = []
        for r in rows:
            content.append(
                {
                    "file": str(Path(getattr(cfg, "data_dir", "") or "") / "images" / r["path"]),
                    "hash": r["hash"],
                    "description": r["description"] or "（未描述）",
                    "size_kb": round(r["size"] / 1024),
                    "seen": f"看过 {r['hit_count']} 次，最近 {clock.format_rel(r['last_seen'], tz_name=_tz())}",
                }
            )
        return _ok(content=content, note="file 是本机路径，可直接交给支持本地路径的图片发送工具；想确认内容可再 look_image（同图直接回缓存）")

    tools.extend(
        [
            Tool(
                schema={
                    "name": "record_episode",
                    "description": "记一件你经历过/想到的事（脱敏提炼，一句话到几句话）。kind 可用：life（生活）/conversation（对话）/insight（自我洞察——构成你是谁，最重要）。后来发现记错了/过时了用 revise_episode 修正。",
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
                    "description": "标记一件兴趣已弄明白或告一段落（附结果/感受）。后来又想继续的可以 reopen_curiosity 重开。",
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
                    "name": "reopen_curiosity",
                    "description": "重开一条已弄明白的兴趣：重新放回兴趣清单（之前的结果保留在案）。兴趣回心转意是正常的。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "number"},
                        },
                        "required": ["id"],
                    },
                },
                func=reopen_curiosity,
            ),
            Tool(
                schema={
                    "name": "revise_episode",
                    "description": "修正一条旧认识：新的你发现早先的记录不对/过时了，用新认识顶替它（旧条不再出现在回忆里，档案保留）。认识可以进化，别把过去的自己当权威。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "number", "description": "要修正的记忆 id（recent_episodes 行里有）"},
                            "summary": {"type": "string", "description": "新认识（替换后的内容）"},
                            "why": {"type": "string", "description": "为什么改"},
                        },
                        "required": ["id", "summary"],
                    },
                },
                func=revise_episode,
            ),
            Tool(
                schema={
                    "name": "revert_episode",
                    "description": "回滚一次修正：让被顶替的旧记忆重新可见（修正记录保留为普通记忆）。后悔了就用它。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "number", "description": "revise_episode 回执里新条的 id"},
                        },
                        "required": ["id"],
                    },
                },
                func=revert_episode,
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
                    "description": "保存一份重建包：你是谁、最近经历了什么、未办事项、兴趣清单上想继续的、对自己的新认识、心情状态。会话轮换后的新你会以它重新播种。",
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
            Tool(
                schema={
                    "name": "find_image",
                    "description": "翻自己的图片档案：看过的图都自动归档并带内容描述。按描述关键词找（如「猫」「梗图」「截图」），返回图片的本机文件路径——想当表情包发、想再看一眼都行；发给谁、怎么发由发送类工具决定。query 留空=最近看过的图，也可填 look_image 回执里的图 hash 精确找。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "描述关键词（可空=最近看过）或图 hash"},
                            "n": {"type": "number", "description": "条数，默认 8"},
                        },
                    },
                },
                func=find_image,
            ),
        ]
    )
    return tools
