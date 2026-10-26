"""命令面：/命令 固定规则应答（身体反射，不经心智）。

与信封（感知面，进心智）/ 工具（表达面，心智调用）正交——这是主人对
身体的直接查询接口：确定性、即时、零 token。仅主人私聊生效；
群聊/非主人的 /xxx 不响应不拦截，走正常信封路由。

纪律：
- 未知命令拦截并提示可用清单（不进心智）
- 命令执行异常转错误回执，绝不抛错
- 应答出站走同一风控钳制（有界等待：最小间隔等得过去，每分钟硬上限
  放弃）；命令交互与应答均入背景听觉（心智翻档案能看全，但不被打扰）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...utils import clock
from .chatlog import format_ts
from .protocol import ParsedEvent

if TYPE_CHECKING:
    from . import QQPlugin

log = logging.getLogger("plugins.qq.commands")

CommandFn = Callable[["QQPlugin", ParsedEvent, str], Awaitable[str]]
_COMMANDS: dict[str, CommandFn] = {}


# ── 工具：HTTP/JSON 回环调用面板 ──────────────
def _http_get_json(url: str, timeout: float = 2.0) -> Any:
    """同进程回环调用面板 API（阻塞，供 to_thread 包装）。失败返回 None。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


async def _fetch(p: QQPlugin, path: str) -> Any:
    base = f"http://127.0.0.1:{p._cfg.server_port}"
    token = urllib.parse.quote(p._cfg.server_token or "")
    sep = "&" if "?" in path else "?"
    return await asyncio.to_thread(_http_get_json, f"{base}{path}{sep}token={token}")


def _fmt_uptime(s: int) -> str:
    if s < 60:
        return f"{s} 秒"
    if s < 3600:
        return f"{s // 60} 分钟"
    if s < 86400:
        return f"{s // 3600} 小时 {s % 3600 // 60} 分"
    return f"{s // 86400} 天 {s % 86400 // 3600} 小时"


def _fmt_tokens(n: int) -> str:
    return f"{n / 10000:.1f} 万" if n >= 10000 else str(n)


def _register_command(func: CommandFn) -> CommandFn:
    """命令注册装饰器：函数名即命令名（去下划线前缀，补 /）。"""
    _COMMANDS[f"/{func.__name__.lstrip('_')}"] = func
    return func


# ── /status：状态输出 ──────────────
@_register_command
async def _status(p: QQPlugin, ev: ParsedEvent, arg: str) -> str:
    """/status：面板 API（版本/运行时长/心智/工具/今日用量）+ 身体本地
    事实（NapCat 连接/未读/发送窗口）。面板未运行时降级为本地字段。"""
    status, usage = await asyncio.gather(_fetch(p, "/api/status"), _fetch(p, "/api/usage"))
    conn = "正常" if p._client is not None and p._client.connected else "断开"
    lines: list[str] = []
    if isinstance(status, dict) and status.get("ok"):
        lines.append(f"lifemate {status.get('version', '?')} · 运行 {_fmt_uptime(int(status.get('uptime_s') or 0))}")
        busy = "思考中" if status.get("busy") else "休息中"
        lines.append(f"连接 {conn} · 心智 {busy} · 会话 {status.get('session_id', '?')}")
        extra = f" · 插件 {'/'.join(status.get('plugins') or [])}" if status.get("plugins") else ""
        lines.append(f"模型 {status.get('model') or '?'} · 工具 {status.get('tool_count', '?')} 个{extra}")
    else:
        lines.append(f"连接 {conn}（面板服务未运行，心智/用量字段缺）")
    if isinstance(usage, dict) and usage.get("by_model"):
        calls = sum(m.get("calls", 0) for m in usage["by_model"].values())
        tokens = sum(m.get("tokens_in", 0) + m.get("tokens_out", 0) for m in usage["by_model"].values())
        lines.append(f"今日 {_fmt_tokens(tokens)} token · {calls} 次调用")
    if p._chatlog is not None:
        scan = p._chatlog._unread_scan(p._conf.bot_uin)
        if scan:
            top = sorted(scan.items(), key=lambda x: -x[1][0])[:3]
            parts = [f"{cid.replace('group_', '群').replace('private_', '私聊')} {st[0]}" for cid, st in top]
            lines.append("未读：" + "、".join(parts))
    now = clock.now(p._cfg.timezone)
    tz = p._cfg.timezone or "本机时区"
    sent = len([t for t in p._send_times if time.monotonic() - t < 60])
    lines.append(f"现在 {now:%H:%M}（{tz}）· 本分钟发送 {sent}/{p._conf.send_max_per_min}")
    return "\n".join(lines)


# ── /history：心智最近活动（observe 档案尾部） ──────────
def _oneline(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def _fmt_record(r: dict, tz: str) -> str:
    """档案记录 → 一行摘要（时间 + 图标 + 事实头）。"""
    ts = r.get("ts") or 0
    head = f"[{format_ts(ts, tz_name=tz) if ts else '?'}] "
    typ, role = r.get("type"), r.get("role")
    if typ == "msg":
        if role == "assistant":
            tcs = r.get("tool_calls") or []
            if tcs:
                calls = ", ".join(f"{tc.get('name', '?')}({_oneline(str(tc.get('arguments', '')), 30)})" for tc in tcs[:3])
                extra = f" …共{len(tcs)}个" if len(tcs) > 3 else ""
                body = f"🔧 {calls}{extra}"
            else:
                body = "💬 " + _oneline(r.get("content") or "", 60)
        elif role == "user":
            body = "📨 " + _oneline(r.get("content") or "", 60)
        elif role == "tool":
            body = f"📥 {r.get('name', '?')} {'✓' if r.get('ok') else '✗'} " + _oneline(str(r.get("content") or ""), 40)
        else:
            body = f"{role} " + _oneline(str(r.get("content") or ""), 40)
    elif typ == "rest":
        body = f"🌙 休息 {_oneline(str(r.get('reason', '')), 40)}"
    elif typ == "wake":
        body = f"☀️ 唤醒 {r.get('trigger', '')}"
    elif typ == "error":
        body = f"✗ {r.get('error', '')}"
    elif typ == "compact":
        body = f"🗜️ 压缩 丢{r.get('dropped', 0)} 保{r.get('kept', 0)}"
    else:
        body = f"· {typ}"
    return head + body


def _observe_tail(p: QQPlugin, n: int) -> list[dict] | None:
    """面板未运行时的兑底：直读最新日档尾部 N 行（坏行跳过）。"""
    files = sorted((Path(p._cfg.data_dir) / "observe").glob("*.jsonl"))
    if not files:
        return None
    try:
        lines = files[-1].read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    rows = []
    for raw in lines[-n:]:
        try:
            rows.append(json.loads(raw))
        except ValueError:
            continue
    return rows


@_register_command
async def _history(p: QQPlugin, ev: ParsedEvent, arg: str) -> str:
    """/history [条数]：心智最近活动（observe 档案尾部）——输出/工具调用/
    收到输入/休息/唤醒/错误各一行。默认 10 条，上限 50。
    面板 API 优先，未运行时直读日档兑底。"""
    n = 10
    if arg.strip():
        if arg.strip().isdigit():
            n = max(1, min(int(arg.strip()), 50))
        else:
            return f"看不懂参数：{arg}\n用法：/history [条数]"
    data = await _fetch(p, f"/api/history?tail={n}")
    rows = data.get("rows") if isinstance(data, dict) and data.get("ok") else None
    if not rows:
        rows = _observe_tail(p, n)  # 面板未运行：直读档案
    if not rows:
        return "没有活动记录（无档案且面板未运行）"
    tz = p._cfg.timezone
    return f"最近 {len(rows)} 条活动：\n" + "\n".join(_fmt_record(r, tz) for r in rows)


@_register_command
async def _reload(p: QQPlugin, ev: ParsedEvent, arg: str) -> str:
    """/reload：重读 config.json（与控制台 /reload 同义）——原地热更新，
    全部活引用即时生效；应答列出本次变化的键，标注需重启才生效的项。"""
    try:
        changed = p._cfg.reload()
    except Exception:  # noqa: BLE001
        log.exception("配置重载失败")
        return "配置重载出错（已记日志）"
    if not changed:
        return "配置无变化（config.json 未改动）"
    fields = p._cfg.schema_dict()["fields"]
    restart_keys = [k for k in changed if k != "plugins" and fields.get(k, {}).get("restart")]
    lines = [f"已重载，变化 {len(changed)} 键：{'、'.join(changed)}"]
    if "plugins" in changed:
        lines.append("插件配置已更新（部分配置需重启进程生效）")
    if restart_keys:
        lines.append(f"部分配置需重启生效：{'、'.join(restart_keys)}")
    return "\n".join(lines)


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
        avail = "\n".join(sorted(_COMMANDS)) or "（暂无）"
        await _reply(p, ev, f"找不到 {cmd}\n可用以下\n{avail}")
        return
    try:
        reply = await fn(p, ev, arg.strip())
    except Exception:  # noqa: BLE001
        log.exception("命令 %s 执行异常", cmd)
        reply = f"命令 {cmd} 执行出错（已记日志）"
    if reply:
        await _reply(p, ev, reply)


async def _reply(p: QQPlugin, ev: ParsedEvent, text: str) -> None:
    """命令应答出站：风控钳制下有界等待——最小间隔等得过去（查询接口
    不沉默），每分钟硬上限放弃（几乎只可能是异常循环）；正式发送仍经
    _throttle 记账，回执入档。失败只留痕不抛错。"""
    for _ in range(3):
        now = time.monotonic()
        window = [t for t in p._send_times if now - t < 60]
        if len(window) >= p._conf.send_max_per_min:
            log.warning("命令应答撞每分钟发送上限（丢弃）")
            return
        if window and (gap := p._conf.send_min_interval_s - (now - window[-1])) > 0:
            await asyncio.sleep(gap + 0.05)
            continue
        break
    blocked = p._throttle()  # 记账（时序上应已放行）
    if blocked:
        log.warning("命令应答被风控钳制（丢弃）：%s", blocked)
        return
    try:
        data = await p._call("send_private_msg", {"user_id": ev.sender_id, "message": [{"type": "text", "data": {"text": text}}]})
        p._log_sent(ev.chat_id, text, int(data.get("message_id") or 0))
    except Exception:  # noqa: BLE001
        log.exception("命令应答发送失败")
