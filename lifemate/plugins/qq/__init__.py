"""QQ 插件（OneBot v11 / NapCat）：第一个真实外界插件。

三件套：本目录（适配器+工具）+ SKILL.md（平台知识）+ data/plugins/qq/（数据）

- 感知面：正向 WS 连 NapCat；事件→信封；强刺激 notify 直达，
  弱刺激（普通群消息）进背景听觉（chat_log），心智自主查看
- 表达面：17 个工具（发消息/媒体/撤回/审批/群管/世界查询/状态/原始 API）
- 出站回执制：永不抛错，{ok, error, detail} 由心智自决
- 风控物理钳制：最小发送间隔 + 每分钟上限（账号生存约束，非行为治理）
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ...kernel.contracts import AMBIENT, DIRECT, PLATFORM_EVENT, Envelope
from .. import Plugin, PluginContext
from .chatlog import ChatLog, format_ts
from .config import QQConfig
from .connection import OneBotClient
from .protocol import compose_envelope_text, parse_event
from .tools import _err, _ok, build_tools

log = logging.getLogger("plugins.qq")

TOOLS_SPEC = """工具集：
send_message / send_image / recall_message / get_message_detail /
read_chat_log / approve_friend_request / handle_group_request /
get_friends / get_groups / get_group_members / get_group_info /
poke / send_like / set_group_card / leave_group /
mute_member / kick_member / set_qq_status / set_profile / qq_api"""


class QQPlugin(Plugin):
    def __init__(self, conf: QQConfig, cfg, data_dir: Path) -> None:
        super().__init__(name="qq", skills=["qq"])
        self._conf = conf  # 声明式配置视图（活绑定，用时读）
        self._cfg = cfg  # 全局活配置（timezone 等主体级事实）
        self._data_dir = data_dir
        self._client: OneBotClient | None = None
        self._chatlog: ChatLog | None = None
        self._kernel = None
        self._send_times: list[float] = []  # 风控滑动窗口
        self._pending_friend: dict[int, str] = {}  # user_id -> flag
        self._pending_group: dict[int, str] = {}  # user_id -> flag
        self._last_direct_chat = ""  # 跨会话切换检测（上下文供给判定）
        self._last_outbound: dict[str, float] = {}  # 会话 → 我最后发言时刻（对话延续窗口起算）
        self._last_ambient_wake: dict[str, float] = {}  # 会话 → 最后攒批唤醒时刻（冷却/兑底用）

    # ── 感知面 ────────────────────────────────────────
    async def start(self, ctx: PluginContext) -> None:
        self._kernel = ctx.kernel
        # 按日分库、不限量；保留期可配（0=永久，默认 0）
        self._chatlog = ChatLog(self._data_dir, retention_days=self._conf.chatlog_retention_days, tz_name=lambda: self._cfg.timezone)
        self._client = OneBotClient(ws_url=self._conf.ws_url, access_token=self._conf.access_token, on_event=self._on_event)
        self._client.start()
        # 身体感受：未读消息概览挂进每次唤醒（自己的出站不算未读）
        ctx.kernel.add_wake_extras(lambda: self._chatlog.unread_summary(self_id=self._conf.bot_uin) if self._chatlog else "")

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.stop()
        if self._chatlog is not None:
            self._chatlog.close()

    async def _on_event(self, data: dict) -> None:
        """OneBot 事件 → 分诊 → 信封 → notify / 背景听觉。"""
        self_id = self._conf.bot_uin
        owner = self._conf.owner_uin
        ev = parse_event(data, self_id=self_id, owner_uin=owner,
                         reply_lookup=(self._chatlog.message_of
                                       if self._chatlog else None),
                         aliases=[str(a) for a in (self._conf.wake_aliases or [])])
        if ev is None or ev.type == "ignore":
            return
        # 群白名单（空=全部）
        groups = self._conf.listen_groups or []
        if groups and ev.chat_id.startswith("group_") and ev.chat_id.split("_", 1)[1] not in [str(g) for g in groups]:
            return
        # 待审请求登记（flag 供审批工具使用）
        if data.get("post_type") == "request":
            if data.get("request_type") == "friend":
                self._pending_friend[ev.sender_id] = data.get("flag", "")
            elif data.get("request_type") == "group":
                self._pending_group[ev.sender_id] = data.get("flag", "")
        text = compose_envelope_text(ev)
        # 对话延续窗口：我发言后 N 秒内的群消息升格为强刺激（仅路由，
        # 文本保持背景形态——对方不一定在对我说话，"对你说"是过度断言）
        in_window = (ev.type == "ambient"
                     and self._conf.conversation_window_s > 0
                     and time.time() - self._last_outbound.get(ev.chat_id, 0.0) < self._conf.conversation_window_s)
        if ev.type == "ambient" and not in_window:
            # 弱刺激：背景听觉（不直达；图片暂不收集，文本入档）
            self._chatlog.append(ev.chat_id, ev.sender_id, ev.sender_name, ev.text, ev.message_id)
            self._maybe_ambient_wake(ev.chat_id)
            return
        # 强刺激/平台事件：直达（图片 URL 附在文本尾）
        if ev.image_urls:
            text += "\n（消息含图片：" + "、".join(ev.image_urls[:3]) + "）"
        kind = PLATFORM_EVENT if ev.type == "platform_event" else DIRECT
        self._chatlog.append(ev.chat_id, ev.sender_id, ev.sender_name, ev.text, ev.message_id)
        if kind == DIRECT and ev.chat_id != self._last_direct_chat:
            # 跨会话切换：附该会话最近几条——心智醒来即有正确上下文，
            # 不需主动 read_chat_log（也就不会惯性读错群）；同会话连续
            # 直达不重复附加（保持信封精瘦）
            text += self._recent_context(ev.chat_id, ev.message_id)
        if kind == DIRECT:
            self._last_direct_chat = ev.chat_id
        self._kernel.notify(Envelope(text=text, kind=kind, source=ev.chat_id, origin="qq", meta={"sender_id": ev.sender_id}))

    # ── 表达面基建 ─────────────────────────────────────
    def _recent_context(self, chat_id: str, cur_mid: int, line_limit: int = 60) -> str:
        """该会话最近几条（跨会话切换时的信封附加上下文）。"""
        n = int(self._conf.envelope_context_n)
        if n <= 0 or self._chatlog is None:
            return ""
        rows = self._chatlog.recent(chat_id, n + 1)
        if rows and (cur_mid == 0 or rows[-1]["message_id"] == cur_mid):
            rows = rows[:-1]  # 去掉刚入档的当前消息
        rows = rows[-n:]
        if not rows:
            return ""
        lines = [f"{r['sender_name']}: {r['text'][:line_limit]}" for r in rows]
        return "\n（该会话最近：\n" + "\n".join(lines) + "\n）"

    async def _call(self, action: str, params: dict | None = None) -> dict:
        return await self._client.call(action, params)

    def _log_sent(self, chat_id: str, text: str, message_id: int) -> None:
        """自己的出站消息也进背景听觉（带撤回句柄）。

        感知面应包含自己造成的世界变化；入档失败不影响发送本身。
        """
        self._last_outbound[chat_id] = time.time()  # 对话延续窗口起算点
        if self._chatlog is None:
            return
        try:
            self._chatlog.append(chat_id, self._conf.bot_uin, "我", text, message_id)
        except Exception:  # noqa: BLE001
            log.exception("出站消息入档失败（不影响发送）")

    def _maybe_ambient_wake(self, chat_id: str) -> None:
        """弱刺激攒批唤醒：阈值/兑底双触发，冷却钳制。

        事件驱动而非定时轮询——有聊天才可能醒，冷群零成本。
        触发即把最近消息递到眼前（已读线同步），接不接话由心智自判；
        AMBIENT kind 不在打断条件里，永远不会打断正在进行的发送。
        """
        if self._chatlog is None or self._kernel is None:
            return
        threshold = self._conf.ambient_wake_threshold
        if threshold <= 0:
            return  # 关闭：纯背景听觉
        now = time.time()
        if now - self._last_ambient_wake.get(chat_id, 0.0) < self._conf.ambient_wake_cooldown_s:
            return  # 冷却中
        unread, earliest = self._chatlog.unread_state(chat_id, self_id=self._conf.bot_uin)
        # 兜底看"最早未读已等多久"（而非距上次唤醒）——首次唤醒也须经受等待
        if not (unread >= threshold
                or (unread >= 2 and earliest and now - earliest >= self._conf.ambient_wake_gap_s)):
            return
        rows = self._chatlog.recent(chat_id, 12)
        self._chatlog.mark_read(chat_id)
        self._last_ambient_wake[chat_id] = now
        bot = self._conf.bot_uin
        lines = []
        for r in rows:
            stamp = format_ts(r["ts"], tz_name=self._cfg.timezone)
            if bot and r["sender_id"] == bot:
                lines.append(f"[{stamp}] 我: {r['text']}（message_id={r['message_id']}）")
            else:
                lines.append(f"[{stamp}] {r['sender_name']}: {r['text']}")
        where = chat_id.replace("group_", "群 ").replace("private_", "私聊 ")
        self._kernel.notify(Envelope(text=f"（{where}你不在时聊了 {unread} 条：\n" + "\n".join(lines) + "）", kind=AMBIENT, source=chat_id, origin="qq"))

    def _receipt(self, coro_factory, **tags) -> Any:
        """出站统一回执：永不抛错（铁律 7）。"""

        async def _run() -> str:
            try:
                data = await coro_factory()
                return _ok(**{**tags, **({"data": data} if data else {})})
            except ConnectionError as e:
                return _err(f"unreachable: {e}", **tags)
            except TimeoutError:
                return _err("api 超时", **tags)
            except Exception as e:  # noqa: BLE001
                return _err(f"api_error: {e}", **tags)

        return _run()

    def _throttle(self) -> str | None:
        """风控物理钳制（账号生存约束）：最小间隔 + 每分钟上限。"""
        now = time.monotonic()
        self._send_times = [t for t in self._send_times if now - t < 60]
        if self._send_times and now - self._send_times[-1] < self._conf.send_min_interval_s:
            return "发送过快（最小间隔限制），稍等再试"
        if len(self._send_times) >= self._conf.send_max_per_min:
            return "已达每分钟发送上限，稍等再试"
        self._send_times.append(now)
        return None

    def _target_params(self, target_type: str, target_id: int) -> tuple[str, dict]:
        if target_type == "group":
            return "send_group_msg", {"group_id": int(target_id)}
        return "send_private_msg", {"user_id": int(target_id)}


CONFIG = QQConfig  # 装载器约定：首启预设写盘（enabled=false）


def create(ctx: PluginContext) -> Plugin | None:
    conf = QQConfig(ctx.cfg)
    if not conf.enabled:
        return None
    plugin = QQPlugin(conf, ctx.cfg, ctx.data_dir)
    plugin.tools = build_tools(plugin)
    return plugin
