"""QQ 插件（OneBot v11 / NapCat）：第一个真实外界插件。

三件套：本目录（适配器+工具）+ SKILL.md（平台知识）+ data/plugins/qq/（数据）

- 感知面：正向 WS 连 NapCat；事件→信封；强刺激 notify 直达，
  弱刺激（普通群消息）进背景听觉（chat_log），心智自主查看
- 表达面：17 个工具（发消息/媒体/撤回/审批/群管/世界查询/状态/原始 API）
- 出站回执制：永不抛错，{ok, error, detail} 由心智自决
- 风控物理钳制：最小发送间隔 + 每分钟上限（账号生存约束，非行为治理）
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ...kernel.contracts import DIRECT, PLATFORM_EVENT, Envelope
from .. import Plugin, PluginContext
from .chatlog import ChatLog
from .config import QQConfig
from .tools import build_tools, _err, _ok
from .connection import OneBotClient
from .protocol import compose_envelope_text, parse_event

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
        self._conf = conf                # 声明式配置视图（活绑定，用时读）
        self._cfg = cfg                  # 全局活配置（timezone 等主体级事实）
        self._data_dir = data_dir
        self._client: OneBotClient | None = None
        self._chatlog: ChatLog | None = None
        self._kernel = None
        self._send_times: list[float] = []   # 风控滑动窗口
        self._pending_friend: dict[int, str] = {}  # user_id -> flag
        self._pending_group: dict[int, str] = {}   # user_id -> flag

    # ── 感知面 ────────────────────────────────────────
    async def start(self, ctx: PluginContext) -> None:
        self._kernel = ctx.kernel
        # 按日分库、不限量；保留期可配（0=永久，默认 0）
        self._chatlog = ChatLog(
            self._data_dir,
            retention_days=self._conf.chatlog_retention_days,
            tz_name=lambda: self._cfg.timezone)
        self._client = OneBotClient(
            ws_url=self._conf.ws_url,
            access_token=self._conf.access_token,
            on_event=self._on_event)
        self._client.start()
        # 身体感受：未读消息概览挂进每次唤醒（自己的出站不算未读）
        ctx.kernel.add_wake_extras(
            lambda: self._chatlog.unread_summary(self_id=self._conf.bot_uin)
            if self._chatlog else "")

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.stop()
        if self._chatlog is not None:
            self._chatlog.close()

    async def _on_event(self, data: dict) -> None:
        """OneBot 事件 → 分诊 → 信封 → notify / 背景听觉。"""
        self_id = self._conf.bot_uin
        owner = self._conf.owner_uin
        ev = parse_event(data, self_id=self_id, owner_uin=owner)
        if ev is None or ev.type == "ignore":
            return
        # 群白名单（空=全部）
        groups = self._conf.listen_groups or []
        if (groups and ev.chat_id.startswith("group_")
                and ev.chat_id.split("_", 1)[1] not in [str(g) for g in groups]):
            return
        # 待审请求登记（flag 供审批工具使用）
        if data.get("post_type") == "request":
            if data.get("request_type") == "friend":
                self._pending_friend[ev.sender_id] = data.get("flag", "")
            elif data.get("request_type") == "group":
                self._pending_group[ev.sender_id] = data.get("flag", "")
        text = compose_envelope_text(ev)
        if ev.type == "ambient":
            # 弱刺激：背景听觉（不直达；图片暂不收集，文本入档）
            self._chatlog.append(ev.chat_id, ev.sender_id, ev.sender_name,
                                 ev.text, ev.message_id)
            return
        # 强刺激/平台事件：直达（图片 URL 附在文本尾）
        if ev.image_urls:
            text += "\n（消息含图片：" + "、".join(ev.image_urls[:3]) + "）"
        kind = PLATFORM_EVENT if ev.type == "platform_event" else DIRECT
        self._chatlog.append(ev.chat_id, ev.sender_id, ev.sender_name,
                             ev.text, ev.message_id)
        self._kernel.notify(Envelope(text=text, kind=kind, source=ev.chat_id,
                                      origin="qq",
                                      meta={"sender_id": ev.sender_id}))

    # ── 表达面基建 ─────────────────────────────────────
    async def _call(self, action: str, params: dict | None = None) -> dict:
        return await self._client.call(action, params)

    def _log_sent(self, chat_id: str, text: str, message_id: int) -> None:
        """自己的出站消息也进背景听觉（带撤回句柄）。

        感知面应包含自己造成的世界变化；入档失败不影响发送本身。
        """
        if self._chatlog is None:
            return
        try:
            self._chatlog.append(chat_id, self._conf.bot_uin, "我",
                                 text, message_id)
        except Exception:  # noqa: BLE001
            log.exception("出站消息入档失败（不影响发送）")

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
        if self._send_times and now - self._send_times[-1] <                 self._conf.send_min_interval_s:
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
