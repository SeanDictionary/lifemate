"""内核契约：一切接口的唯一定义（纯协议，零实现）。

本文件是全仓库唯一允许被所有层引用的模块（器官只认这里，不碰内核内部）。
借签对象：pi 的会话/steer 语义、dsh 的 agents 服务形状，按器官化契约重组。

- Envelope  信封：外界信息进入心智的唯一形态
- MindEvent 事件：心智活动外显的唯一形态（观测/呈现的唯一来源）
- Tool      工具：MCP tool schema 形状 + 进程内执行函数
- OrganPack 器官包：工具+skill 的挂载单元（适配器感知面由宿主驱动）
- MindKernel：认知自我的行为契约（思维循环 + 两接口 + 会话原语）
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Any, Protocol, runtime_checkable

# ── 信封来源类型（envelope.kind）─────────────────────────────
DIRECT = "direct"          # 直接对话（被@/私聊/回复）
AMBIENT = "ambient"        # 环境对话（经注意力门控升格后同样走强刺激路径）
PLATFORM_EVENT = "platform_event"  # 平台事件（好友申请/群成员变动等）
TICK = "tick"              # 内部时钟心跳
WAKE = "wake"              # 唤醒（生物钟/守护进程信号）
BOOT = "boot"              # 出生/启动
SEED = "seed"              # 会话轮换后的播种信封

STRONG_KINDS = frozenset({DIRECT, AMBIENT, PLATFORM_EVENT, TICK, WAKE, BOOT, SEED})


@dataclass
class Envelope:
    """自然语言信封：一切外界信息经此进入心智。

    text   带场景描述的文本（如「[群聊·某群] 张三 对你说：…」）
    kind   来源类型（见上）；强弱分级由注意力器官/适配器在上游完成
    source 会话寻址（如 "group_123"），可空——内核不理解其语义，仅透传
    meta   适配器附加信息（审计/观测用，内核不读内容）
    ts     到达时间戳（观测用：信封何时进入队列）
    delivery 入队方式（prompt/steer/follow_up，由内核入口方法标注）
    """

    text: str
    kind: str = DIRECT
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    delivery: str = ""


# ── 工具（MCP tool schema 形状）──────────────────────────────

ToolSchema = dict[str, Any]
# 执行函数：参数 dict → 文本回执（建议 JSON 字符串，含 ok 字段）
ToolFunc = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class Tool:
    """进程内工具：schema 用 MCP 形状，将来可薄包装成 MCP server 暴露。"""

    schema: ToolSchema  # {"name","description","inputSchema"}
    func: ToolFunc

    @property
    def name(self) -> str:
        return str(self.schema["name"])


@dataclass
class ToolCall:
    """LLM 发起的工具调用（内核内部表示，与具体 API 方言解耦）。"""

    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    """一次模型响应（内核内部表示，含观测元数据）。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""      # 归一化思考（不进上下文，仅供观测）
    stop_reason: str = ""   # stop/length/toolUse/…（raw 保真）
    model: str = ""         # 实际应答模型
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class OrganPack:
    """器官包：挂载单元（工具 + skill 名）。

    适配器的感知面（常驻连接/事件循环）不在此处——由宿主进程驱动，
    产出 Envelope 后经 MindKernel.prompt/steer 注入。
    """

    name: str
    tools: list[Tool] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # skill 名（进系统提示索引）


# ── 事件（观测/呈现的唯一来源）───────────────────────────────

RUN_STARTED = "run_started"        # 一次运行开始（处理一封信封）
ASSISTANT_MSG = "assistant_msg"    # 一条助手消息（文本与/或工具调用）
USER_MSG = "user_msg"              # 一封送达心智的信封（含投递语义）
TOOL_CALL = "tool_call"            # 一次工具调用开始
TOOL_RESULT = "tool_result"        # 一次工具回执（含执行语义）
RUN_ENDED = "run_ended"            # 运行结束
SESSION_ROTATED = "session_rotated"  # 会话轮换完成
COMPACTED = "compacted"            # 上下文压缩完成
QUEUE_DROP = "queue_drop"          # 排队信封被丢弃（rotate/abort）
ERROR = "error"

EventCallback = Callable[["MindEvent"], None]


@dataclass
class MindEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


# ── 内核行为契约 ────────────────────────────────────────────

IdleHook = Callable[[], Awaitable[None]]


@runtime_checkable
class MindKernel(Protocol):
    """认知自我的行为契约（实现见 kernel.mind.SelfKernel）。

    刺激入口三语义（借签 pi）：
    - prompt    空闲时唤醒（忙碌时自动降级为 follow_up）
    - steer     忙碌时插话：当前助手轮的工具批次执行完后、下一次模型调用前送达
    - follow_up 排队：本轮运行完全结束后作为新运行起点
    """

    async def prompt(self, env: Envelope) -> None: ...
    async def steer(self, env: Envelope) -> None: ...
    async def follow_up(self, env: Envelope) -> None: ...

    def register_organ(self, organ: OrganPack) -> None: ...
    def unregister_organ(self, name: str) -> None: ...

    async def rotate(self, seed: str) -> None: ...
    async def abort(self) -> None: ...

    def subscribe(self, cb: EventCallback) -> Callable[[], None]: ...
    async def wait_idle(self) -> None: ...

    def on_idle(self, hook: IdleHook) -> None:
        """注册空闲钩子：每次运行完全结束（无排队信封）时调用。

        生命周期器官用它在恰当的时机触发固化与轮换。
        """
