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
import uuid
from collections import deque
from typing import Any, Callable

from .compaction import Compactor
from .contracts import (ASSISTANT_MSG, COMPACTED, DIRECT, Envelope, ERROR,
                        EventCallback, MindEvent, PluginPack, QUEUE_DROP, REST,
                        SEED, SESSION_ROTATED, TOOL_CALL, TOOL_RESULT, Tool,
                        USER_MSG, WAKE)
from .llm import LLMClient, LLMResponse
from .persona import Persona
from .session import Session

log = logging.getLogger("kernel.mind")

# 工具回执进入上下文前的截断上限（保头尾，防上下文爆炸）
_TOOL_RESULT_MAX = 6000
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"

_NUDGE = "（本轮已连续思考多轮，未休息。）"


def _cap(text: str, n: int = _TOOL_RESULT_MAX) -> str:
    if len(text) <= n:
        return text
    cut = len(text) - n
    return text[: n // 2] + f"\n…（中间省略 {cut} 字符）…\n" + text[-n // 2:]


class _Waiting:
    """一次挂起的等待。reason: auto（收敛）/explicit（闹钟）/sleep（深睡）。"""

    __slots__ = ("future", "reason", "since")

    def __init__(self, future: asyncio.Future, reason: str) -> None:
        self.future = future
        self.reason = reason
        self.since = time.monotonic()


class SelfKernel:
    def __init__(self, *, llm: LLMClient, persona: Persona, session: Session,
                 compactor: Compactor, cfg) -> None:
        self._llm = llm
        self._persona = persona
        self._session = session
        self._compactor = compactor
        self._cfg = cfg  # 活配置：用时读取（热重载生效）

        self._tools: dict[str, Tool] = {}
        self._plugins: dict[str, PluginPack] = {}
        self._steer_q: deque[Envelope] = deque()      # 思考中的插话队列
        self._pending: list[Envelope] = []            # 等待期间累积的事件（组批）
        self._task: asyncio.Task | None = None
        self._subs: list[EventCallback] = []
        self._last_prompt_tokens = 0
        self._rid = ""                                # 当前唤醒周期 id（观测归组）
        self._wait: _Waiting | None = None
        self._wake_extras: list[Callable[[], str]] = []
        self._current_tool: asyncio.Task | None = None
        self._current_tool_interruptible = False
        self._interrupted: Envelope | None = None
        self._last_activity = time.time()             # 墙钟：唤醒差值
        self._last_activity_mono = time.monotonic()   # 单调钟：卡死检测

        self._wait_tool = self._make_wait_tool()
        self.register_tool(self._wait_tool)            # 等待是思维的一部分
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
    def register_tool(self, tool: Tool) -> None:
        """注册工具（重名覆盖；拔器官后同名工具自然消失）。"""
        self._tools[tool.name] = tool

    def register_plugin(self, plugin: PluginPack) -> None:
        for t in plugin.tools:
            self.register_tool(t)
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
            if not w.future.done():
                w.future.set_result(None)
            return
        self._steer_q.append(env)
        self._trim_steer()
        if ((env.kind == DIRECT or env.meta.get("urgent"))
                and self._current_tool is not None
                and self._current_tool_interruptible):
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
            self._emit(QUEUE_DROP, reason="overflow_steer",
                       texts=[e.text for e in dropped])

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
                wake_env = self._steer_q.popleft()   # 出生/重启信封
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
            self._emit(ERROR, error="live_crashed")

    async def _think(self) -> str:
        """一个唤醒周期：思考直到休息。返回下一次唤醒的组批文本。"""
        turns = 0
        nudged = False
        while True:
            await self._maybe_compact()
            resp = await self._llm.chat(
                self._build_messages(), tools=self._tool_schemas() or None)
            self._last_prompt_tokens = resp.prompt_tokens
            self._touch()
            self._record_assistant(resp)
            turns += 1

            if not resp.tool_calls:
                if self._drain_steer():
                    continue  # 插话送达，继续下一轮模型调用
                # 收敛 → 自动休息（说完话挂起，等世界敲门）
                self._emit(REST, rid=self._rid, reason="converged")
                return await self._do_wait(None, "auto")

            for tc in resp.tool_calls:
                self._emit(TOOL_CALL, rid=self._rid, name=tc.name,
                           arguments=tc.arguments[:500])
                result, ok, latency_ms = await self._exec_tool(tc)
                self._session.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result})
                self._emit(TOOL_RESULT, rid=self._rid, tool_call_id=tc.id,
                           name=tc.name, ok=ok, latency_ms=latency_ms,
                           result=result[:500])
            self._drain_steer()  # 工具批次后、下次模型调用前送达插话

            # 防不睡：连续轮次超阈值先提示、再强制（睡眠剥夺→昏睡）
            if turns >= self._cfg.max_turns:
                log.warning("连续 %d 轮未休息，强制休息", turns)
                self._emit(REST, rid=self._rid, reason="forced")
                return await self._do_wait(None, "auto")
            if turns >= self._cfg.turns_rest_nudge and not nudged:
                nudged = True
                self._deliver(Envelope(text=_NUDGE, kind="system",
                                       delivery="nudge"))

    # ── 等待与唤醒 ──────────────────────────────────────
    async def _do_wait(self, timeout_s: float | None, reason: str) -> str:
        """挂起等待。返回唤醒信封（组批事件 + 差值 + 附加事实）。"""
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._wait = _Waiting(fut, reason)
        try:
            if timeout_s is None:
                await fut  # 运行级取消（abort）穿透
            else:
                try:
                    await asyncio.wait_for(fut, timeout=timeout_s)
                except TimeoutError:
                    pass  # 闹钟到点，自然醒
        finally:
            self._wait = None
        return self._compose_wake()

    def _compose_wake(self) -> str:
        """组装唤醒信封：时间差值 + 期间事件组批 + 附加事实。"""
        events, self._pending = self._pending, []
        now = time.strftime("%H:%M")
        mins = max(0.0, (time.time() - self._last_activity) / 60)
        head = f"（你从等待中醒来。现在 {now}，距你上次活动约 {mins:.0f} 分钟。"
        if events:
            lines = [head + "期间收到："]
            for i, env in enumerate(events):
                tag = "·".join(x for x in (env.origin, env.kind, env.source) if x)
                mark = _CIRCLED[i] if i < len(_CIRCLED) else f"({i + 1})"
                text = env.text if len(env.text) <= 800 else env.text[:800] + "…"
                lines.append(f"{mark} [{tag}] {text}" if tag else f"{mark} {text}")
            body = "\n".join(lines) + "）"
        else:
            body = head + "没有新事件——是你设的闹钟或等待自然到期。）"
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
        self._emit(WAKE, rid=self._rid, trigger=trigger,
                   kind=env.kind, source=env.source)
        self._deliver(env)

    def _deliver(self, env: Envelope) -> None:
        """信封送达心智（进上下文）并发观测事件。"""
        self._session.append({"role": "user", "content": env.text})
        self._emit(USER_MSG, rid=self._rid, text=env.text, env={
            "origin": env.origin, "kind": env.kind, "source": env.source,
            "delivery": env.delivery or "wake",
            "arrived_ts": round(env.ts, 3)})
        self._touch()

    def _drain_steer(self) -> bool:
        """把排队的插话作为用户消息送达。返回是否有送达。"""
        if not self._steer_q:
            return False
        while self._steer_q:
            self._deliver(self._steer_q.popleft())
        return True

    def _touch(self) -> None:
        self._last_activity = time.time()
        self._last_activity_mono = time.monotonic()

    def _new_rid(self) -> None:
        self._rid = uuid.uuid4().hex[:6]

    # ── 消息与模型 ──────────────────────────────────────
    def _record_assistant(self, resp: LLMResponse) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments}}
                for c in resp.tool_calls]
        self._session.append(msg)
        self._emit(ASSISTANT_MSG, rid=self._rid, content=resp.content,
                   thinking=resp.thinking,
                   tool_calls=[{"name": c.name, "arguments": c.arguments[:500]}
                               for c in resp.tool_calls],
                   model=resp.model, stop_reason=resp.stop_reason,
                   prompt_tokens=resp.prompt_tokens,
                   completion_tokens=resp.completion_tokens,
                   latency_ms=resp.latency_ms)

    def _build_messages(self) -> list[dict[str, Any]]:
        return ([{"role": "system", "content": self._persona.system_prompt()}]
                + self._session.build_request())

    async def _maybe_compact(self) -> None:
        try:
            if self._compactor.needs_compaction(self._session,
                                                self._last_prompt_tokens):
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
            return (json.dumps(
                {"ok": False,
                 "error": f"工具不存在: {tc.name}（对应器官可能已被摘除）"},
                ensure_ascii=False), False, 0)

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
            result = await asyncio.wait_for(
                task, timeout=self._cfg.tool_timeout_s)
            text = result if isinstance(result, str) else json.dumps(
                result, ensure_ascii=False)
            return _cap(text), True, int((time.monotonic() - t0) * 1000)
        except TimeoutError:
            log.warning("工具超时: %s", tc.name)
            return (json.dumps({"ok": False, "error": "工具执行超时"},
                               ensure_ascii=False),
                    False, int((time.monotonic() - t0) * 1000))
        except asyncio.CancelledError:
            env = self._interrupted
            if env is not None:
                # 强刺激反射性打断：回执而非断链
                self._interrupted = None
                await asyncio.gather(task, return_exceptions=True)
                secs = int(time.monotonic() - t0)
                tag = "·".join(x for x in (env.kind, env.source) if x)
                return (json.dumps(
                    {"ok": False, "interrupted": True,
                     "note": f"被 [{tag}] 打断（执行了 {secs} 秒，未完成）"},
                    ensure_ascii=False), False, secs * 1000)
            raise  # 运行级取消（abort）：穿透
        except Exception as e:  # noqa: BLE001
            log.warning("工具 %s 执行异常: %s", tc.name, e)
            return (json.dumps(
                {"ok": False, "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False), False, int((time.monotonic() - t0) * 1000))
        finally:
            self._current_tool = None
            self._current_tool_interruptible = False

    # ── wait 工具（内置：等待是思维的一部分） ───────────
    def _make_wait_tool(self) -> Tool:
        async def _fn(args: dict) -> str:
            timeout = args.get("timeout_s")
            reason = str(args.get("reason") or "")
            t: float | None = None
            if timeout is not None:
                t = max(float(timeout), 1.0)  # 最短 1 秒
            self._emit(REST, rid=self._rid,
                       reason=f"explicit{':' + reason if reason else ''}",
                       timeout_s=t)
            wake = await self._do_wait(t, "explicit")
            # 唤醒作为工具结果返回：同回合无缝延续（自己定闹钟自己醒）
            self._new_rid()
            self._emit(WAKE, rid=self._rid, trigger="explicit_wait")
            return wake
        return Tool(schema={
            "name": "wait",
            "description": "等待：挂起思考直到有新输入/到期提醒/超时。"
                           "给自己定闹钟用 timeout_s（秒）并可在 reason 里写上"
                           "要做什么；不传 timeout_s 则一直等到有事件。等待零成本。",
            "inputSchema": {"type": "object", "properties": {
                "timeout_s": {"type": "number",
                              "description": "多少秒后醒来（闹钟）"},
                "reason": {"type": "string", "description": "在等什么/到点做什么"},
            }},
        }, func=_fn, interruptible=False)

    # ── 会话原语 ────────────────────────────────────────
    def swap_session(self, seed: str) -> None:
        """睡眠中转身：换新会话并播种（运行不中断；deep_sleep 调用）。"""
        old_id = self._session.session_id
        self._session = self._session.rotate(
            keep=getattr(self._cfg, "session_keep_rotated", 2))
        self._emit(SESSION_ROTATED, old=old_id,
                   new=self._session.session_id, seed_chars=len(seed))
        self._new_rid()
        self._emit(WAKE, rid=self._rid, trigger="rebirth")
        self._deliver(Envelope(text=seed, kind=SEED, delivery="seed"))

    async def rest(self, timeout_s: float | None) -> str:
        """公开的休息原语（deep_sleep 等宿主工具使用）。"""
        self._emit(REST, rid=self._rid, reason="sleep", timeout_s=timeout_s)
        wake = await self._do_wait(timeout_s, "sleep")
        self._new_rid()
        self._emit(WAKE, rid=self._rid, trigger="sleep")
        return wake

    async def abort(self) -> None:
        """中止（停机用）：取消永续循环，插话丢弃并存证。"""
        if self._steer_q:
            self._emit(QUEUE_DROP, reason="abort",
                       texts=[e.text for e in self._steer_q])
            self._steer_q.clear()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._emit(REST, rid=self._rid, reason="aborted")

    # ── 自愈（watchdog 入口） ───────────────────────────
    async def heal(self) -> dict[str, Any]:
        """健康检查与自愈：循环复活 + 思考卡死处置。"""
        info: dict[str, Any] = {
            "busy": self.is_busy, "resting": self._wait is not None,
            "steer": len(self._steer_q)}
        task = self._task
        if task is None or task.done():
            log.warning("永续循环已死，重新拉起")
            self._ensure_run()
            info["healed"] = "revived"
            return info
        # 思考卡死：非等待状态长时间无活动（休息不算卡死）
        if (self._wait is None
                and time.monotonic() - self._last_activity_mono
                > self._cfg.max_run_s):
            elapsed = time.monotonic() - self._last_activity_mono
            log.error("思考卡死（%.0fs 无活动），中止自愈", elapsed)
            self._emit(ERROR, error="think_stuck",
                       detail={"elapsed_s": round(elapsed, 1), "rid": self._rid})
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
        answered = {m.get("tool_call_id") for m in msgs
                    if m.get("role") == "tool"}
        repaired = 0
        for tc in last["tool_calls"]:
            if tc.get("id") not in answered:
                self._session.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(
                        {"ok": False,
                         "note": "进程重启，该调用未返回结果。"},
                        ensure_ascii=False)})
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
        return (self._task is not None and not self._task.done()
                and self._wait is None)

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

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
