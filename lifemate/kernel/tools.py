"""内核工具：思维生理（wait）+ 知情层入口（read_skill）+ 整觉（deep_sleep）+ 看图（look_image）。

约定：每个模块的工具住该模块的 tools.py，无例外——开发者找工具只看这里。

工厂依赖全部作参数传入（kernel/persona/store 实例，鸭子类型），
本文件只 import kernel.contracts——不引入新的依赖方向：
- wait 咬着思维循环的挂起/唤醒机制（_do_wait/_emit/_new_rid），
  是思考本身的生理，故由 mind 持有并注册
- deep_sleep 横跨 kernel（轮换会话）与 store（存信）：装配在 host
  （二者相遇处），工具定义在此
- look_image 横跨 llm（vision 通路）与 store（感知记忆）：装配同在
  host；看懂图像是心智天赋（模态中立），采集图像归器官
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils import clock
from .contracts import REST, WAKE, Tool

if TYPE_CHECKING:
    from ..memory.store import Store
    from .mind import SelfKernel
    from .persona import Persona


def make_wait_tool(kernel: SelfKernel) -> Tool:
    """等待：挂起思考直到有新输入/到期提醒/超时（等待是思维的一部分）。"""

    async def _fn(args: dict) -> str:
        timeout = args.get("timeout_s")
        reason = str(args.get("reason") or "")
        dnd = str(args.get("wake_on") or "any").strip().lower() == "direct"
        t: float | None = None
        if timeout is not None:
            t = max(float(timeout), 1.0)  # 最短 1 秒
        kernel._emit(REST, rid=kernel._rid, reason=f"explicit{':' + reason if reason else ''}", timeout_s=t, dnd=dnd)
        wake = await kernel._do_wait(t, "explicit", intent=reason, dnd=dnd)
        # 唤醒作为工具结果返回：同回合无缝延续（自己定闹钟自己醒）
        kernel._new_rid()
        kernel._emit(WAKE, rid=kernel._rid, trigger="explicit_wait")
        return wake

    return Tool(
        schema={
            "name": "wait",
            "description": "等待：挂起思考直到有新输入/到期提醒/超时。给自己定闹钟用 timeout_s（秒）并可在 reason 里写上要做什么；不传 timeout_s 则一直等到有事件。等待零成本。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout_s": {"type": "number", "description": "多少秒后醒来（闹钟）"},
                    "reason": {"type": "string", "description": "在等什么/到点做什么"},
                    "wake_on": {"type": "string", "enum": ["any", "direct"], "description": "any=默认，任何事件都唤醒：群消息攒批到量也会递到你眼前，想留意群里动态用它。direct=免打扰长睡专用：只被直达（@/私聊/叫名/回复/戳）和自己定的闹钟唤醒，群聊消息完全不递、攒批也不唤醒——代价是整段等待里你对群里动态零感知，只能到点靠闹钟回看。注意两点：①你发言后约 10 分钟内该会话的跟话也算直达，仍会唤醒你；②它屏蔽的是感知不是发言——'不想接话'不该用它，用 any 醒来看一眼不说话即可"},
                },
            },
        },
        func=_fn,
        interruptible=False,
    )


def make_read_skill_tool(persona: Persona) -> Tool:
    """读技能全文（知情层入口；索引常驻系统提示，全文按需读取）。"""

    async def _read(args: dict) -> str:
        return persona.read_skill(str(args.get("name") or ""))

    return Tool(
        schema={
            "name": "read_skill",
            "description": "读取一份技能的完整说明（先用系统提示里的索引选择）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                },
                "required": ["name"],
            },
        },
        func=_read,
    )


def make_deep_sleep_tool(kernel: SelfKernel, store: Store) -> Tool:
    """整觉：写信（重建包）→ 转身（换会话播种）→ 长眠。睡眠制度。"""

    async def _fn(args: dict) -> str:
        letter = str(args.get("letter") or "").strip()
        if not letter:
            return json.dumps({"ok": False, "error": "letter 不能为空（deep_sleep 保存重建包后轮换会话）"}, ensure_ascii=False)
        hours = max(0.1, min(float(args.get("hours") or 8), 72.0))
        store.save_packet(letter)
        pend = store.pending_reminders(5)
        todo = "；".join(r["intent"][:40] for r in pend) or "无"
        seed = "（深睡结束，会话已轮换。上一会话留下的信：\n" + letter + f"\n\n待办提醒：{todo}）"
        kernel.swap_session(seed)
        return await kernel.rest(hours * 3600, dnd=True)  # 整觉默认免打扰：只被直达/闹钟唤醒

    return Tool(
        schema={
            "name": "deep_sleep",
            "description": "整觉：给下一个自己写信（必填）→ 轮换新会话 → 睡指定小时。一天的结束用它；轻度小睡用 wait 即可。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "letter": {"type": "string", "description": "写给下一个自己的信（我是谁/最近经历/未办/心情）"},
                    "hours": {"type": "number", "description": "睡多久（小时，默认 8）"},
                },
                "required": ["letter"],
            },
        },
        func=_fn,
        interruptible=False,
    )


# ── 看图 look_image ──────────────────────────────────


class _Reject(Exception):
    """取图被拒（回执制：转 ok=False，绝不抛穿思维循环）。"""


_FETCH_TIMEOUT_S = 20.0  # 取图超时（网络兑现上限，非行为参数）
_MEDIA_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp"}
_DESC_MAX = 1500  # 描述入库/回执截断字符


def _ok_receipt(**kw) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def _err_receipt(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _sniff_image(data: bytes) -> str:
    """魔数嗅探（内容即真相，不信任声明）。"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return ""


def _assert_public_http(url: str) -> None:
    """防 SSRF：只允许公网 http(s) 目标（内网/回环/链路本地一律拒绝）。

    注：解析后连接存在 DNS 重绑定窗口，对当前威胁模型（取图工具）
    可接受；将来接入不可信输入再收紧（解析即连接固定 IP）。
    """
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise _Reject(f"仅支持 http/https 取图（收到：{p.scheme or '无协议'}）")
    host = p.hostname
    if not host:
        raise _Reject("URL 缺少主机名")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise _Reject(f"主机解析失败：{e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise _Reject(f"拒绝访问非公网地址：{host}")


def _http_get_image(url: str, max_bytes: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (lifemate-look-image)"})
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            data = resp.read(max_bytes + 1)
    except Exception as e:  # noqa: BLE001
        raise _Reject(f"取图失败（URL 可能已过期）：{e}") from e
    if len(data) > max_bytes:
        raise _Reject(f"图片超过大小上限（{max_bytes // 1_048_576}MB）")
    return data, ctype


def _resolve_image(image: str, max_bytes: int) -> tuple[bytes, str]:
    """四形态归一：URL / data:URI / base64:// / 本地路径 → (字节, 媒体类型)。

    广泛适用性：任何器官将来都可以用任一形态喂图（web 插件的网页图、
    video 的帧、本地文件……），本工具不感知来源。媒体类型以魔数嗅探
    为准，声明仅为兑底。
    """
    s = image.strip()
    declared = ""
    if s.startswith(("http://", "https://")):
        _assert_public_http(s)
        data, declared = _http_get_image(s, max_bytes)
    elif s.startswith("data:"):
        try:
            head, _, payload = s.partition(",")
            meta = head[5:].split(";", 1)
            declared = meta[0].strip().lower()
            if len(meta) > 1 and meta[1].strip().lower() == "base64":
                data = base64.b64decode(payload)
            else:
                data = urllib.parse.unquote_to_bytes(payload)
        except Exception as e:  # noqa: BLE001
            raise _Reject(f"data URI 解析失败：{e}") from e
        if len(data) > max_bytes:
            raise _Reject(f"图片超过大小上限（{max_bytes // 1_048_576}MB）")
    elif s.startswith("base64://"):
        try:
            data = base64.b64decode(s[9:])
        except Exception as e:  # noqa: BLE001
            raise _Reject(f"base64 解码失败：{e}") from e
        if len(data) > max_bytes:
            raise _Reject(f"图片超过大小上限（{max_bytes // 1_048_576}MB）")
    else:
        try:
            data = Path(s).expanduser().read_bytes()
        except OSError as e:
            raise _Reject(f"读取本地图片失败：{e}") from e
        if len(data) > max_bytes:
            raise _Reject(f"图片超过大小上限（{max_bytes // 1_048_576}MB）")
    media = _sniff_image(data) or (declared if declared.startswith("image/") else "")
    if not media:
        raise _Reject("不是可识别的图片（支持 jpg/png/gif/webp/bmp）")
    return data, media


def make_look_image_tool(llm, store: Store, cfg) -> Tool:
    """看图：任何来源的图 → 内容描述（感知记忆：内容 hash 去重 + 原图归档）。

    llm 需提供 vision(text, image_b64, media_type)（OpenAILLM 已具备）。
    装配在 host（llm 与 store 相遇处）。看与不看由心智自决（行动是拉的）。
    """

    async def _fn(args: dict) -> str:
        # 能力门控：vision 模型未配置→不尝试、不归档，直接给心智明确事实
        # （主模型多半纯文本，试了只会得到费解的网关错误）
        if not str(getattr(cfg, "llm_vision_model", "") or "").strip():
            return _err_receipt("看图能力未配置：llm_vision_model 为空。可以告诉主人：在 config.json「模型」组设置支持图像的模型名（llm_vision_model），保存后 /reload 即生效")
        image = str(args.get("image") or "").strip()
        question = str(args.get("question") or "").strip()
        if not image:
            return _err_receipt("需要 image（图片 URL / data:URI / base64:// / 本地路径）")
        max_bytes = int(float(cfg.image_max_mb) * 1_048_576)
        try:
            data, media = await asyncio.to_thread(_resolve_image, image, max_bytes)
        except _Reject as e:
            return _err_receipt(str(e))
        h = hashlib.sha256(data).hexdigest()
        row = store.find_image(h)
        # 缓存命中（不问新问题）：零 vision 成本，直接回描述
        if row and row["description"] and not question:
            store.touch_image(h)
            seen = clock.format_rel(row["first_seen"], tz_name=cfg.timezone)
            return _ok_receipt(detail=row["description"], hash=h, note=f"这张图你 {seen} 看过，直接回缓存描述（未重复识别）")
        # 归档原图（一次即档：之后 URL 过期也能看；文件丢失则补写）
        fname = f"{h}.{_MEDIA_EXT.get(media, 'img')}"
        fpath = Path(cfg.data_dir) / "images" / fname
        if not fpath.is_file():

            def _save() -> None:
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_bytes(data)

            await asyncio.to_thread(_save)
        store.record_image(h, fname, len(data), "")
        q = question or "客观、简洁地描述这张图片的内容（人物/场景/文字/情绪）"
        try:
            resp = await llm.vision(q, base64.b64encode(data).decode("ascii"), media)
        except Exception as e:  # noqa: BLE001
            return _err_receipt(f"看图失败：{e}")
        answer = (resp.content or "").strip()[:_DESC_MAX]
        if not answer:
            return _err_receipt("看图失败：模型未返回内容")
        if question:
            store.touch_image(h)  # 带问题的回答不覆盖通用描述（特定问答不缓存）
        else:
            store.record_image(h, fname, len(data), answer)
        return _ok_receipt(detail=answer, hash=h, note="已归档原图与描述：同图再看（不问新问题）直接回缓存")

    return Tool(
        schema={
            "name": "look_image",
            "description": "看一张图，返回内容的描述；可带 question 提具体问题（图里写了什么/这是什么梗）。输入支持图片 URL、data:URI、base64://、本地路径——任何来源的图都能看。同一张图（内容相同）只识别一次，之后直接回缓存；原图自动归档，URL 过期也能回看。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "description": "图片：URL / data:URI / base64:// 前缀 / 本地路径"},
                    "question": {"type": "string", "description": "想问这张图什么（不填=通用描述）"},
                },
                "required": ["image"],
            },
        },
        func=_fn,
        interruptible=False,
    )
