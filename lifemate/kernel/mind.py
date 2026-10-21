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
from collections import deque
from typing import Any

from .compaction import Compactor
from .contracts import (ASSISTANT_MSG, ERROR, Envelope, EventCallback, IdleHook,
                        MindEvent, OrganPack, RUN_ENDED, RUN_STARTED,
                        SESSION_ROTATED, SEED, TOOL_CALL, TOOL_RESULT, Tool)
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
                 compactor: Compactor, max_turns: int = 24,
                 tool_timeout_s: float = 120.0) -> None:
        self._llm = llm
        self._persona = persona
        self._session = session
        self._compactor = compactor
        self._max_turns = max_turns
        self._tool_timeout_s = tool_timeout_s

        self._tools: dict[str, Tool] = {}
        self._organs: dict[str, OrganPack] = {}
        self._steer_q: deque[Envelope] = deque()
        self._follow_q: deque[Envelope] = deque()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._subs: list[EventCallback] = []
        self._idle_hooks: list[IdleHook] = []
        self._last_prompt_tokens = 0

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
        async with self._lock:
            self._follow_q.append(env)
            self._ensure_run()

    async def follow_up(self, env: Envelope) -> None:
        async with self._lock:
            self._follow_q.append(env)
            self._ensure_run()

    async def steer(self, env: Envelope) -> None:
        async with self._lock:
            if self._task is not None and not self._task.done():
                self._steer_q.append(env)
            else:
                self._follow_q.append(env)
                self._ensure_run()

    def _ensure_run(self) -> None:
        """启动运行任务（调用方持锁）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    # ── 运行循环 ────────────────────────────────────────
    async def _run(self) -> None:
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
        self._emit(RUN_STARTED, kind=env.kind, source=env.source)
        self._session.append({"role": "user", "content": env.text})

        for _turn in range(self._max_turns):
            await self._maybe_compact()
            resp = await self._llm.chat(
                self._build_messages(), tools=self._tool_schemas() or None)
            self._last_prompt_tokens = resp.prompt_tokens
            self._record_assistant(resp)
            if not resp.tool_calls:
                if self._drain_steer():
                    continue  # 插话送达，继续下一轮模型调用
                self._emit(RUN_ENDED)
                return
            for tc in resp.tool_calls:
                self._emit(TOOL_CALL, name=tc.name, arguments=tc.arguments[:500])
                result = await self._exec_tool(tc)
                self._session.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result})
                self._emit(TOOL_RESULT, name=tc.name, result=result[:500])
            self._drain_steer()  # 工具批次后、下次模型调用前送达插话

        # 轮次耗尽：通知后做一次无工具收束
        self._session.append({
            "role": "user",
            "content": "（已达单次运行的轮次上限，请不再调用工具，直接收束本轮）"})
        resp = await self._llm.chat(self._build_messages(), tools=None)
        self._record_assistant(resp)
        self._emit(RUN_ENDED, reason="max_turns")

    def _record_assistant(self, resp: LLMResponse) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments}}
                for c in resp.tool_calls]
        self._session.append(msg)
        self._emit(ASSISTANT_MSG, content=resp.content,
                   tool_calls=[{"name": c.name} for c in resp.tool_calls])

    def _drain_steer(self) -> bool:
        """把排队的插话作为用户消息送达。返回是否有送达。"""
        if not self._steer_q:
            return False
        while self._steer_q:
            env = self._steer_q.popleft()
            self._session.append({"role": "user", "content": env.text})
        return True

    def _build_messages(self) -> list[dict[str, Any]]:
        return ([{"role": "system", "content": self._persona.system_prompt()}]
                + self._session.build_request())

    async def _maybe_compact(self) -> None:
        try:
            if self._compactor.needs_compaction(self._session,
                                                self._last_prompt_tokens):
                await self._compactor.compact(self._session)
        except Exception:  # noqa: BLE001
            log.exception("压缩流程异常（跳过）")

    async def _exec_tool(self, tc: Any) -> str:
        """执行工具：任何异常转错误回执，绝不断链。"""
        try:
            args = json.loads(tc.arguments or "{}")
            if not isinstance(args, dict):
                args = {}
        except ValueError:
            args = {}
        tool = self._tools.get(tc.name)
        if tool is None:
            return json.dumps(
                {"ok": False,
                 "error": f"工具不存在: {tc.name}（对应器官可能已被摘除）"},
                ensure_ascii=False)
        try:
            result = await asyncio.wait_for(
                tool.func(args), timeout=self._tool_timeout_s)
            return _cap(result if isinstance(result, str)
                        else json.dumps(result, ensure_ascii=False))
        except TimeoutError:
            log.warning("工具超时: %s", tc.name)
            return json.dumps({"ok": False, "error": "工具执行超时"},
                              ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            log.warning("工具 %s 执行异常: %s", tc.name, e)
            return json.dumps(
                {"ok": False, "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False)

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
            old_id = self._session.session_id
            self._session = self._session.rotate()
            self._steer_q.clear()
            self._emit(SESSION_ROTATED, old=old_id,
                       new=self._session.session_id)
            self._follow_q.append(Envelope(text=seed, kind=SEED))
            self._ensure_run()

    async def abort(self) -> None:
        """中止当前运行（插话丢弃；排队的 follow_up 保留）。"""
        task = self._task
        self._steer_q.clear()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
