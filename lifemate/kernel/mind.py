"""思维循环（SelfKernel）：MindKernel 契约的自建实现。

信封队列三语义（借签 pi 的 prompt/steer/followUp）：
- prompt    空闲时唤醒（忙碌时自动降级为 follow_up）
- steer     忙碌时插话：当前助手轮的工具批次执行完后、下一次模型调用前送达
- follow_up 排队：本轮运行完全结束后作为新运行起点

一次运行 = 处理一封信封直至收敛（无更多工具调用且无插话）。
每个 tool_call 必有 tool 回执（全链兜底，任何异常转为错误回执不断链）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import Any

from .compaction import Compactor
from .contracts import (ASSISTANT_MSG, COMPACTED, ERROR, Envelope, EventCallback,
                        IdleHook, MindEvent, OrganPack, QUEUE_DROP, RUN_ENDED,
                        RUN_STARTED, SESSION_ROTATED, SEED, TOOL_CALL,
                        TOOL_RESULT, Tool, USER_MSG)
from .llm import LLMClient, LLMResponse
from .persona import Persona
from .session import Session

log = logging.getLogger("kernel.mind")

# 工具回执进入上下文前的截断上限（保头尾，防上下文爆炸）
_TOOL_RESULT_MAX = 6000


def _cap(text: str, n: int = _TOOL_RESULT_MAX) -> str:
    if len(text) <= n:
        return text
    cut = len(text) - n
    return text[: n // 2] + f"\n…（中间省略 {cut} 字符）…\n" + text[-n // 2:]


class SelfKernel:
    def __init__(self, *, llm: LLMClient, persona: Persona, session: Session,
                 compactor: Compactor, cfg) -> None:
        self._llm = llm
        self._persona = persona
        self._session = session
        self._compactor = compactor
        self._cfg = cfg  # 活配置：max_turns/tool_timeout_s 用时读取（热重载生效）

        self._tools: dict[str, Tool] = {}
        self._organs: dict[str, OrganPack] = {}
        self._steer_q: deque[Envelope] = deque()
        self._follow_q: deque[Envelope] = deque()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._subs: list[EventCallback] = []
        self._idle_hooks: list[IdleHook] = []
        self._last_prompt_tokens = 0
        self._rid = ""  # 当前运行 id（观测归组用）
        self._run_started_at = 0.0  # watchdog 卡死检测用

    # ── 事件 ─────────────────────────────────────────────
    def subscribe(self, cb: EventCallback) -> Any:
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

    def on_idle(self, hook: IdleHook) -> None:
        """空闲钩子：运行完全结束且无排队信封时触发（生命周期器官用）。"""
        self._idle_hooks.append(hook)

    # ── 工具/器官挂载 ───────────────────────────────────
    def register_tool(self, tool: Tool) -> None:
        """注册工具（重名覆盖；拔器官后同名工具自然消失）。"""
        self._tools[tool.name] = tool

    def register_organ(self, organ: OrganPack) -> None:
        for t in organ.tools:
            self.register_tool(t)
        self._organs[organ.name] = organ
        log.info("器官装载: %s（工具 %d 个）", organ.name, len(organ.tools))

    def unregister_organ(self, name: str) -> None:
        organ = self._organs.pop(name, None)
        if not organ:
            return
        for t in organ.tools:
            if self._tools.get(t.name) is t:
                del self._tools[t.name]
        log.info("器官摘除: %s", name)

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return [t.schema for t in self._tools.values()]

    # ── 刺激入口 ────────────────────────────────────────
    async def prompt(self, env: Envelope) -> None:
        env.delivery = "prompt"
        async with self._lock:
            self._follow_q.append(env)
            self._trim_queues()
            self._ensure_run()

    async def follow_up(self, env: Envelope) -> None:
        env.delivery = "follow_up"
        async with self._lock:
            self._follow_q.append(env)
            self._trim_queues()
            self._ensure_run()

    async def steer(self, env: Envelope) -> None:
        env.delivery = "steer"
        async with self._lock:
            if self._task is not None and not self._task.done():
                self._steer_q.append(env)
                self._trim_queues()
            else:
                self._follow_q.append(env)
                self._trim_queues()
                self._ensure_run()

    def _trim_queues(self) -> None:
        """背压：队列超限时丢最旧并存证（洪峰下保留最新上下文）。"""
        for q, cap, label in (
                (self._follow_q, self._cfg.queue_max_follow, "follow"),
                (self._steer_q, self._cfg.queue_max_steer, "steer")):
            dropped = []
            while len(q) > cap:
                dropped.append(q.popleft())
            if dropped:
                log.warning("队列溢出（%s）：丢弃最旧 %d 封", label, len(dropped))
                self._emit(QUEUE_DROP, reason=f"overflow_{label}",
                           texts=[e.text for e in dropped])

    def _ensure_run(self) -> None:
        """启动运行任务（调用方持锁）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    # ── 运行循环 ────────────────────────────────────────
    async def _run(self) -> None:
        self._run_started_at = time.monotonic()
        try:
            while self._follow_q:
                env = self._follow_q.popleft()
                try:
                    await self._process(env)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("处理信封异常（kind=%s）", env.kind)
                    self._emit(ERROR, error="process_failed", kind=env.kind)
        finally:
            respawn = False
            hooks: list[IdleHook] = []
            async with self._lock:
                if self._follow_q:
                    respawn = True  # 收尾期间又有新信封
                else:
                    hooks = list(self._idle_hooks)
            if respawn:
                self._task = asyncio.create_task(self._run())
            else:
                for hook in hooks:
                    try:
                        await hook()
                    except Exception:  # noqa: BLE001
                        log.exception("空闲钩子异常")

    async def _process(self, env: Envelope) -> None:
        """处理一封信封直至收敛。"""
        self._rid = uuid.uuid4().hex[:6]
        self._emit(RUN_STARTED, rid=self._rid, kind=env.kind, source=env.source)
        self._deliver(env)

        for _turn in range(self._cfg.max_turns):
            await self._maybe_compact()
            resp = await self._llm.chat(
                self._build_messages(), tools=self._tool_schemas() or None)
            self._last_prompt_tokens = resp.prompt_tokens
            self._record_assistant(resp)
            if not resp.tool_calls:
                if self._drain_steer():
                    continue  # 插话送达，继续下一轮模型调用
                self._emit(RUN_ENDED, rid=self._rid, reason="stop")
                return
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

        # 轮次耗尽：通知后做一次无工具收束
        self._session.append({
            "role": "user",
            "content": "（已达单次运行的轮次上限，请不再调用工具，直接收束本轮）"})
        resp = await self._llm.chat(self._build_messages(), tools=None)
        self._record_assistant(resp)
        self._emit(RUN_ENDED, rid=self._rid, reason="max_turns")

    def _deliver(self, env: Envelope) -> None:
        """信封送达心智（进上下文）并发观测事件。"""
        self._session.append({"role": "user", "content": env.text})
        self._emit(USER_MSG, rid=self._rid, text=env.text, env={
            "kind": env.kind, "source": env.source,
            "delivery": env.delivery, "arrived_ts": round(env.ts, 3)})

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

    def _drain_steer(self) -> bool:
        """把排队的插话作为用户消息送达。返回是否有送达。"""
        if not self._steer_q:
            return False
        while self._steer_q:
            self._deliver(self._steer_q.popleft())
        return True

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

    async def _exec_tool(self, tc: Any) -> tuple[str, bool, int]:
        """执行工具：任何异常转错误回执，绝不断链。

        返回 (回执文本, ok, 耗时 ms)。"""
        import time as _time
        t0 = _time.monotonic()
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
        try:
            result = await asyncio.wait_for(
                tool.func(args), timeout=self._cfg.tool_timeout_s)
            text = result if isinstance(result, str) else json.dumps(
                result, ensure_ascii=False)
            return _cap(text), True, int((_time.monotonic() - t0) * 1000)
        except TimeoutError:
            log.warning("工具超时: %s", tc.name)
            return (json.dumps({"ok": False, "error": "工具执行超时"},
                               ensure_ascii=False),
                    False, int((_time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            log.warning("工具 %s 执行异常: %s", tc.name, e)
            return (json.dumps(
                {"ok": False, "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False),
                    False, int((_time.monotonic() - t0) * 1000))

    # ── 会话原语 ────────────────────────────────────────
    async def rotate(self, seed: str) -> None:
        """会话轮换 + 播种：固化的产物（重建包）由生命周期器官在轮换前生成。"""
        task = self._task
        async with self._lock:
            if task is not None and not task.done():
                task.cancel()
            else:
                task = None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            if self._steer_q:
                self._emit(QUEUE_DROP, reason="rotate",
                           texts=[e.text for e in self._steer_q])
                self._steer_q.clear()
            old_id = self._session.session_id
            self._session = self._session.rotate(
                keep=getattr(self._cfg, "session_keep_rotated", 2))
            self._emit(SESSION_ROTATED, old=old_id,
                       new=self._session.session_id, seed_chars=len(seed))
            self._follow_q.append(Envelope(text=seed, kind=SEED))
            self._ensure_run()

    async def abort(self) -> None:
        """中止当前运行（插话丢弃并记录；排队的 follow_up 保留）。"""
        if self._steer_q:
            self._emit(QUEUE_DROP, reason="abort",
                       texts=[e.text for e in self._steer_q])
            self._steer_q.clear()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._emit(RUN_ENDED, rid=self._rid, reason="aborted")

    # ── 自愈（watchdog 入口） ─────────────────────
    async def heal(self) -> dict[str, Any]:
        """健康检查与自愈：僵尸队列复活 + 卡死运行处置。

        由宿主 watchdog 周期调用；返回当前状态供观测。
        """
        info: dict[str, Any] = {
            "busy": self.is_busy,
            "follow": len(self._follow_q),
            "steer": len(self._steer_q),
        }
        # 卡死运行：超时中止（abort 保留 follow_q，中止后会自动重生）
        if self.is_busy and self._run_started_at:
            elapsed = time.monotonic() - self._run_started_at
            if elapsed > self._cfg.max_run_s:
                log.error("运行超时（%.0fs > %.0fs），中止并自愈",
                          elapsed, self._cfg.max_run_s)
                self._emit(ERROR, error="run_stuck", detail={
                    "elapsed_s": round(elapsed, 1), "rid": self._rid})
                await self.abort()
                info["healed"] = "run_stuck"
        # 僵尸队列：task 已死但队列非空（respawn 失败等罕见情形）
        async with self._lock:
            if (self._task is None or self._task.done()) and self._follow_q:
                log.warning("检测到僵尸队列（%d 封待处理），重新拉起运行",
                            len(self._follow_q))
                self._ensure_run()
                info["healed"] = "zombie_queue"
        return info

    async def wait_idle(self) -> None:
        while True:
            task = self._task
            if task is None or task.done():
                async with self._lock:
                    if not self._follow_q and (
                            self._task is None or self._task.done()):
                        return
                continue
            await asyncio.gather(task, return_exceptions=True)

    # ── 观测辅助 ────────────────────────────────────────
    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def is_busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def tool_names(self) -> list[str]:
        return sorted(self._tools)
