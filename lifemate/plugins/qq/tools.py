"""QQ 工具集：表达面（20 个工具工厂）。

约定：每个模块的工具住该模块的 tools.py，无例外。
所有工具闭包持有插件实例 p（感知面在 __init__.py 的 QQPlugin），
经 p._call/p._receipt 出站——回执制，永不抛错。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ...kernel.contracts import Tool
from .chatlog import format_ts, render_log_lines
from .protocol import forward_id_of, render_forward

if TYPE_CHECKING:
    from . import QQPlugin


def _ok(**kw) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def _err(msg: str, **kw) -> str:
    return json.dumps({"ok": False, "error": msg, **kw}, ensure_ascii=False)


_URLISH_RE = re.compile(r"^(https?://|base64://|data:|file://)", re.I)


def _resolve_media_path(file: str, cfg) -> str:
    """相对路径 → 绝对路径（发媒体前的适配）。

    NapCat 按它自己的 cwd 解析相对路径（多半对不上），出站前钉死绝对位。
    候选依次取第一个存在的：原样（本进程 cwd）→ {data_dir}/file →
    {data_dir}/images/文件名（find_image 回执给的就是这种）。都不在就
    原样放行，让对端报错、回执制兑底。URL/base64/data: 直通。"""
    if _URLISH_RE.match(file):
        return file
    p = Path(file)
    if p.is_absolute():
        return str(p)
    data_dir = Path(getattr(cfg, "data_dir", "") or ".")
    for cand in (p, data_dir / file, data_dir / "images" / p.name):
        if cand.is_file():
            return str(cand.resolve())
    return file


def build_tools(p: QQPlugin) -> list[Tool]:
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
            mid = int(data.get("message_id") or 0)
            p._log_sent(f"{target_type}_{target_id}", text, mid)
            # 回执复述动作事实：心智凭此审视刚发出的话（撤回句柄也在）
            return {"message_id": mid, "text": text}

        return await p._receipt(_do, target=f"{target_type}_{target_id}", action=action)

    async def send_image(args: dict) -> str:
        target_type = str(args.get("target_type") or "private")
        target_id = int(args.get("target_id") or 0)
        file = _resolve_media_path(str(args.get("file") or "").strip(), p._cfg)  # 相对路径先解析
        caption = str(args.get("caption") or "")
        if not target_id or not file:
            return _err("需要 target_type、target_id、file（图片 URL 或路径）")
        # 本地文件 → base64:// 直嘂字节：不依赖 NapCat 与本进程同机/同 cwd
        # （URL/base64/data: 直通，NapCat 自己取）；超上限回执报错不硬发
        if not _URLISH_RE.match(file):
            pth = Path(file)
            if pth.is_file():
                data = await asyncio.to_thread(pth.read_bytes)
                max_mb = float(p._conf.send_image_max_mb)
                if len(data) > max_mb * 1_048_576:
                    return _err(f"图片太大（{len(data) / 1_048_576:.1f}MB > 上限 {max_mb:g}MB）：换个小的，或转告主人调 send_image_max_mb")
                file = "base64://" + base64.b64encode(data).decode("ascii")
        blocked = p._throttle()
        if blocked:
            return _err(blocked)
        action, base = p._target_params(target_type, target_id)
        msg = [{"type": "image", "data": {"file": file}}]
        if caption:
            msg.append({"type": "text", "data": {"text": caption}})
        base["message"] = msg
        sent_text = "[图片]" + (f" {caption}" if caption else "")

        async def _do():
            data = await p._call(action, base)
            mid = int(data.get("message_id") or 0)
            p._log_sent(f"{target_type}_{target_id}", sent_text, mid)
            return {"message_id": mid, "text": sent_text}

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
            ts = float(data.get("time") or 0)
            return {"sender": (data.get("sender") or {}).get("nickname"), "text": data.get("raw_message"), "ts": ts, "time": format_ts(ts, tz_name=p._cfg.timezone) if ts else ""}

        return await p._receipt(_do)

    async def read_forward(args: dict) -> str:
        mid = int(args.get("message_id") or 0)
        if not mid:
            return _err("需要 message_id（那条 [合并转发] 消息的 ID，read_chat_log 里有句柄）")

        async def _do():
            # 先取该消息解出合并转发 ID（resid），再取内容——心智不再手搓 CQ
            msg = await p._call("get_msg", {"message_id": mid})
            fwd = forward_id_of(msg)
            if not fwd:
                raise ValueError("这条消息不包含合并转发")
            data = await p._call("get_forward_msg", {"id": fwd})
            lines = render_forward(data)
            if not lines:
                raise ValueError("合并转发内容为空（可能已过期或被撤回）")
            return {"count": len(lines), "log": "\n".join(lines)}

        return await p._receipt(_do, message_id=mid)

    async def reply_to(args: dict) -> str:
        mid = int(args.get("message_id") or 0)
        text = str(args.get("text") or "").strip()
        if not mid or not text:
            return _err("需要 message_id（回复哪条消息）、text")
        blocked = p._throttle()
        if blocked:
            return _err(blocked)

        async def _do():
            # 反查那条消息定位会话：回复去它所在的地方，不会回错群
            origin = await p._call("get_msg", {"message_id": mid})
            gid = int(origin.get("group_id") or 0)
            if gid:
                action, base, chat_id = "send_group_msg", {"group_id": gid}, f"group_{gid}"
            else:
                uid = int((origin.get("sender") or {}).get("user_id") or 0)
                if not uid or uid == int(p._conf.bot_uin or 0):
                    raise ValueError("定位不到会话（自己发的私聊消息查不到对方）；私聊直接用 send_message")
                action, base, chat_id = "send_private_msg", {"user_id": uid}, f"private_{uid}"
            base["message"] = [{"type": "reply", "data": {"id": mid}}, {"type": "text", "data": {"text": text}}]
            data = await p._call(action, base)
            new_mid = int(data.get("message_id") or 0)
            p._log_sent(chat_id, text, new_mid)
            # 回执复述动作事实（行动后审视的凭据）
            return {"message_id": new_mid, "text": text, "reply_to": mid, "target": chat_id}

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
        # 每条带 message_id 句柄：reply_to（引用回复）/read_forward（读合并转发）的定位凭据
        lines = render_log_lines(rows, bot_uin=p._conf.bot_uin, tz_name=p._cfg.timezone)
        return json.dumps({"ok": True, "count": len(rows), "log": "\n".join(lines)}, ensure_ascii=False)

    # ── 审批 ──────────────────────────────────────────
    async def approve_friend_request(args: dict) -> str:
        user_id = int(args.get("user_id") or 0)
        approve = bool(args.get("approve", True))
        flag = p._pending_friend.pop(user_id, None)
        if not flag:
            return _err("没有该用户的待审好友请求（或已处理）")

        async def _do():
            await p._call("set_friend_add_request", {"flag": flag, "approve": approve})
            return {"approved": approve}

        return await p._receipt(_do, user_id=user_id)

    async def handle_group_request(args: dict) -> str:
        user_id = int(args.get("user_id") or 0)
        approve = bool(args.get("approve", True))
        flag = p._pending_group.pop(user_id, None)
        if not flag:
            return _err("没有该用户的待审加群请求（或已处理）")

        async def _do():
            await p._call("set_group_add_request", {"flag": flag, "sub_type": "add", "approve": approve})
            return {"approved": approve}

        return await p._receipt(_do, user_id=user_id)

    # ── 世界查询 ──────────────────────────────────────
    async def get_friends(args: dict) -> str:
        async def _do():
            data = await p._call("get_friend_list")
            return [{"id": f["user_id"], "name": f.get("nickname", "")} for f in (data or [])[:100]]

        return await p._receipt(_do)

    async def get_groups(args: dict) -> str:
        async def _do():
            data = await p._call("get_group_list")
            return [{"id": g["group_id"], "name": g.get("group_name", "")} for g in (data or [])[:100]]

        return await p._receipt(_do)

    async def get_group_members(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _err("需要 group_id")

        async def _do():
            data = await p._call("get_group_member_list", {"group_id": gid})
            return [{"id": m["user_id"], "name": m.get("card") or m.get("nickname", "")} for m in (data or [])[:200]]

        return await p._receipt(_do)

    async def get_group_info(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _err("需要 group_id")
        return await p._receipt(lambda: p._call("get_group_info", {"group_id": gid}))

    # ── 互动 ──────────────────────────────────────────
    async def poke(args: dict) -> str:
        target_type = str(args.get("target_type") or "private")
        target_id = int(args.get("target_id") or 0)
        user_id = int(args.get("user_id") or 0)  # 戳谁（群内需要）
        if not user_id:
            user_id = target_id
        action = "group_poke" if target_type == "group" else "friend_poke"
        # NapCat 扩展；标准端不支持时回执 api_error，心智自决
        params = {"group_id": target_id, "user_id": user_id} if target_type == "group" else {"user_id": user_id}
        return await p._receipt(lambda: p._call(action, params))

    async def send_like(args: dict) -> str:
        user_id = int(args.get("user_id") or 0)
        times = min(int(args.get("times") or 1), 10)
        if not user_id:
            return _err("需要 user_id")
        return await p._receipt(lambda: p._call("send_like", {"user_id": user_id, "times": times}))

    # ── 群操作 ────────────────────────────────────────
    async def set_group_card(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        card = str(args.get("card") or "")
        self_id = p._conf.bot_uin
        if not gid or not card or not self_id:
            return _err("需要 group_id、card（且配置 bot_uin）")
        return await p._receipt(lambda: p._call("set_group_card", {"group_id": gid, "user_id": self_id, "card": card}))

    async def leave_group(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _err("需要 group_id")
        return await p._receipt(lambda: p._call("set_group_leave", {"group_id": gid, "is_dismiss": False}))

    async def mute_member(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        uid = int(args.get("user_id") or 0)
        duration = int(args.get("duration_s") or 600)
        if not gid or not uid:
            return _err("需要 group_id、user_id、duration_s（0=解禁）")
        return await p._receipt(lambda: p._call("set_group_ban", {"group_id": gid, "user_id": uid, "duration": duration}))

    async def kick_member(args: dict) -> str:
        gid = int(args.get("group_id") or 0)
        uid = int(args.get("user_id") or 0)
        if not gid or not uid:
            return _err("需要 group_id、user_id")
        return await p._receipt(lambda: p._call("set_group_kick", {"group_id": gid, "user_id": uid, "reject_add_request": False}))

    # ── 状态与资料（NapCat 扩展，能力降级回执） ────────
    _STATUS = {"在线": 10, "离开": 30, "隐身": 40, "忙碌": 50, "Q我吧": 60, "请勿打扰": 70}
    _EXT_STATUS = {"无": 0, "听歌中": 1028, "学习中": 1018, "睡觉中": 1016, "游戏中": 1027, "摸鱼中": 1300, "熬夜中": 1032, "Emo": 1401, "我没事": 1052, "想静静": 1061, "悠哉哉": 1059, "想开了": 2013, "去旅行": 2015, "信号弱": 1011}

    async def set_qq_status(args: dict) -> str:
        status = args.get("status", "在线")
        ext = args.get("ext_status", "无")
        code = _STATUS.get(status, status)  # 闭包局部表，非实例属性
        ext_code = _EXT_STATUS.get(ext, ext)
        try:
            params = {"status": int(code), "ext_status": int(ext_code), "battery_status": 0}
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
        return await p._receipt(lambda: p._call(action, dict(args.get("params") or {})))

    tools.extend(
        [
            Tool(schema={"name": "send_message", "description": "发 QQ 消息（文本）。target_type=group/private，target_id 是群号或 QQ 号；回复时用信封里的来源。", "inputSchema": {"type": "object", "properties": {"target_type": {"type": "string"}, "target_id": {"type": "number"}, "text": {"type": "string"}}, "required": ["target_type", "target_id", "text"]}}, func=send_message, interruptible=True),
            Tool(schema={"name": "send_image", "description": "发图片。file 支持 URL/本地路径/base64:// 前缀；本地文件（含 find_image 回执里的相对路径）会自动读出并转 base64:// 发送，不要求 NapCat 同机。", "inputSchema": {"type": "object", "properties": {"target_type": {"type": "string"}, "target_id": {"type": "number"}, "file": {"type": "string"}, "caption": {"type": "string"}}}, "required": ["target_type", "target_id", "file"]}, func=send_image, interruptible=True),
            Tool(schema={"name": "recall_message", "description": "撤回自己刚发的消息。", "inputSchema": {"type": "object", "properties": {"message_id": {"type": "number"}}, "required": ["message_id"]}}, func=recall_message),
            Tool(schema={"name": "get_message_detail", "description": "查一条消息的详情（谁发的/内容/时间）。", "inputSchema": {"type": "object", "properties": {"message_id": {"type": "number"}}, "required": ["message_id"]}}, func=get_message_detail),
            Tool(schema={"name": "read_forward", "description": "读一条合并转发消息的完整内容（信封里显示 [合并转发] 的那种）。message_id 从 read_chat_log 的句柄获取；内部自动解引用转发 ID。", "inputSchema": {"type": "object", "properties": {"message_id": {"type": "number", "description": "那条 [合并转发] 消息的 message_id"}}, "required": ["message_id"]}}, func=read_forward),
            Tool(schema={"name": "reply_to", "description": "引用回复：回复指定消息并附上你的文本（对方会看到引用）。自动发到那条消息所在的会话，不会回错地方；message_id 从 read_chat_log 的句柄获取。", "inputSchema": {"type": "object", "properties": {"message_id": {"type": "number", "description": "要回复哪条消息"}, "text": {"type": "string", "description": "回复内容"}}, "required": ["message_id", "text"]}}, func=reply_to, interruptible=True),
            Tool(schema={"name": "read_chat_log", "description": "看某个会话最近的聊天记录（背景听觉：你没注意时群里的对话都记着）。每条带时间标注，注意区分刚发的和早前的消息。看完自动算已读。", "inputSchema": {"type": "object", "properties": {"target_type": {"type": "string"}, "target_id": {"type": "number"}, "n": {"type": "number"}}, "required": ["target_type", "target_id"]}}, func=read_chat_log, interruptible=True),
            Tool(schema={"name": "approve_friend_request", "description": "处理好友申请（approve=true 同意）。", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "number"}, "approve": {"type": "boolean"}}, "required": ["user_id"]}}, func=approve_friend_request),
            Tool(schema={"name": "handle_group_request", "description": "处理加群申请/邀请。", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "number"}, "approve": {"type": "boolean"}}, "required": ["user_id"]}}, func=handle_group_request),
            Tool(schema={"name": "get_friends", "description": "好友列表。", "inputSchema": {"type": "object", "properties": {}}}, func=get_friends),
            Tool(schema={"name": "get_groups", "description": "所在群列表。", "inputSchema": {"type": "object", "properties": {}}}, func=get_groups),
            Tool(schema={"name": "get_group_members", "description": "群成员列表。", "inputSchema": {"type": "object", "properties": {"group_id": {"type": "number"}}, "required": ["group_id"]}}, func=get_group_members),
            Tool(schema={"name": "get_group_info", "description": "群信息（名称/人数等）。", "inputSchema": {"type": "object", "properties": {"group_id": {"type": "number"}}, "required": ["group_id"]}}, func=get_group_info),
            Tool(schema={"name": "poke", "description": "戳一戳（NapCat 扩展，部分端不支持）。", "inputSchema": {"type": "object", "properties": {"target_type": {"type": "string"}, "target_id": {"type": "number"}, "user_id": {"type": "number"}}}}, func=poke, interruptible=True),
            Tool(schema={"name": "send_like", "description": "给好友点赞（每天有次数限制）。", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "number"}, "times": {"type": "number"}}, "required": ["user_id"]}}, func=send_like),
            Tool(schema={"name": "set_group_card", "description": "改自己的群名片（自我呈现）。", "inputSchema": {"type": "object", "properties": {"group_id": {"type": "number"}, "card": {"type": "string"}}, "required": ["group_id", "card"]}}, func=set_group_card),
            Tool(schema={"name": "leave_group", "description": "退出一个群（慎重，自决）。", "inputSchema": {"type": "object", "properties": {"group_id": {"type": "number"}}, "required": ["group_id"]}}, func=leave_group),
            Tool(schema={"name": "mute_member", "description": "禁言群成员（需群管理权限；0=解禁）。", "inputSchema": {"type": "object", "properties": {"group_id": {"type": "number"}, "user_id": {"type": "number"}, "duration_s": {"type": "number"}}, "required": ["group_id", "user_id"]}}, func=mute_member),
            Tool(schema={"name": "kick_member", "description": "踢出群成员（需群主/管理权限，慎用）。", "inputSchema": {"type": "object", "properties": {"group_id": {"type": "number"}, "user_id": {"type": "number"}}, "required": ["group_id", "user_id"]}}, func=kick_member),
            Tool(schema={"name": "set_qq_status", "description": "设置 QQ 在线状态（如 睡觉中/摸鱼中/熬夜中——应反映你的真实状态）。", "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "ext_status": {"type": "string"}}}}, func=set_qq_status, interruptible=True),
            Tool(schema={"name": "set_profile", "description": "改昵称/个性签名。", "inputSchema": {"type": "object", "properties": {"nickname": {"type": "string"}, "personal_note": {"type": "string"}}}}, func=set_profile, interruptible=True),
            Tool(schema={"name": "qq_api", "description": "原始 OneBot API 逃生口（标准接口没覆盖时用）。", "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}, "params": {"type": "object"}}, "required": ["action"]}}, func=qq_api),
        ]
    )
    return tools
