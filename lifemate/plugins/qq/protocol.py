"""OneBot v11 事件解析（纯函数，零 IO——单测友好）。

OneBot 事件 JSON → ParsedEvent（信封素材），由插件层组装成 Envelope。
对照协议：../qq-agent/docs/onebot-11/（message/segment、event/*）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedEvent:
    """解析产物：type 决定后续路由（direct/ambient/platform_event/ignore）。"""

    type: str  # direct / ambient / platform_event / ignore
    chat_id: str = ""  # group_{id} / private_{id}
    sender_id: int = 0
    sender_name: str = ""
    text: str = ""  # 已拼装的可读文本（含 [图片]/[表情] 等标注）
    message_id: int = 0
    image_urls: list[str] = field(default_factory=list)
    is_owner: bool = False
    raw: dict = field(default_factory=dict)


def _parse_message(event: dict, self_id: int, owner_uin: int, reply_lookup=None, aliases=None) -> ParsedEvent:
    is_group = event.get("message_type") == "group"
    sender = event.get("sender") or {}
    sender_id = event.get("user_id", 0)
    sender_name = sender.get("card") or sender.get("nickname") or str(sender_id)
    texts: list[str] = []
    image_urls: list[str] = []
    at_me = False
    reply_to_me = False
    message = event.get("message", [])
    if isinstance(message, str):  # CQ 码字符串格式兼容
        texts.append(message)
        if f"[CQ:at,qq={self_id}]" in message:
            at_me = True
    else:
        for seg in message or []:
            stype = seg.get("type")
            data = seg.get("data", {})
            if stype == "text":
                texts.append(data.get("text", ""))
            elif stype == "image":
                url = data.get("url") or data.get("file") or ""
                if url:
                    image_urls.append(url)
                texts.append("[图片]")
            elif stype == "at":
                if str(data.get("qq")) == str(self_id):
                    at_me = True
                else:
                    texts.append(f"@{data.get('qq')}")
            elif stype == "reply":
                target = data.get("id")
                if reply_lookup is None:
                    reply_to_me = True
                    texts.append(f"[回复{target}]")
                else:
                    try:
                        origin = reply_lookup(int(target))
                    except (TypeError, ValueError):
                        origin = None
                    if origin is None:
                        reply_to_me = False
                        texts.append(f"[回复{target}]")
                    else:
                        reply_to_me = origin["sender_id"] == self_id
                        head = origin.get("sender_name") or str(origin["sender_id"])
                        snippet = (origin.get("text") or "").replace("\n", " ")[:30]
                        texts.append(f"[回复 {head}：{snippet}]" if snippet else f"[回复 {head}]")
            elif stype == "face":
                texts.append(f"[表情{data.get('id')}]")
            elif stype == "record":
                texts.append("[语音]")
            elif stype == "video":
                texts.append("[视频]")
            elif stype == "forward":
                texts.append("[合并转发]")
            elif stype in ("json", "xml"):
                texts.append(f"[{stype}卡片]")
            elif stype == "poke":
                texts.append("[戳一戳]")
    chat_id = f"group_{event['group_id']}" if is_group else f"private_{sender_id}"
    is_owner = bool(owner_uin) and sender_id == owner_uin
    text = "".join(texts).strip()
    # 唤醒别名：叫名字不@也算呼叫（误报只是多看一眼，漏报伤社交）
    alias_hit = any(a and a in text for a in (aliases or []))
    direct = (not is_group) or at_me or reply_to_me or alias_hit
    # 注：主人（is_owner）不再无条件直达——@/别名/回复/私聊/对话延续窗口
    # 已覆盖真实呼叫场景；主人跟别人说话不该唤醒。标志仅用于（主人）标注
    return ParsedEvent(type="direct" if direct else "ambient", chat_id=chat_id, sender_id=sender_id, sender_name=sender_name, text=text, message_id=event.get("message_id", 0), image_urls=image_urls, is_owner=is_owner, raw=event)


def parse_event(event: dict, self_id: int = 0, owner_uin: int = 0, reply_lookup=None, aliases: list[str] | None = None) -> ParsedEvent | None:
    """OneBot 事件 → ParsedEvent；无法翻译的返回 None（调用方忽略）。

    reply_lookup(message_id) -> {sender_id, sender_name, text} | None：
    回复链解引用通道（chat_log 本地反查）；None（无通道）时保守直达。
    aliases：唤醒别名表——消息文本含任一别名即强刺激（叫名字不@）。"""
    post_type = event.get("post_type", "")

    if post_type == "message":
        return _parse_message(event, self_id, owner_uin, reply_lookup, aliases)

    if post_type == "meta_event":
        return None  # 心跳/生命周期：连接层自理

    if post_type == "request":
        rt = event.get("request_type", "")
        if rt == "friend":
            return ParsedEvent(type="platform_event", chat_id=f"private_{event.get('user_id', 0)}", sender_id=event.get("user_id", 0), text=f"（有人申请加你好友：{event.get('user_id')}，验证信息：{event.get('comment') or '（无）'}）", raw=event)
        if rt == "group":
            return ParsedEvent(type="platform_event", chat_id=f"group_{event.get('group_id', 0)}", sender_id=event.get("user_id", 0), text=f"（有人申请加入群 {event.get('group_id')}：{event.get('user_id')}，验证：{event.get('comment') or '（无）'}）", raw=event)
        return None

    if post_type == "notice":
        nt = event.get("notice_type", "")
        sub = event.get("sub_type", "")
        uid = event.get("user_id", 0)
        gid = event.get("group_id", 0)
        if nt == "group_decrease" and uid == self_id:
            return ParsedEvent(type="platform_event", chat_id=f"group_{gid}", sender_id=uid, text=f"（你{'被移出' if sub == 'kick_me' else '退出了'}群 {gid}）", raw=event)
        if nt == "group_increase" and uid == self_id:
            return ParsedEvent(type="platform_event", chat_id=f"group_{gid}", sender_id=uid, text=f"（你加入了群 {gid}）", raw=event)
        if nt == "friend_add":
            return ParsedEvent(type="platform_event", chat_id=f"private_{uid}", sender_id=uid, text=f"（{uid} 和你成为了好友）", raw=event)
        if nt == "notify" and sub == "poke" and event.get("target_id") == self_id:
            # 戳一戳自己（user_id=发起方，target_id=被戳方）：显式互动，按强刺激
            operator = event.get("user_id", 0)
            return ParsedEvent(type="direct", chat_id=f"group_{gid}" if gid else f"private_{operator}", sender_id=operator, text=f"（{operator} 戳了你一下）", raw=event)
        if nt in ("group_recall", "friend_recall"):
            # 自己的消息被撤回：值得知道的自我相关事件
            user = event.get("user_id", 0)
            if user == self_id:
                where = f"群 {gid}" if gid else "私聊"
                return ParsedEvent(type="platform_event", chat_id=f"group_{gid}" if gid else f"private_{event.get('user_id', 0)}", sender_id=user, text=f"（你刚刚在{where}发的消息被撤回了）", raw=event)
        return None

    return None


def render_segments(segments: list) -> str:
    """消息段数组 → 可读文本（信封同款标注；合并转发节点渲染用）。

    与 _parse_message 的段处理同构但独立——那边要同时收集 at_me/图片等
    路由素材，这里只求可读；图片带 URL（look_image 可直接用）。"""
    texts: list[str] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        stype = seg.get("type")
        data = seg.get("data") or {}
        if stype == "text":
            texts.append(str(data.get("text") or ""))
        elif stype == "image":
            url = data.get("url") or data.get("file") or ""
            texts.append(f"[图片 {url}]" if url else "[图片]")
        elif stype == "at":
            texts.append(f"@{data.get('qq')}")
        elif stype == "reply":
            texts.append(f"[回复{data.get('id')}]")
        elif stype == "face":
            texts.append(f"[表情{data.get('id')}]")
        elif stype == "record":
            texts.append("[语音]")
        elif stype == "video":
            texts.append("[视频]")
        elif stype == "forward":
            texts.append("[合并转发]")
        elif stype in ("json", "xml"):
            texts.append(f"[{stype}卡片]")
        elif stype == "poke":
            texts.append("[戳一戳]")
    return "".join(texts).strip()


def render_forward(data: dict) -> list[str]:
    """get_forward_msg 响应 → 可读行列表（发话人: 内容）。

    兼容两种形态：OneBot 标准（message 数组为 node 段，内容在 data.content）
    与 NapCat（直接铺开的消息对象，sender + message/content）。嵌套的
    合并转发只标注不再展开（qq_api 逃生口仍在）。"""
    nodes = data.get("message") or data.get("messages") or []
    lines: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "node":
            d = n.get("data") or {}
            name = str(d.get("nickname") or d.get("user_id") or "?")
            content = d.get("content")
        else:
            sender = n.get("sender") or {}
            name = str(sender.get("card") or sender.get("nickname") or sender.get("user_id") or "?")
            content = n.get("message") if isinstance(n.get("message"), list) else n.get("content")
        text = content if isinstance(content, str) else render_segments(content or [])
        lines.append(f"{name}: {text}" if text else f"{name}: （空）")
    return lines


def forward_id_of(msg: dict) -> str:
    """get_msg 响应 → 合并转发 ID（数组段优先，raw_message 的 CQ 码兜底）。"""
    for seg in msg.get("message") or []:
        if isinstance(seg, dict) and seg.get("type") == "forward":
            return str((seg.get("data") or {}).get("id") or "")
    m = re.search(r"\[CQ:forward,id=([^\],]+)", str(msg.get("raw_message") or ""))
    return m.group(1) if m else ""


def compose_envelope_text(ev: ParsedEvent) -> str:
    """ParsedEvent → 信封文本（自然语言信封：场景标注 + 内容）。"""
    if ev.type == "platform_event":
        return ev.text
    where = ev.chat_id
    if where.startswith("group_"):
        head = f"[群聊·{where.split('_', 1)[1]}] {ev.sender_name}"
        if ev.is_owner:
            head += "（主人）"
        if ev.type == "direct":
            return f"{head} 对你说：{ev.text}"
        return f"{head}：{ev.text}"
    head = f"[私聊{'·主人' if ev.is_owner else ''}] {ev.sender_name}"
    return f"{head}：{ev.text}"
