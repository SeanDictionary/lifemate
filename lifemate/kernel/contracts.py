"""内核契约：一切接口的唯一定义（纯协议，零实现）。

本文件是全仓库唯一允许被所有层引用的模块（器官只认这里，不碰内核内部）。
借签对象：pi 的会话/steer 语义、dsh 的 agents 服务形状，按器官化契约重组。

- Envelope  信封：外界信息进入心智的唯一形态
- MindEvent 事件：心智活动外显的唯一形态（观测/呈现的唯一来源）
- Tool      工具：MCP tool schema 形状 + 进程内执行函数
- PluginPack 插件包：工具+skill 的挂载单元（适配器感知面由插件自持 start/stop）
- MindKernel：认知自我的行为契约（思维循环 + 两接口 + 会话原语）
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ── 信封来源类型（envelope.kind）─────────────────────────────
DIRECT = "direct"  # 直接对话（被@/私聊/回复）
AMBIENT = "ambient"  # 环境对话（经注意力门控升格后同样走强刺激路径）
PLATFORM_EVENT = "platform_event"  # 平台事件（好友申请/群成员变动等）
REMINDER = "reminder"  # 到期提醒（它自己定的闹钟被兑现）
TICK = "tick"  # 内部时钟（保留）
WAKE = "wake"  # 唤醒信封（等待解决后的组批消息）
BOOT = "boot"  # 出生/启动
SEED = "seed"  # 会话轮换后的播种信封

STRONG_KINDS = frozenset({DIRECT, AMBIENT, PLATFORM_EVENT, REMINDER, TICK, WAKE, BOOT, SEED})


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
    origin: str = ""  # 投递者标识（插件名/console/host）——信封溯源


# ── 工具（MCP tool schema 形状）──────────────────────────────

ToolSchema = dict[str, Any]
# 执行函数：参数 dict → 文本回执（建议 JSON 字符串，含 ok 字段）。
# 出站类回执须复述动作事实（对象/内容/句柄）：回执是心智审视
# 刚发生动作的镜子——只报 ok 不报事实，心智无从反悔（见 builtin/action-review）
ToolFunc = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class Tool:
    """进程内工具：schema 用 MCP 形状，将来可薄包装成 MCP server 暴露。

    interruptible：强刺激（直接对话）可反射性打断执行中的调用
    （停手是生理，反应是意志）；长工具（watch/exec）声明 True。
    """

    schema: ToolSchema  # {"name","description","inputSchema"}
    func: ToolFunc
    interruptible: bool = False

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
    thinking: str = ""  # 归一化思考（不进上下文，仅供观测）
    stop_reason: str = ""  # stop/length/toolUse/…（raw 保真）
    model: str = ""  # 实际应答模型
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class PluginPack:
    """插件包：挂载单元（工具 + skill 名）。

    适配器的感知面（常驻连接/事件循环）不在此处——由插件的 start/stop
    自持，产出 Envelope 后经 MindKernel.notify 注入。
    """

    name: str
    tools: list[Tool] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # skill 名（进系统提示索引）


# ── 事件（观测/呈现的唯一来源）───────────────────────────────

WAKE = "wake"  # 唤醒周期开始（wait 解决/出生/重生）
ASSISTANT_MSG = "assistant_msg"  # 一条助手消息（文本与/或工具调用）
USER_MSG = "user_msg"  # 一封送达心智的信封（含投递语义）
TOOL_CALL = "tool_call"  # 一次工具调用开始
TOOL_RESULT = "tool_result"  # 一次工具回执（含执行语义）
REST = "rest"  # 进入休息（收敛/显式/强制/中止）
SESSION_ROTATED = "session_rotated"  # 会话轮换完成
COMPACTED = "compacted"  # 上下文压缩完成
QUEUE_DROP = "queue_drop"  # 排队信封被丢弃（rotate/abort）
ERROR = "error"

EventCallback = Callable[["MindEvent"], None]


@dataclass
class MindEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


# ── 内核行为契约 ────────────────────────────────────────────


@runtime_checkable
class MindKernel(Protocol):
    """认知自我的行为契约（实现见 kernel.mind.SelfKernel）。

    永续回合模型：一次运行贯穿一生——思考 → 休息(wait) → 唤醒 → 思考 → ……
    不是事件开启思考，而是思考在等待事件。

    - notify     世界敲门（唯一入口）：等待中→解决等待并组批唤醒；
                 思考中→插话（工具批次后、下次模型调用前送达）；
                 强刺激（DIRECT）可反射性打断可打断工具
    - rest       挂起等待（deep_sleep 等宿主工具使用）
    - swap_session  睡眠中转身：换新会话并播种，运行不中断
    - add_wake_extras  宿主/插件注入唤醒附加事实（身体感受等，内核不解其义）
    """

    def notify(self, env: Envelope) -> None: ...
    def add_wake_extras(self, fn: Callable[[], str]) -> None: ...

    def register_plugin(self, plugin: PluginPack) -> None: ...
    def unregister_plugin(self, name: str) -> None: ...

    def swap_session(self, seed: str) -> None: ...
    async def rest(self, timeout_s: float | None) -> str: ...
    async def abort(self) -> None: ...

    def subscribe(self, cb: EventCallback) -> Callable[[], None]: ...
    async def wait_idle(self) -> None: ...
    async def heal(self) -> dict: ...
