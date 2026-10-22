"""运行时服务：驱动思维循环 + 基建任务 + 插件装载 + 控制台。

与宿主侧其他服务（将来的 server 网络层）对等：只消费入口
（__main__ 组合根）准备好的共享上下文（kernel/store/recorder/cfg/stop），
不感知其他服务。进程级编排（锁/日志/标记/装配/停机顺序）在入口。

职责：
- 插件自动发现装载（器官 = 身体，归运行时管）
- 出生/重启信封（三态：首启/正常重启/崩溃重启）
- 控制台（stdin 调试信封 + /命令）或服务模式（纯信号驱动）
- 基建任务：配置热重载 / watchdog 自愈 / 磁盘治理 / 闹钟兑现
- 收尾：中止思维 → 停基建任务 → 停插件（档案/关库由入口收尾）
"""
from __future__ import annotations

import asyncio
import hashlib
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
from .utils.config import Config
from .utils import clock
from .utils.log import get_logger
from .utils.recorder import Recorder
from .console import Console
from .plugins import PluginContext, stop_plugins

log = get_logger("host")

BOOT_ENVELOPE = (
    "（系统首次启动。未装配任何外界插件。当前可用能力以工具列表为准，"
    "记忆为空。）"
)

RESUME_ENVELOPE = (
    "（系统启动。上次为正常停机，会话与记忆已恢复。）"
)

CRASH_ENVELOPE = (
    "（系统启动。上次为非正常退出（崩溃或被强制结束），"
    "数据已恢复到最后一致状态，会话中未完成的调用已补充回执。）"
)


def _with_now(text: str, tz_name: str) -> str:
    """括号信封开头插入"现在 X。"——出生/重生时的昼夜感。"""
    now = clock.now(tz_name).strftime("%Y-%m-%d %H:%M")
    if text.startswith("（"):
        return f"（现在 {now}。" + text[1:]
    return f"（现在 {now}。）{text}"


def _restart_envelope(unclean: bool, tz_name: str = "") -> str:
    """重启信封：区分正常停机与意外中断（强杀/崩溃/断电）。"""
    return _with_now(
        CRASH_ENVELOPE if unclean else RESUME_ENVELOPE, tz_name)


def _boot_envelope(plugin_names: list[str], tz_name: str = "") -> str:
    """首次启动信封：客观陈述装配状态（无主观语气）。"""
    if not plugin_names:
        return _with_now(BOOT_ENVELOPE, tz_name)
    names = "、".join(plugin_names)
    return _with_now(
        f"（系统首次启动。已装配插件：{names}。"
        "当前可用能力以工具列表与技能索引为准，记忆为空。）", tz_name)


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
    for t in build_memory_tools(store, cfg):
        kernel.register_tool(t)
    kernel.register_tool(_read_skill_tool(persona))
    kernel.register_tool(_deep_sleep_tool(kernel, store))

    # 唤醒附加事实（身体感受：待办提醒概览——内核不解其义，只附上）
    def _extras() -> str:
        pend = store.pending_reminders(5)
        if not pend:
            return ""
        items = "；".join(r["intent"][:30] for r in pend[:3])
        rel = clock.format_rel(pend[0]["due_at"], tz_name=cfg.timezone)
        return f"（你还有 {len(pend)} 条待办提醒，最近一条 {rel}，如：{items}。）"
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
                 "error": "letter 不能为空（deep_sleep 保存重建包后轮换会话）"},
                ensure_ascii=False)
        hours = max(0.1, min(float(args.get("hours") or 8), 72.0))
        store.save_packet(letter)
        pend = store.pending_reminders(5)
        todo = "；".join(r["intent"][:40] for r in pend) or "无"
        seed = ("（深睡结束，会话已轮换。上一会话留下的信：\n" + letter
                + f"\n\n待办提醒：{todo}）")
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
    # 图标语言：与 replay.py / web(src/lib/render.ts) 同一套通用 emoji
    glyph = {WAKE: "☀️", ASSISTANT_MSG: "💬", TOOL_CALL: "🔧",
             TOOL_RESULT: "📥", REST: "🌙", SESSION_ROTATED: "🔄"}

    def _print(ev) -> None:
        g = glyph.get(ev.type, "⚪")
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
                        text=f"（你自己定的提醒到点了：{r['intent']}。"
                             f"现在 {clock.hm(time.time(), cfg.timezone)}。）",
                        kind=REMINDER, origin="host"))
            except Exception:  # noqa: BLE001
                log.exception("提醒轮询异常")


class Host:
    """运行时服务（职责见模块 docstring）。run() 返回退出原因。"""

    def __init__(self, kernel: SelfKernel, store: Store, recorder: Recorder,
                 cfg: Config, stop: asyncio.Event, *,
                 plugins: list | None = None, no_stdin: bool = False,
                 first_boot: bool = False, unclean_prev: bool = False) -> None:
        self._kernel = kernel
        self._store = store
        self._recorder = recorder
        self._cfg = cfg
        self._stop = stop
        self._plugins = list(plugins or [])   # 组合根装载（与 server 共享）
        self._no_stdin = no_stdin
        self._first_boot = first_boot
        self._unclean_prev = unclean_prev

    async def run(self) -> str:
        """运行至停止。返回退出原因（signal/quit/stdin_eof）。"""
        kernel, cfg = self._kernel, self._cfg
        kernel.subscribe(_console_printer())
        plugins = self._plugins

        # 启动快照（含插件工具，复现它当时看到的世界）
        persona = Persona(cfg.soul_dir, cfg.skills_dir)
        self._recorder.event(
            "boot", version=__version__, config=cfg.masked(),
            persona_hash=hashlib.sha256(
                persona.system_prompt().encode("utf-8")).hexdigest()[:12],
            skills=sorted(persona.skills()), tools=kernel.tool_names())
        self._recorder.event("session_load",
                             msgs=len(kernel._session.messages),
                             summary_chars=len(kernel._session.summary))

        log.info("运行时启动（session=%s, 记忆=%s, 插件=%s）",
                 kernel.session_id, self._store.stats(), [p.name for p in plugins])

        tasks = [
            asyncio.create_task(_config_watcher(cfg, self._stop, self._recorder)),
            asyncio.create_task(_watchdog(cfg, kernel, self._stop)),
            asyncio.create_task(_maintenance(cfg, self._store, self._recorder,
                                             self._stop)),
            asyncio.create_task(_reminder_poller(cfg, self._store, kernel,
                                                 self._stop)),
        ]

        # 启动信封：首次启动 / 正常停机重启 / 非正常退出重启，三态客观陈述
        if self._first_boot:
            boot_text = _boot_envelope([p.name for p in plugins],
                                       self._cfg.timezone)
        else:
            boot_text = _restart_envelope(self._unclean_prev, self._cfg.timezone)
        kernel.notify(Envelope(text=boot_text, kind=BOOT, origin="host"))

        # 开发控制台：stdin 调试信封 + /命令；信号经 stop 事件传入。
        # 服务模式（no_stdin）：无 stdin 依赖，纯信号驱动（后台/容器/服务化）
        if self._no_stdin:
            log.info("服务模式：无控制台，纯信号驱动")
            await self._stop.wait()
            reason = "signal"
        else:
            console = Console(kernel, cfg, self._recorder)
            console.start()
            reason = await console.run(self._stop)

        # 优雅停机：中止思维 → 停后台任务 → 停插件（档案/关库由入口收尾）
        log.info("开始优雅停机（reason=%s）", reason)
        await kernel.abort()
        log.info("停机（host）：思维已中止")
        await kernel.wait_idle()
        self._stop.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for t, r in zip(tasks, results):
            if isinstance(r, BaseException):
                log.warning("停机（host）：基建任务 %s 异常退出：%r",
                            t.get_name(), r)
        log.info("停机（host）：基建任务已停止（%d 项）", len(tasks))
        await stop_plugins(plugins)
        log.info("停机（host）：插件已停止（%d 个）", len(plugins))
        return reason
