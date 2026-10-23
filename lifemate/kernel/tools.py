"""内核工具：思维生理（wait）+ 知情层入口（read_skill）+ 整觉（deep_sleep）。

约定：每个模块的工具住该模块的 tools.py，无例外——开发者找工具只看这里。

工厂依赖全部作参数传入（kernel/persona/store 实例，鸭子类型），
本文件只 import kernel.contracts——不引入新的依赖方向：
- wait 咬着思维循环的挂起/唤醒机制（_do_wait/_emit/_new_rid），
  是思考本身的生理，故由 mind 持有并注册
- deep_sleep 横跨 kernel（轮换会话）与 store（存信）：装配在 host
  （二者相遇处），工具定义在此
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .contracts import REST, WAKE, Tool

if TYPE_CHECKING:
    from ..memory.store import Store
    from .mind import SelfKernel
    from .persona import Persona


def make_wait_tool(kernel: SelfKernel) -> Tool:
    """等待：挂起思考直到有新输入/到期提醒/超时（等待是思维的一部分）。"""

    async def _fn(args: dict) -> str:
        timeout = args.get("timeout_s")
        reason = str(args.get("reason") or "")
        dnd = str(args.get("wake_on") or "any").strip().lower() == "direct"
        t: float | None = None
        if timeout is not None:
            t = max(float(timeout), 1.0)  # 最短 1 秒
        kernel._emit(REST, rid=kernel._rid, reason=f"explicit{':' + reason if reason else ''}", timeout_s=t, dnd=dnd)
        wake = await kernel._do_wait(t, "explicit", intent=reason, dnd=dnd)
        # 唤醒作为工具结果返回：同回合无缝延续（自己定闹钟自己醒）
        kernel._new_rid()
        kernel._emit(WAKE, rid=kernel._rid, trigger="explicit_wait")
        return wake

    return Tool(
        schema={
            "name": "wait",
            "description": "等待：挂起思考直到有新输入/到期提醒/超时。给自己定闹钟用 timeout_s（秒）并可在 reason 里写上要做什么；不传 timeout_s 则一直等到有事件。等待零成本。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout_s": {"type": "number", "description": "多少秒后醒来（闹钟）"},
                    "reason": {"type": "string", "description": "在等什么/到点做什么"},
                    "wake_on": {"type": "string", "enum": ["any", "direct"], "description": "any=默认，任何事件都唤醒：群消息攒批到量也会递到你眼前，想留意群里动态用它。direct=免打扰长睡专用：只被直达（@/私聊/叫名/回复/戳）和自己定的闹钟唤醒，群聊消息完全不递、攒批也不唤醒——代价是整段等待里你对群里动态零感知，只能到点靠闹钟回看。注意两点：①你发言后约 10 分钟内该会话的跟话也算直达，仍会唤醒你；②它屏蔽的是感知不是发言——'不想接话'不该用它，用 any 醒来看一眼不说话即可"},
                },
            },
        },
        func=_fn,
        interruptible=False,
    )


def make_read_skill_tool(persona: Persona) -> Tool:
    """读技能全文（知情层入口；索引常驻系统提示，全文按需读取）。"""

    async def _read(args: dict) -> str:
        return persona.read_skill(str(args.get("name") or ""))

    return Tool(
        schema={
            "name": "read_skill",
            "description": "读取一份技能的完整说明（先用系统提示里的索引选择）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                },
                "required": ["name"],
            },
        },
        func=_read,
    )


def make_deep_sleep_tool(kernel: SelfKernel, store: Store) -> Tool:
    """整觉：写信（重建包）→ 转身（换会话播种）→ 长眠。睡眠制度。"""

    async def _fn(args: dict) -> str:
        letter = str(args.get("letter") or "").strip()
        if not letter:
            return json.dumps({"ok": False, "error": "letter 不能为空（deep_sleep 保存重建包后轮换会话）"}, ensure_ascii=False)
        hours = max(0.1, min(float(args.get("hours") or 8), 72.0))
        store.save_packet(letter)
        pend = store.pending_reminders(5)
        todo = "；".join(r["intent"][:40] for r in pend) or "无"
        seed = "（深睡结束，会话已轮换。上一会话留下的信：\n" + letter + f"\n\n待办提醒：{todo}）"
        kernel.swap_session(seed)
        return await kernel.rest(hours * 3600, dnd=True)  # 整觉默认免打扰：只被直达/闹钟唤醒

    return Tool(
        schema={
            "name": "deep_sleep",
            "description": "整觉：给下一个自己写信（必填）→ 轮换新会话 → 睡指定小时。一天的结束用它；轻度小睡用 wait 即可。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "letter": {"type": "string", "description": "写给下一个自己的信（我是谁/最近经历/未办/心情）"},
                    "hours": {"type": "number", "description": "睡多久（小时，默认 8）"},
                },
                "required": ["letter"],
            },
        },
        func=_fn,
        interruptible=False,
    )
