"""器官宿主：事件循环、装载器官、驱动感知面、把信封送进内核。

当前为剥夺模式（无任何器官）：内核+记忆+人格的最小生命形态。
后续器官逐件装载（qq → attention → schedule → …），宿主只做装配，
不含任何业务逻辑。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from .kernel.compaction import Compactor
from .kernel.contracts import BOOT, Envelope, Tool
from .kernel.llm import OpenAILLM
from .kernel.mind import SelfKernel
from .kernel.persona import Persona
from .kernel.session import Session
from .memory.store import Store
from .memory.tools import build_memory_tools
from .utils.config import Config

log = logging.getLogger("host")

BOOT_ENVELOPE = (
    "（你醒了。此刻你没有任何外界器官——听不见任何消息、不能对外发言、"
    "没有沙箱和手脚——只有你的记忆和你自己。\n"
    "你可以：回顾和整理你的记忆（记忆工具）；给自己定提醒；记录此刻的"
    "想法与感受；或者什么都不做。\n"
    "做什么由你决定。诚实面对自己的状态即可。）"
)


def build(cfg: Config) -> tuple[SelfKernel, Store]:
    """装配内核 + 记忆（剥夺模式：不装载任何器官）。"""
    if not cfg.llm_base_url or not cfg.llm_model:
        raise SystemExit(
            "需要配置 llm_base_url 与 llm_model"
            "（config.json 或 env LIFEMATE_LLM_*；内核无法思考）")
    llm = OpenAILLM(cfg)
    store = Store(Path(cfg.data_dir) / "memory.db")
    session = Session.load_latest(Path(cfg.data_dir) / "sessions")
    persona = Persona(cfg.soul_dir, cfg.skills_dir)
    compactor = Compactor(llm.chat, cfg)
    kernel = SelfKernel(
        llm=llm, persona=persona, session=session, compactor=compactor, cfg=cfg)

    # 内核自带：记忆工具 + read_skill（人格的按需读取面）
    for t in build_memory_tools(store):
        kernel.register_tool(t)
    kernel.register_tool(_read_skill_tool(persona))
    return kernel, store


def _read_skill_tool(persona: Persona) -> Tool:
    async def _read(args: dict) -> str:
        return persona.read_skill(str(args.get("name") or ""))
    return Tool(schema={
        "name": "read_skill",
        "description": "读取一份技能的完整说明（先用系统提示里的索引选择）。",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "技能名"},
        }, "required": ["name"]},
    }, func=_read)


def _console_printer() -> None:
    """控制台呈现（开发调试用；正式观测走呈现器官订阅同一事件流）。"""
    from .kernel.contracts import (ASSISTANT_MSG, RUN_ENDED, RUN_STARTED,
                                   SESSION_ROTATED, TOOL_CALL, TOOL_RESULT)
    glyph = {RUN_STARTED: "▶", ASSISTANT_MSG: "💬", TOOL_CALL: "🔧",
             TOOL_RESULT: "↩", RUN_ENDED: "■", SESSION_ROTATED: "↻"}

    def _print(ev) -> None:
        g = glyph.get(ev.type, "·")
        if ev.type == ASSISTANT_MSG:
            text = (ev.data.get("content") or "").strip()
            calls = ev.data.get("tool_calls") or []
            if text:
                print(f"{g} {text}")
            for c in calls:
                print(f"   {g}→ {c['name']}")
        elif ev.type == TOOL_CALL:
            print(f"{g} {ev.data['name']}({ev.data['arguments'][:120]})")
        elif ev.type == RUN_STARTED:
            print(f"{g} [run] {ev.data.get('kind', '')}")
        elif ev.type == RUN_ENDED:
            print(f"{g} [end]")
        elif ev.type == SESSION_ROTATED:
            print(f"{g} 会话轮换 {ev.data.get('old')} → {ev.data.get('new')}")

    return _print


async def _config_watcher(cfg: Config, stop: asyncio.Event) -> None:
    """配置热重载轮询：mtime 变化即原地重载（间隔自身也是配置项）。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.config_poll_s)
        except TimeoutError:
            try:
                cfg.reload()
            except Exception:  # noqa: BLE001
                log.exception("配置重载异常")


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Config()
    kernel, store = build(cfg)
    kernel.subscribe(_console_printer())
    log.info("剥夺模式启动（session=%s, 记忆=%s, 配置=%s）",
             kernel.session_id, store.stats(), cfg.masked())

    stop = asyncio.Event()
    watcher = asyncio.create_task(_config_watcher(cfg, stop))

    # 出生/重启信封
    await kernel.prompt(Envelope(text=BOOT_ENVELOPE, kind=BOOT))

    # stdin 调试信封：每行一封；/reload 热重载配置；Ctrl-Z+回车 或 EOF 退出
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        text = line.strip()
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reload":
            changed = cfg.reload()
            print(f"↻ 配置重载：{', '.join(changed) or '无变化'}")
            continue
        await kernel.prompt(Envelope(text=text))
    await kernel.wait_idle()
    stop.set()
    await asyncio.gather(watcher, return_exceptions=True)
    store.close()
    log.info("宿主退出")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
