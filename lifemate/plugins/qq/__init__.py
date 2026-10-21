"""QQ 插件（OneBot v11 / NapCat）：第一个真实外界插件。

三件套：本目录（适配器+工具）+ SKILL.md（平台知识）+ data/plugins/qq/（数据）

- 感知面：正向 WS 连 NapCat；事件→信封；强刺激 notify 直达，
  弱刺激（普通群消息）进背景听觉（chat_log），心智自主查看
- 表达面：17 个工具（发消息/媒体/撤回/审批/群管/世界查询/状态/原始 API）
- 出站回执制：永不抛错，{ok, error, detail} 由心智自决
- 风控物理钳制：最小发送间隔 + 每分钟上限（账号生存约束，非行为治理）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from ...kernel.contracts import AMBIENT, DIRECT, PLATFORM_EVENT, Envelope, Tool
from .. import Plugin, PluginContext
from .chatlog import ChatLog
from .connection import OneBotClient
from .protocol import compose_envelope_text, parse_event

log = logging.getLogger("plugins.qq")

TOOLS_SPEC = """工具集：
send_message / send_image / recall_message / get_message_detail /
read_chat_log / approve_friend_request / handle_group_request /
get_friends / get_groups / get_group_members / get_group_info /
poke / send_like / set_group_card / leave_group /
mute_member / kick_member / set_qq_status / set_profile / qq_api"""


def _ok(**kw) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def _err(msg: str, **kw) -> str:
    return json.dumps({"ok": False, "error": msg, **kw}, ensure_ascii=False)


class QQPlugin(Plugin):
    def __init__(self, cfg, data_dir: Path) -> None:
        super().__init__(name="qq", skills=["qq"])
        self._cfg = cfg                  # 活配置（热重载即时生效项见 _q）
        self._data_dir = data_dir
        self._client: OneBotClient | None = None
        self._chatlog: ChatLog | None = None
        self._kernel = None
        self._send_times: list[float] = []   # 风控滑动窗口
        self._pending_friend: dict[int, str] = {}  # user_id -> flag
        self._pending_group: dict[int, str] = {}   # user_id -> flag

    # ── 配置读取（活引用，部分项热生效） ───────────────
    def _q(self, key: str, default=None):
        return self._cfg.plugins.get("qq", {}).get(key, default)

    # ── 感知面 ────────────────────────────────────────
    async def start(self, ctx: PluginContext) -> None:
        self._kernel = ctx.kernel
        self._chatlog = ChatLog(self._data_dir / "chatlog.db",
                                int(self._q("chatlog_max", 500)))
        self._client = OneBotClient(
            ws_url=self._q("ws_url", "ws://127.0.0.1:3001"),
            access_token=self._q("access_token", ""),
            on_event=self._on_event)
        self._client.start()
        # 身体感受：未读消息概览挂进每次唤醒
        ctx.kernel.add_wake_extras(
            lambda: self._chatlog.unread_summary() if self._chatlog else "")

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.stop()
        if self._chatlog is not None:
            self._chatlog.close()

    async def _on_event(self, data: dict) -> None:
        """OneBot 事件 → 分诊 → 信封 → notify / 背景听觉。"""
        self_id = int(self._q("bot_uin", 0) or 0)
        owner = int(self._q("owner_uin", 0) or 0)
        ev = parse_event(data, self_id=self_id, owner_uin=owner)
        if ev is None or ev.type == "ignore":
            return
        # 群白名单（空=全部）
        groups = self._q("listen_groups") or []
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
                                      meta={"sender_id": ev.sender_id}))

    # ── 表达面基建 ─────────────────────────────────────
    async def _call(self, action: str, params: dict | None = None) -> dict:
        return await self._client.call(action, params)

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
        if self._send_times and now - self._send_times[-1] < float(
                self._q("send_min_interval_s", 1.0)):
            return "发送过快（最小间隔限制），稍等再试"
        if len(self._send_times) >= int(self._q("send_max_per_min", 20)):
            return "已达每分钟发送上限，稍等再试"
        self._send_times.append(now)
        return None

    def _target_params(self, target_type: str, target_id: int) -> tuple[str, dict]:
        if target_type == "group":
            return "send_group_msg", {"group_id": int(target_id)}
        return "send_private_msg", {"user_id": int(target_id)}


def create(ctx: PluginContext) -> Plugin | None:
    if not ctx.cfg.plugins.get("qq", {}).get("enabled"):
        return None
    plugin = QQPlugin(ctx.cfg, ctx.data_dir)
    plugin.tools = _build_tools(plugin)
    return plugin


def _build_tools(p: QQPlugin) -> list[Tool]:
    tools: list[Tool] = []

    # ── 发消息 ────────────────────────────────────────
    async def send_message(args: dict) -> str:
        target_type = str(args.get("target_type") or "private")
        target_id = int(args.get("target_id") or 0)
        text = str(args.get("text") or "").strip()
        if not target_id or not text:
            return _err("需要 target_type(group/private)、target_id、text")
        blocked = p._throttle()
        if blocked:
            return _err(blocked, target=f"{target_type}_{target_id}")
        action, base = p._target_params(target_type, target_id)
        base["message"] = [{"type": "text", "data": {"text": text}}]

        async def _do():
            data = await p._call(action, base)
            return {"message_id": data.get("message_id")}
        return await p._receipt(_do, target=f"{target_type}_{target_id}",
                                action=action)

    async def send_image(args: dict) -> str:
        target_type = str(args.get("target_type") or "private")
        target_id = int(args.get("target_id") or 0)
        file = str(args.get("file") or "").strip()  # URL / 本地路径 / base64://
        caption = str(args.get("caption") or "")
        if not target_id or not file:
            return _err("需要 target_type、target_id、file（图片 URL 或路径）")
        blocked = p._throttle()
        if blocked:
            return _err(blocked)
        action, base = p._target_params(target_type, target_id)
        msg = [{"type": "image", "data": {"file": file}}]
        if caption:
            msg.append({"type": "text", "data": {"text": caption}})
        base["message"] = msg

        async def _do():
            data = await p._call(action, base)
            return {"message_id": data.get("message_id")}
        return await p._receipt(_do, target=f"{target_type}_{target_id}")

    async def recall_message(args: dict) -> str:
        mid = int(args.get("message_id") or 0)
        if not mid:
            return _err("需要 message_id")
        return await p._receipt(lambda: p._call("delete_msg", {"message_id": mid}))

    async def get_message_detail(args: dict) -> str:
        mid = int(args.get("message_id") or 0)
        if not mid:
            return _err("需要 message_id")

        async def _do():
            data = await p._call("get_msg", {"message_id": mid})
            return {"sender": (data.get("sender") or {}).get("nickname"),
                    "text": data.get("raw_message"), "ts": data.get("time")}
        return await p._receipt(_do)

    # ── 背景听觉 ──────────────────────────────────────
    async def read_chat_log(args: dict) -> str:
        target_type = str(args.get("target_type") or "group")
        target_id = int(args.get("target_id") or 0)
        n = min(int(args.get("n") or 20), 100)
        if not target_id:
            return _err("需要 target_type、target_id")
        chat_id = f"{target_type}_{target_id}"
        rows = p._chatlog.recent(chat_id, n)
        p._chatlog.mark_read(chat_id)
        if not rows:
            return _err("该会话暂无记录")
        lines = [f"{r['sender_name']}: {r['text']}" for r in rows]
        return json.dumps({"ok": True, "count": len(rows),
                           "log": "\n".join(lines)}, ensure_ascii=False)

    # ── 审批 ──────────────────────────────────────────
    async def approve_friend_request(args: dict) -> str:
        user_id = int(args.get("user_id") or 0)
        approve = bool(args.get("approve", True))
        flag = p._pending_friend.pop(user_id, None)
        if not flag:
            return _err("没有该用户的待审好友请求（或已处理）")

        async def _do():
            await p._call("set_friend_add_request",
                          {"flag": flag, "approve": approve})
            return {"approved": approve}
        return await p._receipt(_do, user_id=user_id)

    async def handle_group_request(args: dict) -> str:
        user_id = int(args.get("user_id") or 0)
        approve = bool(args.get("approve", True))
        flag = p._pending_group.pop(user_id, None)
        if not flag:
            return _err("没有该用户的待审加群请求（或已处理）")

        async def _do():
            await p._call("set_group_add_request",
                          {"flag": flag, "sub_type": "add",
                           "approve": approve})
            return {"approved": approve}
        return await p._receipt(_do, user_id=user_id)

    # ── 世界查询 ──────────────────────────────────────
    async def get_friends(args: dict) -> str:
        async def _do():
            data = await p._call("get_friend_list")
            return [{"id": f["user_id"], "name": f.get("nickname", "")}
                    for f in (data or [])[:100]]
        return await p._receipt(_do)

    async def get_groups(args: dict) -> str:
        async def _do():
            data = await p._call("get_group_list")
            return [{"id": g["group_id"], "name": g.get("group_name", "")}
                    for g in (data or [])[:100]]
        return await p._receipt(_do)

    async def get_group_members(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _err("需要 group_id")

        async def _do():
            data = await p._call("get_group_member_list", {"group_id": gid})
            return [{"id": m["user_id"], "name": m.get("card")
                     or m.get("nickname", "")}
                    for m in (data or [])[:200]]
        return await p._receipt(_do)

    async def get_group_info(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _err("需要 group_id")
        return await p._receipt(
            lambda: p._call("get_group_info", {"group_id": gid}))

    # ── 互动 ──────────────────────────────────────────
    async def poke(args: dict) -> str:
        target_type = str(args.get("target_type") or "private")
        target_id = int(args.get("target_id") or 0)
        user_id = int(args.get("user_id") or 0)  # 戳谁（群内需要）
        if not user_id:
            user_id = target_id
        action = "group_poke" if target_type == "group" else "friend_poke"
        # NapCat 扩展；标准端不支持时回执 api_error，心智自决
        params = ({"group_id": target_id, "user_id": user_id}
                  if target_type == "group" else {"user_id": user_id})
        return await p._receipt(lambda: p._call(action, params))

    async def send_like(args: dict) -> str:
        user_id = int(args.get("user_id") or 0)
        times = min(int(args.get("times") or 1), 10)
        if not user_id:
            return _err("需要 user_id")
        return await p._receipt(
            lambda: p._call("send_like", {"user_id": user_id, "times": times}))

    # ── 群操作 ────────────────────────────────────────
    async def set_group_card(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        card = str(args.get("card") or "")
        self_id = int(p._q("bot_uin", 0) or 0)
        if not gid or not card or not self_id:
            return _err("需要 group_id、card（且配置 bot_uin）")
        return await p._receipt(lambda: p._call(
            "set_group_card",
            {"group_id": gid, "user_id": self_id, "card": card}))

    async def leave_group(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _err("需要 group_id")
        return await p._receipt(
            lambda: p._call("set_group_leave",
                            {"group_id": gid, "is_dismiss": False}))

    async def mute_member(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        uid = int(args.get("user_id") or 0)
        duration = int(args.get("duration_s") or 600)
        if not gid or not uid:
            return _err("需要 group_id、user_id、duration_s（0=解禁）")
        return await p._receipt(lambda: p._call(
            "set_group_ban",
            {"group_id": gid, "user_id": uid, "duration": duration}))

    async def kick_member(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        uid = int(args.get("user_id") or 0)
        if not gid or not uid:
            return _err("需要 group_id、user_id")
        return await p._receipt(lambda: p._call(
            "set_group_kick", {"group_id": gid, "user_id": uid,
                               "reject_add_request": False}))

    # ── 状态与资料（NapCat 扩展，能力降级回执） ────────
    _STATUS = {"在线": 10, "离开": 30, "隐身": 40, "忙碌": 50,
               "Q我吧": 60, "请勿打扰": 70}
    _EXT_STATUS = {"无": 0, "听歌中": 1028, "学习中": 1018, "睡觉中": 1016,
                   "游戏中": 1027, "摸鱼中": 1300, "熬夜中": 1032, "Emo": 1401,
                   "我没事": 1052, "想静静": 1061, "悠哉哉": 1059, "想开了": 2013,
                   "去旅行": 2015, "信号弱": 1011}

    async def set_qq_status(args: dict) -> str:
        status = args.get("status", "在线")
        ext = args.get("ext_status", "无")
        code = p._STATUS.get(status, status)
        ext_code = p._EXT_STATUS.get(ext, ext)
        try:
            params = {"status": int(code), "ext_status": int(ext_code),
                      "battery_status": 0}
        except (TypeError, ValueError):
            return _err("status/ext_status 需是名称或数字码")
        return await p._receipt(lambda: p._call("set_online_status", params))

    async def set_profile(args: dict) -> str:
        params = {}
        if args.get("nickname"):
            params["nickname"] = str(args["nickname"])
        if args.get("personal_note"):
            params["personal_note"] = str(args["personal_note"])
        if not params:
            return _err("需要 nickname 或 personal_note 至少一项")
        return await p._receipt(lambda: p._call("set_qq_profile", params))

    # ── 逃生口 ────────────────────────────────────────
    async def qq_api(args: dict) -> str:
        action = str(args.get("action") or "").strip()
        if not action:
            return _err("需要 action（OneBot API 名）与 params")
        return await p._receipt(
            lambda: p._call(action, dict(args.get("params") or {})))

    tools.extend([
        Tool(schema={"name": "send_message",
                     "description": "发 QQ 消息（文本）。target_type=group/private，"
                                    "target_id 是群号或 QQ 号；回复时用信封里的来源。",
                     "inputSchema": {"type": "object", "properties": {
                         "target_type": {"type": "string"},
                         "target_id": {"type": "number"},
                         "text": {"type": "string"}},
                         "required": ["target_type", "target_id", "text"]}},
              func=send_message, interruptible=True),
        Tool(schema={"name": "send_image",
                     "description": "发图片。file 支持 URL/本地路径/base64:// 前缀。",
                     "inputSchema": {"type": "object", "properties": {
                         "target_type": {"type": "string"},
                         "target_id": {"type": "number"},
                         "file": {"type": "string"},
                         "caption": {"type": "string"}}},
                         "required": ["target_type", "target_id", "file"]},
              func=send_image, interruptible=True),
        Tool(schema={"name": "recall_message", "description": "撤回自己刚发的消息。",
                     "inputSchema": {"type": "object", "properties": {
                         "message_id": {"type": "number"}},
                         "required": ["message_id"]}}, func=recall_message),
        Tool(schema={"name": "get_message_detail", "description": "查一条消息的详情（谁发的/内容/时间）。",
                     "inputSchema": {"type": "object", "properties": {
                         "message_id": {"type": "number"}},
                         "required": ["message_id"]}}, func=get_message_detail),
        Tool(schema={"name": "read_chat_log",
                     "description": "看某个会话最近的聊天记录（背景听觉：你没注意时"
                                    "群里的对话都记着）。看完自动算已读。",
                     "inputSchema": {"type": "object", "properties": {
                         "target_type": {"type": "string"},
                         "target_id": {"type": "number"},
                         "n": {"type": "number"}},
                         "required": ["target_type", "target_id"]}},
              func=read_chat_log, interruptible=True),
        Tool(schema={"name": "approve_friend_request",
                     "description": "处理好友申请（approve=true 同意）。",
                     "inputSchema": {"type": "object", "properties": {
                         "user_id": {"type": "number"},
                         "approve": {"type": "boolean"}},
                         "required": ["user_id"]}}, func=approve_friend_request),
        Tool(schema={"name": "handle_group_request", "description": "处理加群申请/邀请。",
                     "inputSchema": {"type": "object", "properties": {
                         "user_id": {"type": "number"},
                         "approve": {"type": "boolean"}},
                         "required": ["user_id"]}}, func=handle_group_request),
        Tool(schema={"name": "get_friends", "description": "好友列表。",
                     "inputSchema": {"type": "object", "properties": {}}},
              func=get_friends),
        Tool(schema={"name": "get_groups", "description": "所在群列表。",
                     "inputSchema": {"type": "object", "properties": {}}},
              func=get_groups),
        Tool(schema={"name": "get_group_members", "description": "群成员列表。",
                     "inputSchema": {"type": "object", "properties": {
                         "group_id": {"type": "number"}},
                         "required": ["group_id"]}}, func=get_group_members),
        Tool(schema={"name": "get_group_info", "description": "群信息（名称/人数等）。",
                     "inputSchema": {"type": "object", "properties": {
                         "group_id": {"type": "number"}},
                         "required": ["group_id"]}}, func=get_group_info),
        Tool(schema={"name": "poke", "description": "戳一戳（NapCat 扩展，部分端不支持）。",
                     "inputSchema": {"type": "object", "properties": {
                         "target_type": {"type": "string"},
                         "target_id": {"type": "number"},
                         "user_id": {"type": "number"}}}},
              func=poke, interruptible=True),
        Tool(schema={"name": "send_like", "description": "给好友点赞（每天有次数限制）。",
                     "inputSchema": {"type": "object", "properties": {
                         "user_id": {"type": "number"},
                         "times": {"type": "number"}},
                         "required": ["user_id"]}}, func=send_like),
        Tool(schema={"name": "set_group_card",
                     "description": "改自己的群名片（自我呈现）。",
                     "inputSchema": {"type": "object", "properties": {
                         "group_id": {"type": "number"},
                         "card": {"type": "string"}},
                         "required": ["group_id", "card"]}}, func=set_group_card),
        Tool(schema={"name": "leave_group", "description": "退出一个群（慎重，自决）。",
                     "inputSchema": {"type": "object", "properties": {
                         "group_id": {"type": "number"}},
                         "required": ["group_id"]}}, func=leave_group),
        Tool(schema={"name": "mute_member", "description": "禁言群成员（需群管理权限；0=解禁）。",
                     "inputSchema": {"type": "object", "properties": {
                         "group_id": {"type": "number"},
                         "user_id": {"type": "number"},
                         "duration_s": {"type": "number"}},
                         "required": ["group_id", "user_id"]}}, func=mute_member),
        Tool(schema={"name": "kick_member", "description": "踢出群成员（需群主/管理权限，慎用）。",
                     "inputSchema": {"type": "object", "properties": {
                         "group_id": {"type": "number"},
                         "user_id": {"type": "number"}},
                         "required": ["group_id", "user_id"]}}, func=kick_member),
        Tool(schema={"name": "set_qq_status",
                     "description": "设置 QQ 在线状态（如 睡觉中/摸鱼中/熬夜中——"
                                    "应反映你的真实状态）。",
                     "inputSchema": {"type": "object", "properties": {
                         "status": {"type": "string"},
                         "ext_status": {"type": "string"}}}},
              func=set_qq_status, interruptible=True),
        Tool(schema={"name": "set_profile", "description": "改昵称/个性签名。",
                     "inputSchema": {"type": "object", "properties": {
                         "nickname": {"type": "string"},
                         "personal_note": {"type": "string"}}}},
              func=set_profile, interruptible=True),
        Tool(schema={"name": "qq_api",
                     "description": "原始 OneBot API 逃生口（标准接口没覆盖时用）。",
                     "inputSchema": {"type": "object", "properties": {
                         "action": {"type": "string"},
                         "params": {"type": "object"}},
                         "required": ["action"]}}, func=qq_api),
    ])
    return tools
