"""命令面：/命令 固定规则应答（身体反射，不经心智）。

与信封（感知面，进心智）/ 工具（表达面，心智调用）正交——这是主人对
身体的直接查询接口：确定性、即时、零 token。仅主人私聊生效；
群聊/非主人的 /xxx 不响应不拦截，走正常信封路由。

纪律：
- 未知命令拦截并提示可用清单（不进心智）
- 命令执行异常转错误回执，绝不抛错
- 应答走同一风控钳制；命令交互与应答均入背景听觉（心智翻档案能看全，
  但不被打扰——与私聊消息一致的未读语义）
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .protocol import ParsedEvent

if TYPE_CHECKING:
    from . import QQPlugin

log = logging.getLogger("plugins.qq.commands")

# 命令表：/命令名 → 处理函数。返回回复文本；空串 = 不回。
# 填充示例：
#   async def _status(p: QQPlugin, ev: ParsedEvent, arg: str) -> str:
#       return f"连接 {p._client.connected if p._client else False}"
#   _COMMANDS["/status"] = _status
CommandFn = Callable[["QQPlugin", ParsedEvent, str], Awaitable[str]]
_COMMANDS: dict[str, CommandFn] = {}


async def handle_command(p: QQPlugin, ev: ParsedEvent) -> None:
    """命令分发（调用方已保证：主人私聊 + / 开头）。永不抛错。"""
    cmd, _, arg = ev.text.strip().partition(" ")
    # 命令交互入档（对称：应答也经 _log_sent 入档）
    if p._chatlog is not None:
        try:
            p._chatlog.append(ev.chat_id, ev.sender_id, ev.sender_name, ev.text, ev.message_id)
        except Exception:  # noqa: BLE001
            log.exception("命令消息入档失败")
    fn = _COMMANDS.get(cmd)
    if fn is None:
        await _reply(p, ev, f"没有这个命令：{cmd}\n可用：{' '.join(sorted(_COMMANDS)) or '（暂无）'}")
        return
    try:
        reply = await fn(p, ev, arg.strip())
    except Exception:  # noqa: BLE001
        log.exception("命令 %s 执行异常", cmd)
        reply = f"命令 {cmd} 执行出错（已记日志）"
    if reply:
        await _reply(p, ev, reply)


async def _reply(p: QQPlugin, ev: ParsedEvent, text: str) -> None:
    """命令应答出站：同一风控钳制 + 回执入档。失败只留痕不抛错。"""
    blocked = p._throttle()
    if blocked:
        log.warning("命令应答被风控钳制（丢弃）：%s", blocked)
        return
    try:
        data = await p._call("send_private_msg", {"user_id": ev.sender_id, "message": [{"type": "text", "data": {"text": text}}]})
        p._log_sent(ev.chat_id, text, int(data.get("message_id") or 0))
    except Exception:  # noqa: BLE001
        log.exception("命令应答发送失败")
