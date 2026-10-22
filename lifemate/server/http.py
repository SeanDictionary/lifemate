"""极简 HTTP 基建：请求解析/路由分发/响应构造/静态托管（零第三方依赖）。

手写 HTTP/1.1 子集（asyncio 流协议）：
- 请求：请求行 + headers + Content-Length body（上限 1MB）
- 连接：普通请求 Connection: close；SSE 由调用方特判接管（唯一流式端点）
- 路由：(method, pattern, handler) 有序匹配；pattern 支持 {name} 段
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

MAX_BODY = 1024 * 1024

Handler = Callable[["Request", dict[str, str]], Awaitable[Any]]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes = b""

    def json(self) -> dict:
        """body 解析为 dict（坏 JSON/非对象 → 空 dict，由各端点自行回执）。"""
        try:
            data = json.loads(self.body.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}


@dataclass
class Route:
    method: str
    pattern: str  # 如 "/api/memory/{table}"
    handler: Handler

    def match(self, method: str, path: str) -> dict[str, str] | None:
        if self.method != method:
            return None
        ps = self.pattern.strip("/").split("/")
        ts = path.strip("/").split("/")
        if len(ps) != len(ts):
            return None
        params: dict[str, str] = {}
        for p, t in zip(ps, ts, strict=True):
            if p.startswith("{") and p.endswith("}"):
                params[p[1:-1]] = t
            elif p != t:
                return None
        return params


def parse_query(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in parse_qs(raw, keep_blank_values=True).items():
        out[k] = v[0]
    return out


async def read_request(reader: asyncio.StreamReader) -> Request | None:
    """读一个完整请求头+体。解析失败/连接断开 → None。"""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError):
        return None
    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, _proto = lines[0].split(" ", 2)
    except ValueError:
        return None
    headers: dict[str, str] = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    clen = int(headers.get("content-length") or 0)
    if clen > MAX_BODY:
        return None
    body = b""
    if clen > 0:
        try:
            body = await reader.readexactly(clen)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
    path, _, qraw = target.partition("?")
    return Request(method=method.upper(), path=path, query=parse_query(qraw), headers=headers, body=body)


_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}


def resp_bytes(status: int, ctype: str, body: bytes, extra: dict[str, str] | None = None) -> bytes:
    head = f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
    for k, v in (extra or {}).items():
        head += f"{k}: {v}\r\n"
    return (head + "\r\n").encode("latin-1") + body


def json_resp(obj: Any, status: int = 200) -> bytes:
    return resp_bytes(status, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8"))


_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
    ".woff2": "font/woff2",
    ".webp": "image/webp",
}


def static_resp(root, rel: str) -> bytes:
    """受控静态文件：防穿越；解析顺序 文件 → 路径/index.html → 路径.html
    → Next 预取翻译（__next.<seg>.__PAGE__.txt → __next.<seg>/__PAGE__.txt）。

    Next 静态导出的资源带内容哈希，_next/static/ 发 immutable 缓存头。
    """
    rel = rel.lstrip("/")
    if not rel:
        rel = "index.html"
    base = (root / rel).resolve()
    if not str(base).startswith(str(root.resolve())):
        return json_resp({"ok": False, "error": "forbidden"}, 403)
    cands = [base]
    if base.suffix == "":
        cands.append(base / "index.html")
        cands.append(base.with_name(base.name + ".html"))
    if "__next." in rel:  # RSC 预取 URL 的点号 → 文件系统斜杠
        translated = re.sub(r"\.([^.]+)\.txt$", r"/\1.txt", rel)
        cands.append((root / translated).resolve())
    for c in cands:
        if c.is_file():
            ctype = _MIME.get(c.suffix.lower(), "application/octet-stream")
            if rel.startswith("_next/static/"):
                # 内容哈希资源：永久缓存
                extra = {"Cache-Control": "public, max-age=31536000, immutable"}
            elif ctype.startswith("text/html"):
                # HTML 引用哈希资源：必须每次回源，否则重建后用户永远拿旧页面
                extra = {"Cache-Control": "no-cache"}
            else:
                extra = None
            return resp_bytes(200, ctype, c.read_bytes(), extra)
    return resp_bytes(404, "text/plain; charset=utf-8", b"not found")


def no_build_page() -> bytes:
    """静态目录缺失时的提示页（API 不受影响）。"""
    html = "<!doctype html><meta charset='utf-8'><title>lifemate</title><body style='font-family:system-ui;padding:2rem;line-height:1.8'><h2>前端未构建</h2><p>在 web/ 目录执行 <code>npm run build</code> 后刷新本页。API 已就绪：<a href='/api/status'>/api/status</a></p></body>"
    return resp_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
