"""思维循环（SelfKernel）：永续回合模型。

一次运行贯穿一生：思考 → 休息(wait) → 唤醒 → 思考 → ……
不是事件开启思考，而是思考在等待事件。

- 收敛自动休息（auto-wait）：说完话挂起，等世界敲门，零成本
- 显式 wait 工具：自己定闹钟——唤醒作为工具返回值，同回合无缝延续
- notify 唯一入口：等待中→解决等待（组批唤醒）；思考中→插话
- 强刺激反射性打断可打断工具（停手是生理，反应是意志）
- swap_session：睡眠中转身（deep_sleep 用），运行不中断
- 防不睡：连续轮次超阈值先提示后强制休息（睡眠剥夺→昏睡）
- 崩溃恢复：悬空工具调用补合成回执，全链兜底延伸到生死边界
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from ..utils import clock
from .compaction import Compactor
from .contracts import AMBIENT, ASSISTANT_MSG, COMPACTED, DIRECT, ERROR, QUEUE_DROP, REST, SEED, SESSION_ROTATED, TOOL_CALL, TOOL_RESULT, USER_MSG, WAKE, Envelope, EventCallback, MindEvent, PluginPack, Tool
from .llm import LLMClient, LLMResponse, is_retryable
from .persona import Persona
from .session import Session
from .tools import make_wait_tool

log = logging.getLogger("kernel.mind")

# 工具回执进入上下文前的截断上限（保头尾，防上下文爆炸）
_TOOL_RESULT_MAX = 6000
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
_WAKE_AMBIENT_KEEP = 12  # 唤醒组批里 ambient 保留条数（超出计数提示，信息在 chat_log）

_NUDGE = "（本轮已连续思考多轮，未休息。）"


def _cap(text: str, n: int = _TOOL_RESULT_MAX) -> str:
    if len(text) <= n:
        return text
    cut = len(text) - n
    return text[: n // 2] + f"\n…（中间省略 {cut} 字符）…\n" + text[-n // 2 :]


class _Waiting:
    """一次挂起的等待。

    reason: auto（收敛）/explicit（闹钟）/sleep（深睡）；
    intent: 闹钟意图（wait 工具的 reason 文本，空=无未来承诺）；
    dnd: 自主免打扰——ambient 信封只累积不唤醒（直达/闹钟照常穿透）。
    """

    __slots__ = ("future", "reason", "since", "intent", "timeout_s", "dnd")

    def __init__(self, future: asyncio.Future, reason: str, intent: str = "", timeout_s: float | None = None, dnd: bool = False) -> None:
        self.future = future
        self.reason = reason
        self.since = time.monotonic()
        self.intent = intent
        self.timeout_s = timeout_s
        self.dnd = dnd


class SelfKernel:
    def __init__(self, *, llm: LLMClient, persona: Persona, session: Session, compactor: Compactor, cfg) -> None:
        self._llm = llm
        self._persona = persona
        self._session = session
        self._compactor = compactor
        self._cfg = cfg  # 活配置：用时读取（热重载生效）

        self._tools: dict[str, Tool] = {}
        self._tool_src: dict[str, str] = {}  # 工具名 → 登记方（观测展示用）
        self._plugins: dict[str, PluginPack] = {}
        self._steer_q: deque[Envelope] = deque()  # 思考中的插话队列
        self._pending: list[Envelope] = []  # 等待期间累积的事件（组批）
        self._task: asyncio.Task | None = None
        self._subs: list[EventCallback] = []
        self._last_prompt_tokens = 0
        self._rid = ""  # 当前唤醒周期 id（观测归组）
        self._wait: _Waiting | None = None
        self._llm_fail_streak = 0  # LLM 瞬态连续失败计数（兑底退避用，成功即清零）
        self._refuse_streak = 0  # 结果性失败连续计数（被拒/永久错误，成功即清零）
        self._last_wake_events = 0  # 最近一次组批唤醒携带的事件数（退避续跑判断用）
        self._wake_extras: list[Callable[[], str]] = []
        self._current_tool: asyncio.Task | None = None
        self._current_tool_interruptible = False
        self._interrupted: Envelope | None = None
        self._last_activity = time.time()  # 墙钟：唤醒差值
        self._last_activity_mono = time.monotonic()  # 单调钟：卡死检测

        self._wait_tool = make_wait_tool(self)
        self.register_tool(self._wait_tool)  # 等待是思维的一部分
        self._repair_dangling()

    # ── 事件 ─────────────────────────────────────────────
    def subscribe(self, cb: EventCallback) -> Callable[[], None]:
        self._subs.append(cb)

        def _unsub() -> None:
            if cb in self._subs:
                self._subs.remove(cb)

        return _unsub

    def _emit(self, type_: str, **data: Any) -> None:
        ev = MindEvent(type=type_, data=data)
        for cb in list(self._subs):
            try:
                cb(ev)
            except Exception:  # noqa: BLE001
                log.exception("事件订阅者异常（type=%s）", type_)

    def add_wake_extras(self, fn: Callable[[], str]) -> None:
        """宿主/插件注入唤醒附加事实（身体感受）——内核不解其义，只附上。

        可多次注册（host 的待办概览、插件的未读消息等），组批唤醒时全部拼接。
        """
        self._wake_extras.append(fn)

    # ── 工具/器官挂载 ───────────────────────────────────
    def register_tool(self, tool: Tool, source: str = "内核") -> None:
        """注册工具（重名覆盖；拔器官后同名工具自然消失）。

        source：登记方标识（内核/记忆/宿主/插件名）——仅观测展示用，
        内核不解其义。"""
        self._tools[tool.name] = tool
        self._tool_src[tool.name] = source

    def register_plugin(self, plugin: PluginPack) -> None:
        for t in plugin.tools:
            self.register_tool(t, source=plugin.name)
        self._plugins[plugin.name] = plugin
        log.info("插件注册: %s（工具 %d 个）", plugin.name, len(plugin.tools))

    def unregister_plugin(self, name: str) -> None:
        plugin = self._plugins.pop(name, None)
        if not plugin:
            return
        for t in plugin.tools:
            if self._tools.get(t.name) is t:
                del self._tools[t.name]
        log.info("插件摘除: %s", name)

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return [t.schema for t in self._tools.values()]

    # ── 输入唯一入口 ────────────────────────────────────
    def notify(self, env: Envelope) -> None:
        """世界敲门：等待中→组批唤醒；思考中→插话。

        强刺激（DIRECT/urgent）反射性打断当前可打断工具——
        停手是生理，怎么反应是意志。
        """
        env.delivery = "notify"
        w = self._wait
        if w is not None:
            self._pending.append(env)
            # 自主免打扰（dnd）：ambient（群聊攒批等）只累积不唤醒——
            # 醒不醒是心智自己的决定；直达/@/私聊/自定闹钟/平台事件照常穿透
            if w.dnd and env.kind == AMBIENT:
                return
            if not w.future.done():
                w.future.set_result(None)
            return
        self._steer_q.append(env)
        self._trim_steer()
        if (env.kind == DIRECT or env.meta.get("urgent")) and self._current_tool is not None and self._current_tool_interruptible:
            self._interrupted = env
            self._current_tool.cancel()
        self._ensure_run()

    def _trim_steer(self) -> None:
        """背压：插话队列超限时丢最旧并存证（洪峰下保留最新上下文）。"""
        dropped = []
        while len(self._steer_q) > self._cfg.queue_max_steer:
            dropped.append(self._steer_q.popleft())
        if dropped:
            log.warning("插话队列溢出：丢弃最旧 %d 封", len(dropped))
            self._emit(QUEUE_DROP, reason="overflow_steer", texts=[e.text for e in dropped])

    def _ensure_run(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._live())

    # ── 永续循环 ────────────────────────────────────────
    async def _live(self) -> None:
        """一生：唤醒周期串成的永续回合。"""
        try:
            wake_env: Envelope | None = None
            first = True
            if self._steer_q:
                wake_env = self._steer_q.popleft()  # 出生/重启信封
            elif self._has_dangling_input():
                # 崩溃恢复：上次思考中断（如进程异常/残余 bug），
                # 输入还在会话里没处理完——续上，不静默丢弃
                wake_env = Envelope(text="（思考中断后恢复：上一条输入尚未处理完，请继续处理。）", kind=DIRECT, origin="host")
            while True:
                if wake_env is None:
                    # 无待处理输入：休息等敲门
                    self._emit(REST, rid=self._rid, reason="auto")
                    text = await self._do_wait(None, "auto")
                    wake_env = Envelope(text=text, kind=WAKE)
                self._begin_cycle(wake_env, trigger="boot" if first else "event")
                first = False
                wake_text = await self._think()
                wake_env = Envelope(text=wake_text, kind=WAKE)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("永续循环异常（watchdog 将拉起）")
            # 完整堆栈入档（黑匣子可独立排查，不必翻日志对时间戳）
            self._emit(ERROR, error="live_crashed", detail={"traceback": traceback.format_exc()[-4000:], "rid": self._rid})

    async def _backoff_wait(self, delay: float, reason: str) -> None:
        """瞬态故障退避：挂起休息后原地续跑。

        退避期间到达的真实事件照常送达（与故障无关，不能丢）；
        纯到点醒来（无事件）不投递任何内容——过程信息不进上下文。
        """
        self._emit(REST, rid=self._rid, reason=reason, timeout_s=delay)
        wake = await self._do_wait(delay, reason)
        if self._last_wake_events:
            self._deliver(Envelope(text=wake, kind=WAKE, origin="host"))

    async def _refuse(self, summary: str) -> None:
        """结果性失败回执（进上下文）：永久错误/内容拦截——处置权在心智。

        连续超过阈值后不再重复回执（心智已知情），改退避重试——
        避免回执循环烧 token；任何一次正常输出即清零计数。
        """
        self._refuse_streak += 1
        log.error("LLM 结果性失败（连续第 %d 次）：%s", self._refuse_streak, summary)
        self._emit(ERROR, error="llm_refused", detail={"streak": self._refuse_streak, "error": summary[:200]})
        if self._refuse_streak > self._cfg.llm_refuse_receipts:
            delay = min(self._cfg.llm_retry_backoff_s * (2 ** min(self._refuse_streak, 6)), 300.0)
            await self._backoff_wait(delay, "llm_refuse_retry")
            return
        self._deliver(Envelope(text=(f"（系统回执：{summary[:300]}。这不是网络波动——不会自动消失，原样重试大概率同样的结果。这是需要你知道的结果，怎么处置由你决定：调整后重试、先搁置，或主动联系主人说明情况、请主人帮忙排查。）"), kind="system", origin="host", delivery="receipt"))

    def _has_dangling_input(self) -> bool:
        """会话尾部有未回应的输入（崩溃/中断遗留，正常休息时尾部是 assistant）。"""
        msgs = self._session.messages
        return bool(msgs) and msgs[-1].get("role") in ("user", "tool")

    async def _think(self) -> str:
        """一个唤醒周期：思考直到休息。返回下一次唤醒的组批文本。"""
        turns = 0
        nudged = False
        while True:
            await self._maybe_compact()
            try:
                resp = await self._llm.chat(self._build_messages(), tools=self._tool_schemas() or None)
            except Exception as e:  # noqa: BLE001
                if is_retryable(e):
                    # 瞬态故障（网络波动/限流/5xx）：静默重试——过程信息
                    # 不进上下文，心智无感（llm.py 内部已重试一轮，这里
                    # 接住残余，退避后原地续，输入保留在会话）
                    self._llm_fail_streak += 1
                    delay = min(self._cfg.llm_retry_backoff_s * (2 ** min(self._llm_fail_streak, 6)), 300.0)
                    log.error("LLM 瞬态故障（连续第 %d 次，%.0fs 后自动重试本轮）：%s", self._llm_fail_streak, delay, e)
                    self._emit(ERROR, error="llm_failed", detail={"streak": self._llm_fail_streak, "retry_in_s": round(delay, 1), "error": str(e)[:200]})
                    await self._backoff_wait(delay, "llm_retry")
                    continue
                # 永久错误（鉴权/参数/内容被拒）：这是结果不是过程——
                # 回执进上下文，处置权在心智（换方式重试/搁置/找主人）
                await self._refuse(f"模型服务返回确定性错误，本次思考未能完成：{e}")
                continue
            self._llm_fail_streak = 0
            self._last_prompt_tokens = resp.prompt_tokens
            self._touch()
            self._record_assistant(resp)
            turns += 1

            if resp.stop_reason == "content_filter":
                # 输出被内容过滤拦截（敏感内容等）：同样是结果——回执
                # 给心智自决（换说法/搁置/求助），不属于自动重试的过程
                await self._refuse("上一次输出被内容过滤拦截，未产生内容")
                continue
            self._refuse_streak = 0

            if not resp.tool_calls:
                if self._drain_steer():
                    continue  # 插话送达，继续下一轮模型调用
                # 收敛 → 自动休息（说完话挂起，等世界敲门）
                self._emit(REST, rid=self._rid, reason="converged")
                return await self._do_wait(None, "auto")

            for tc in resp.tool_calls:
                self._emit(TOOL_CALL, rid=self._rid, name=tc.name, arguments=tc.arguments[:500])
                result, ok, latency_ms = await self._exec_tool(tc)
                self._session.append({"role": "tool", "tool_call_id": tc.id, "content": result, "ts": round(time.time(), 3)})
                self._emit(TOOL_RESULT, rid=self._rid, tool_call_id=tc.id, name=tc.name, ok=ok, latency_ms=latency_ms, result=result[:500])
            self._drain_steer()  # 工具批次后、下次模型调用前送达插话

            # 防不睡：连续轮次超阈值先提示、再强制（睡眠剥夺→昏睡）
            if turns >= self._cfg.max_turns:
                log.warning("连续 %d 轮未休息，强制休息", turns)
                self._emit(REST, rid=self._rid, reason="forced")
                return await self._do_wait(None, "auto")
            if turns >= self._cfg.turns_rest_nudge and not nudged:
                nudged = True
                self._deliver(Envelope(text=_NUDGE, kind="system", delivery="nudge"))

    # ── 等待与唤醒 ──────────────────────────────────────
    async def _do_wait(self, timeout_s: float | None, reason: str, intent: str = "", dnd: bool = False) -> str:
        """挂起等待。返回唤醒信封（组批事件 + 差值 + 附加事实）。"""
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._wait = _Waiting(fut, reason, intent, timeout_s, dnd)
        expired = False
        try:
            if timeout_s is None:
                await fut  # 运行级取消（abort）穿透
            else:
                try:
                    await asyncio.wait_for(fut, timeout=timeout_s)
                except TimeoutError:
                    expired = True  # 闹钟到点，自然醒
        finally:
            w = self._wait
            self._wait = None
        # 闹钟意图两头都还给她：被事件取代时告知（要继续等重新定）；
        # 自然到期时更要还——"我等这个是为了什么"正是到点最该想起的事
        alarm = None
        if w is not None and w.intent:
            if expired:
                alarm = f"（到点了——你定这个等待时说：「{w.intent}」。接下来做什么由你决定。）"
            elif timeout_s is not None:
                alarm = f"（你之前定的闹钟「{w.intent}」已被这次唤醒取代——还要等的话重新定。）"
        wake = self._compose_wake(alarm)
        # 等待结束即活动：关闭 watchdog 在"长睡醒来→首次模型响应"之间的
        # 误报窗口（否则长于 max_run_s 的等待一到期，恰逢模型调用慢/重试
        # 就会被误判 think_stuck 而中止自愈）
        self._touch()
        return wake

    def _compose_wake(self, alarm_note: str | None = None) -> str:
        """组装唤醒信封：时间差值 + 期间事件组批 + 附加事实。"""
        events, self._pending = self._pending, []
        self._last_wake_events = len(events)
        # DND 累积截断：ambient 只保留最近 N 条，更早的计数提示
        # （信息已入背景听觉，read_chat_log 可看——不丢，只去噪）
        ambient_idx = [i for i, e in enumerate(events) if e.kind == AMBIENT]
        dropped = 0
        if len(ambient_idx) > _WAKE_AMBIENT_KEEP:
            drop = set(ambient_idx[:-_WAKE_AMBIENT_KEEP])
            dropped = len(drop)
            events = [e for i, e in enumerate(events) if i not in drop]
        now = clock.hm(time.time(), self._cfg.timezone)
        mins = max(0.0, (time.time() - self._last_activity) / 60)
        head = f"（你从等待中醒来。现在 {now}，距你上次活动约 {mins:.0f} 分钟。"
        if events:
            lines = [head + "期间收到："]
            for i, env in enumerate(events):
                tag = "·".join(x for x in (env.origin, env.kind, env.source) if x)
                mark = _CIRCLED[i] if i < len(_CIRCLED) else f"({i + 1})"
                text = env.text if len(env.text) <= 800 else env.text[:800] + "…"
                lines.append(f"{mark} [{tag}] {text}" if tag else f"{mark} {text}")
            if dropped:
                lines.append(f"（另有 {dropped} 条群消息未列出——已入背景听觉，read_chat_log 可看。）")
            if alarm_note:
                lines.append(alarm_note)
            body = "\n".join(lines) + "）"
        else:
            body = head + "没有新事件——是你设的闹钟或等待自然到期。）"
            if alarm_note:
                body += "\n" + alarm_note
        parts = [body]
        for fn in self._wake_extras:
            try:
                extra = fn()
                if extra:
                    parts.append(extra)
            except Exception:  # noqa: BLE001
                log.exception("唤醒附加事实生成异常（忽略该条）")
        return "\n".join(parts)

    def _begin_cycle(self, env: Envelope, trigger: str = "event") -> None:
        self._new_rid()
        self._emit(WAKE, rid=self._rid, trigger=trigger, kind=env.kind, source=env.source)
        self._deliver(env)

    def _deliver(self, env: Envelope) -> None:
        """信封送达心智（进上下文）并发观测事件。"""
        self._session.append({"role": "user", "content": env.text, "ts": round(env.ts, 3)})
        self._emit(USER_MSG, rid=self._rid, text=env.text, env={"origin": env.origin, "kind": env.kind, "source": env.source, "delivery": env.delivery or "wake", "arrived_ts": round(env.ts, 3)})
        self._touch()

    def _drain_steer(self) -> bool:
        """把排队的插话作为用户消息送达。返回是否有送达。"""
        if not self._steer_q:
            return False
        while self._steer_q:
            env = self._steer_q.popleft()
            # 插话带时间标注（到达时刻）：长回合中途的输入不再被
            # 误读为本轮早期语境（唤醒信封自带"现在 X"，不在此列）
            env.text = f"[{clock.hm(env.ts, self._cfg.timezone)}] {env.text}"
            self._deliver(env)
        return True

    def _touch(self) -> None:
        self._last_activity = time.time()
        self._last_activity_mono = time.monotonic()

    def _new_rid(self) -> None:
        self._rid = uuid.uuid4().hex[:6]

    # ── 消息与模型 ──────────────────────────────────────
    def _record_assistant(self, resp: LLMResponse) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": resp.content, "ts": round(time.time(), 3)}
        if resp.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in resp.tool_calls]
        self._session.append(msg)
        self._emit(ASSISTANT_MSG, rid=self._rid, content=resp.content, thinking=resp.thinking, tool_calls=[{"name": c.name, "arguments": c.arguments[:500]} for c in resp.tool_calls], model=resp.model, stop_reason=resp.stop_reason, prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens, latency_ms=resp.latency_ms)

    def _build_messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self._persona.system_prompt()}] + self._session.build_request()

    async def _maybe_compact(self) -> None:
        try:
            if self._compactor.needs_compaction(self._session, self._last_prompt_tokens):
                info = await self._compactor.compact(self._session)
                if info:
                    self._emit(COMPACTED, rid=self._rid, **info)
        except Exception:  # noqa: BLE001
            log.exception("压缩流程异常（跳过）")

    # ── 工具执行 ────────────────────────────────────────
    async def _exec_tool(self, tc: Any) -> tuple[str, bool, int]:
        """执行工具：任何异常转错误回执，绝不断链。

        返回 (回执文本, ok, 耗时 ms)。wait 工具特例：无超时、由事件解决。
        """
        t0 = time.monotonic()
        self._interrupted = None
        try:
            args = json.loads(tc.arguments or "{}")
            if not isinstance(args, dict):
                args = {}
        except ValueError:
            args = {}
        tool = self._tools.get(tc.name)
        if tool is None:
            return (json.dumps({"ok": False, "error": f"工具不存在: {tc.name}（对应器官可能已被摘除）"}, ensure_ascii=False), False, 0)

        if tool is self._wait_tool:
            try:
                result = await tool.func(args)
                return (result, True, int((time.monotonic() - t0) * 1000))
            except asyncio.CancelledError:
                raise  # 运行级取消穿透

        task = asyncio.create_task(tool.func(args))
        self._current_tool = task
        self._current_tool_interruptible = tool.interruptible
        try:
            result = await asyncio.wait_for(task, timeout=self._cfg.tool_timeout_s)
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            return _cap(text), True, int((time.monotonic() - t0) * 1000)
        except TimeoutError:
            log.warning("工具超时: %s", tc.name)
            return (json.dumps({"ok": False, "error": "工具执行超时"}, ensure_ascii=False), False, int((time.monotonic() - t0) * 1000))
        except asyncio.CancelledError:
            env = self._interrupted
            if env is not None:
                # 强刺激反射性打断：回执而非断链
                self._interrupted = None
                await asyncio.gather(task, return_exceptions=True)
                secs = int(time.monotonic() - t0)
                tag = "·".join(x for x in (env.kind, env.source) if x)
                return (json.dumps({"ok": False, "interrupted": True, "note": f"被 [{tag}] 打断（执行了 {secs} 秒，未完成）"}, ensure_ascii=False), False, secs * 1000)
            raise  # 运行级取消（abort）：穿透
        except Exception as e:  # noqa: BLE001
            log.warning("工具 %s 执行异常: %s", tc.name, e)
            return (json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), False, int((time.monotonic() - t0) * 1000))
        finally:
            self._current_tool = None
            self._current_tool_interruptible = False

    # ── 会话原语 ────────────────────────────────────────
    def swap_session(self, seed: str) -> None:
        """睡眠中转身：换新会话并播种（运行不中断；deep_sleep 调用）。"""
        old_id = self._session.session_id
        self._session = self._session.rotate(keep=getattr(self._cfg, "session_keep_rotated", 2))
        self._emit(SESSION_ROTATED, old=old_id, new=self._session.session_id, seed_chars=len(seed))
        self._new_rid()
        self._emit(WAKE, rid=self._rid, trigger="rebirth")
        now = clock.now(self._cfg.timezone).strftime("%Y-%m-%d %H:%M")
        self._deliver(Envelope(text=f"（轮换完成。现在是 {now}。）\n\n{seed}", kind=SEED, delivery="seed"))

    async def rest(self, timeout_s: float | None, dnd: bool = False) -> str:
        """公开的休息原语（deep_sleep 等宿主工具使用）。dnd=免打扰。"""
        self._emit(REST, rid=self._rid, reason="sleep", timeout_s=timeout_s, dnd=dnd)
        wake = await self._do_wait(timeout_s, "sleep", dnd=dnd)
        self._new_rid()
        self._emit(WAKE, rid=self._rid, trigger="sleep")
        return wake

    async def abort(self) -> None:
        """中止（停机用）：取消永续循环，插话丢弃并存证。"""
        if self._steer_q:
            self._emit(QUEUE_DROP, reason="abort", texts=[e.text for e in self._steer_q])
            self._steer_q.clear()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._emit(REST, rid=self._rid, reason="aborted")

    # ── 自愈（watchdog 入口） ───────────────────────────
    async def heal(self) -> dict[str, Any]:
        """健康检查与自愈：循环复活 + 思考卡死处置。"""
        info: dict[str, Any] = {"busy": self.is_busy, "resting": self._wait is not None, "steer": len(self._steer_q)}
        task = self._task
        if task is None or task.done():
            log.warning("永续循环已死，重新拉起")
            self._ensure_run()
            info["healed"] = "revived"
            return info
        # 思考卡死：非等待状态长时间无活动（休息不算卡死）
        if self._wait is None and time.monotonic() - self._last_activity_mono > self._cfg.max_run_s:
            elapsed = time.monotonic() - self._last_activity_mono
            log.error("思考卡死（%.0fs 无活动），中止自愈", elapsed)
            self._emit(ERROR, error="think_stuck", detail={"elapsed_s": round(elapsed, 1), "rid": self._rid})
            await self.abort()
            self._ensure_run()
            info["healed"] = "think_stuck"
        return info

    # ── 崩溃恢复 ────────────────────────────────────────
    def _repair_dangling(self) -> None:
        """会话末尾悬空的工具调用补合成回执（重启续上唯一的 run）。"""
        msgs = self._session.messages
        if not msgs:
            return
        last = msgs[-1]
        if last.get("role") != "assistant" or not last.get("tool_calls"):
            return
        answered = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
        repaired = 0
        for tc in last["tool_calls"]:
            if tc.get("id") not in answered:
                self._session.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"ok": False, "note": "进程重启，该调用未返回结果。"}, ensure_ascii=False)})
                repaired += 1
        if repaired:
            log.warning("修复悬空工具调用 %d 个（崩溃恢复）", repaired)

    # ── 观测辅助 ────────────────────────────────────────
    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def is_busy(self) -> bool:
        """思考中（任务活着且不在等待）。"""
        return self._task is not None and not self._task.done() and self._wait is None

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def tools_detail(self) -> list[dict[str, Any]]:
        """全部工具的详细信息（/api/tools 观测面板用）。"""
        out = []
        for name in sorted(self._tools):
            t = self._tools[name]
            out.append(
                {
                    "name": name,
                    "description": t.schema.get("description", ""),
                    "inputSchema": t.schema.get("inputSchema", {}),
                    "interruptible": bool(t.interruptible),
                    "source": self._tool_src.get(name, "内核"),
                }
            )
        return out

    async def wait_idle(self) -> None:
        """等到静止：循环结束，或真正挂在未决的等待上。"""
        while True:
            task = self._task
            if task is None or task.done():
                return
            w = self._wait
            if w is not None and not w.future.done():
                return
            await asyncio.sleep(0.02)
