"""器官宿主：事件循环、装载器官、驱动感知面、把信封送进内核。

当前为剥夺模式（无任何器官）：内核+记忆+人格的最小生命形态。
后续器官逐件装载（schedule → qq → attention → …），宿主只做装配。

基建职责（7×24 守护进程化）：
- 单实例锁（防双开损坏数据）
- 全局日志（.log 轮转 + 未捕获异常兜底）
- 优雅停机（信号 → 中止思维 → flush 档案 → 关库）
- watchdog（僵尸队列复活 / 卡死运行处置）
- 磁盘治理（observe 保留期清理 / memory.db 定期备份）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
import time
from pathlib import Path

from . import __version__
from .kernel.compaction import Compactor
from .kernel.contracts import BOOT, REMINDER, Envelope, Tool
from .kernel.llm import OpenAILLM
from .kernel.mind import SelfKernel
from .kernel.persona import Persona
from .kernel.session import Session
from .memory.store import Store
from .memory.tools import build_memory_tools
from .utils import instance
from .utils.config import Config
from .utils.log import (get_logger, install_excepthooks, install_loop_handler,
                        level_from_name, setup_logging)
from .utils.recorder import Recorder
from .console import Console
from .plugins import PluginContext, load_plugins, stop_plugins

log = get_logger("host")

BOOT_ENVELOPE = (
    "（你醒了。此刻你没有任何外界器官——听不见任何消息、不能对外发言、"
    "没有沙箱和手脚——只有你的记忆和你自己。\n"
    "你可以：回顾和整理你的记忆（记忆工具）；给自己定提醒或闹钟（wait）；"
    "记录此刻的想法与感受；或者什么都不做。\n"
    "做什么由你决定。诚实面对自己的状态即可。）"
)

RESUME_ENVELOPE = (
    "（进程重启了。你上次的活动被打断——会话与记忆已恢复，"
    "从中断处继续即可。如果你正在等待什么，重新等。）"
)


def _boot_envelope(plugin_names: list[str]) -> str:
    """出生信封：如实告知身体状态（不搬谎——它据此建立自我模型）。"""
    if not plugin_names:
        return BOOT_ENVELOPE
    names = "、".join(plugin_names)
    return (
        f"（你醒了。你此刻装配的身体：{names}——外界的动静会唤醒你。"
        "动手之前，建议先用 read_skill 读一下相应平台的技能说明"
        "（索引就在你视野里）；没有装配的能力不要假装有。"
        "做什么由你决定。诚实面对自己的状态即可。）")


def build(cfg: Config) -> tuple[SelfKernel, Store, Recorder]:
    """装配内核 + 记忆 + 观测记录器（剥夺模式：不装载任何器官）。"""
    if not cfg.llm_base_url or not cfg.llm_model:
        raise SystemExit(
            "需要配置 llm_base_url 与 llm_model"
            "（config.json 或 env LIFEMATE_LLM_*；内核无法思考）")

    # 观测记录器先行（黑匣子原则：任何后续装配失败也有档可查）
    recorder = Recorder(Path(cfg.data_dir) / "observe",
                        sid_fn=lambda: kernel.session_id,  # noqa: F821 惰性闭包
                        max_field_chars=cfg.observe_max_field_chars)

    llm = OpenAILLM(
        cfg,
        on_persistent_failure=lambda streak: recorder.event(
            "error", where="llm",
            error=f"连续失败 {streak} 次——自主性受影响，请检查网关/渠道"))

    store = Store(Path(cfg.data_dir) / "memory.db")
    session = Session.load_latest(Path(cfg.data_dir) / "sessions")
    persona = Persona(cfg.soul_dir, cfg.skills_dir)
    compactor = Compactor(llm.chat, cfg)
    kernel = SelfKernel(
        llm=llm, persona=persona, session=session, compactor=compactor, cfg=cfg)
    kernel.subscribe(recorder.on_kernel_event)

    # 内核自带：记忆工具 + read_skill + deep_sleep（wait 由内核内置；
    # 插件能力由 plugins/ 自动发现装载，见 run()）
    for t in build_memory_tools(store):
        kernel.register_tool(t)
    kernel.register_tool(_read_skill_tool(persona))
    kernel.register_tool(_deep_sleep_tool(kernel, store))

    # 唤醒附加事实（身体感受：待办提醒概览——内核不解其义，只附上）
    def _extras() -> str:
        pend = store.pending_reminders(5)
        if not pend:
            return ""
        items = "；".join(r["intent"][:30] for r in pend[:3])
        return f"（你还有 {len(pend)} 条待办提醒，如：{items}。）"
    kernel.add_wake_extras(_extras)

    return kernel, store, recorder


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


def _deep_sleep_tool(kernel: SelfKernel, store: Store) -> Tool:
    """整觉：写信（重建包）→ 转身（换会话播种）→ 长眠。睡眠制度。"""

    async def _fn(args: dict) -> str:
        import json as _json
        letter = str(args.get("letter") or "").strip()
        if not letter:
            return _json.dumps(
                {"ok": False,
                 "error": "letter 不能为空——睡前给下一个自己写信是制度"},
                ensure_ascii=False)
        hours = max(0.1, min(float(args.get("hours") or 8), 72.0))
        store.save_packet(letter)
        pend = store.pending_reminders(5)
        todo = "；".join(r["intent"][:40] for r in pend) or "无"
        seed = ("（你刚从深睡中醒来，这是新会话的开始。"
                "上一个你留给你的信：\n" + letter
                + "\n\n你定的待办提醒：" + todo + "）")
        kernel.swap_session(seed)
        return await kernel.rest(hours * 3600)

    return Tool(schema={
        "name": "deep_sleep",
        "description": "整觉：给下一个自己写信（必填）→ 轮换新会话 → 睡指定小时。"
                       "一天的结束用它；轻度小睡用 wait 即可。",
        "inputSchema": {"type": "object", "properties": {
            "letter": {"type": "string",
                       "description": "写给下一个自己的信（我是谁/最近经历/未办/心情）"},
            "hours": {"type": "number", "description": "睡多久（小时，默认 8）"},
        }, "required": ["letter"]}},
        func=_fn, interruptible=False)


def _console_printer() -> None:
    """控制台呈现（开发调试用；正式观测走 observe 档案 + replay）。"""
    from .kernel.contracts import (ASSISTANT_MSG, REST, SESSION_ROTATED,
                                   TOOL_CALL, TOOL_RESULT, WAKE)
    glyph = {WAKE: "☀", ASSISTANT_MSG: "💬", TOOL_CALL: "🔧",
             TOOL_RESULT: "↩", REST: "🌙", SESSION_ROTATED: "↻"}

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
        elif ev.type == WAKE:
            print(f"{g} [唤醒] {ev.data.get('trigger', '')}")
        elif ev.type == REST:
            print(f"{g} [休息] {ev.data.get('reason', '')}")
        elif ev.type == SESSION_ROTATED:
            print(f"{g} 会话轮换 {ev.data.get('old')} → {ev.data.get('new')}")

    return _print


# ── 后台基建任务 ────────────────────────────────────────

async def _config_watcher(cfg: Config, stop: asyncio.Event,
                          recorder: Recorder) -> None:
    """配置热重载轮询：mtime 变化即原地重载（间隔自身也是配置项）。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.config_poll_s)
        except TimeoutError:
            try:
                changed = cfg.reload()
                if changed:
                    recorder.event("config_reload", changed=changed)
            except Exception:  # noqa: BLE001
                log.exception("配置重载异常")


async def _watchdog(cfg: Config, kernel: SelfKernel,
                    stop: asyncio.Event) -> None:
    """健康巡检：僵尸队列复活 / 卡死运行处置（自愈入口在内核 heal）。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.watchdog_interval_s)
        except TimeoutError:
            try:
                info = await kernel.heal()
                if "healed" in info:
                    log.warning("watchdog 自愈动作：%s", info["healed"])
            except Exception:  # noqa: BLE001
                log.exception("watchdog 巡检异常")


async def _maintenance(cfg: Config, store: Store, recorder: Recorder,
                       stop: asyncio.Event) -> None:
    """磁盘治理：observe 保留期清理 + memory.db 定期在线备份。"""
    last_backup = 0.0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=3600.0)
        except TimeoutError:
            pass
        try:
            removed = recorder.cleanup(cfg.observe_retention_days)
            if removed:
                log.info("观测档案清理：%d 个超期日档", removed)
            if time.time() - last_backup >= cfg.backup_interval_s:
                last_backup = time.time()
                ts = time.strftime("%Y%m%d-%H%M%S")
                dest = store.backup(Path(cfg.data_dir) / "backups" / f"memory-{ts}.db")
                _prune_backups(Path(cfg.data_dir) / "backups", cfg.backup_keep)
                log.info("记忆已备份：%s", dest.name)
        except Exception:  # noqa: BLE001
            log.exception("维护任务异常")


def _prune_backups(directory: Path, keep: int) -> None:
    files = sorted(directory.glob("memory-*.db"), key=lambda f: f.stat().st_mtime)
    for old in files[: max(len(files) - keep, 0)]:
        try:
            old.unlink()
        except OSError:
            pass


# ── 闹钟兑现器 ─────────────────────────────────────────


async def _reminder_poller(cfg: Config, store: Store, kernel: SelfKernel,
                            stop: asyncio.Event) -> None:
    """闹钟兑现器：到期提醒 → notify（它的意志，机制兑现）。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.reminder_poll_s)
        except TimeoutError:
            try:
                for r in store.due_reminders(3):
                    kernel.notify(Envelope(
                        text=f"（你自己定的提醒到点了：{r['intent']}）",
                        kind=REMINDER))
            except Exception:  # noqa: BLE001
                log.exception("提醒轮询异常")


async def run(silence: bool = False, log_level: str | None = None) -> None:
    cfg = None
    lock = None
    kernel = store = recorder = None
    try:
        from .utils.config import Config as _C
        cfg = _C()
        # 单实例锁先于一切数据访问
        lock = instance.acquire(cfg.data_dir)
        level = level_from_name(log_level or cfg.log_level)
        log_path = setup_logging(
            cfg.data_dir, max_bytes=cfg.log_max_bytes,
            backup_count=cfg.log_backup_count,
            level=level, console=not silence)
        install_excepthooks()
        install_loop_handler(asyncio.get_event_loop())
        log.info("日志：%s（level=%s）", log_path, logging.getLevelName(level))

        kernel, store, recorder = build(cfg)
        kernel.subscribe(_console_printer())

        # 插件自动发现装载（plugins/ 目录，零主代码改动）
        from .kernel.persona import Persona
        persona = Persona(cfg.soul_dir, cfg.skills_dir)
        plugins = await load_plugins(PluginContext(
            kernel=kernel, store=store, cfg=cfg, recorder=recorder,
            data_dir=Path(cfg.data_dir) / "plugins"))

        # 启动快照（含插件工具，复现它当时看到的世界）
        recorder.event(
            "boot", version=__version__, config=cfg.masked(),
            persona_hash=hashlib.sha256(
                persona.system_prompt().encode("utf-8")).hexdigest()[:12],
            skills=sorted(persona.skills()), tools=kernel.tool_names())
        recorder.event("session_load",
                       msgs=len(kernel._session.messages),
                       summary_chars=len(kernel._session.summary))

        log.info("剥夺模式启动（session=%s, 记忆=%s, 插件=%s）",
                 kernel.session_id, store.stats(), [p.name for p in plugins])

        stop = asyncio.Event()
        for sig in ("SIGINT", "SIGTERM"):
            if hasattr(signal, sig):
                def _on_signal(s, _f, _loop=asyncio.get_event_loop()):
                    _loop.call_soon_threadsafe(stop.set)
                try:
                    signal.signal(getattr(signal, sig), _on_signal)
                except (ValueError, OSError):
                    pass  # 非主线程/平台不支持时忽略

        tasks = [
            asyncio.create_task(_config_watcher(cfg, stop, recorder)),
            asyncio.create_task(_watchdog(cfg, kernel, stop)),
            asyncio.create_task(_maintenance(cfg, store, recorder, stop)),
            asyncio.create_task(_reminder_poller(cfg, store, kernel, stop)),
        ]

        # 出生/重启信封（永续回合的第一声敲门；如实告知身体状态）
        fresh = len(kernel._session.messages) == 0
        if fresh:
            boot_text = _boot_envelope([p.name for p in plugins])
        else:
            boot_text = RESUME_ENVELOPE
        kernel.notify(Envelope(text=boot_text, kind=BOOT))

        # 开发控制台：stdin 调试信封 + /命令；信号经 stop 事件传入
        console = Console(kernel, cfg, recorder)
        console.start()
        reason = await console.run(stop)

        # 优雅停机：中止思维 → 停后台任务 → flush 档案 → 关库 → 释放锁
        log.info("开始优雅停机（reason=%s）", reason)
        await kernel.abort()
        await kernel.wait_idle()
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stop_plugins(plugins)
        recorder.event("host_stop", reason=reason)
        recorder.close()
        store.close()
        log.info("宿主退出")
    finally:
        instance.release(lock)
 

def main(silence: bool = False, log_level: str | None = None) -> None:
    try:
        asyncio.run(run(silence, log_level))
    except KeyboardInterrupt:
        pass
