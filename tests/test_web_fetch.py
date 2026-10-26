"""读页（web_fetch）单测：类型分流 + 编码 + 正文提取 + 截断 + SSRF + 回执制。

HTTP 层 monkeypatch web.http_get / urlopen——零网络；canned 响应
模拟真实网页（含 gbk 编码、script/style 噪声、链接渲染）。
"""

import json
import urllib.request

from conftest import make_config

from lifemate.kernel import web as kw
from lifemate.kernel.tools import make_web_fetch_tool


def _setup(tmp_path):
    cfg = make_config(tmp_path)
    tool = make_web_fetch_tool(cfg)
    return cfg, tool


async def _call(tool, **args) -> dict:
    return json.loads(await tool.func(args))


PAGE = """<!DOCTYPE html>
<html><head><title>测试页面</title>
<script>var evil = "不要执行我";</script>
<style>.a { color: red; }</style>
<meta charset="utf-8"></head>
<body>
<nav>首页 标签 关于</nav>
<h1>正文标题</h1>
<p>第一段 &amp; 实体转义。</p>
<p>详情见 <a href="https://example.com/more">这个链接</a>。</p>
<div><span>行内</span>内容保持同行。</div>
<ul><li>列表一</li><li>列表二</li></ul>
<script>alert("x")</script>
</body></html>"""


async def test_web_fetch_html_extraction(tmp_path, monkeypatch):
    """HTML → 标题 + 正文：script/style 剥除、块级换行、链接保留 markdown、实体解码。"""
    _, tool = _setup(tmp_path)
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (PAGE.encode("utf-8"), "text/html"))
    r = await _call(tool, url="https://example.com/a")
    assert r["ok"] is True
    d = r["detail"]
    assert d.startswith(kw.WEB_NOTICE)  # 防注入前置
    assert "# 测试页面" in d
    assert "第一段 & 实体转义" in d
    assert "[这个链接](https://example.com/more)" in d
    assert "evil" not in d and "alert" not in d and "color: red" not in d  # 噪声剥除
    assert "行内内容保持同行" in d.replace("\n", "")  # 行内不换行（空白归一后）
    assert "列表一" in d and "列表二" in d
    assert r["truncated"] is False


async def test_web_fetch_gbk_page(tmp_path, monkeypatch):
    """gbk 编码页：meta 声明 → 正确解码（乱码胜于报错的反面验证）。"""
    _, tool = _setup(tmp_path)
    gbk_html = '<html><head><meta charset="gbk"><title>中文标题</title></head><body><p>这段中文是 gbk 编码的。</p></body></html>'.encode("gbk")
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (gbk_html, "text/html"))
    r = await _call(tool, url="https://example.cn/a")
    assert r["ok"] is True
    assert "这段中文是 gbk 编码的" in r["detail"] and "# 中文标题" in r["detail"]


async def test_web_fetch_plain_text_and_json(tmp_path, monkeypatch):
    """纯文本/JSON 直通（不连 HTML 解析）。"""
    _, tool = _setup(tmp_path)
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": ("第一行\n第二行".encode(), "text/plain"))
    r = await _call(tool, url="https://example.com/t.txt")
    assert r["ok"] is True and "第一行" in r["detail"] and "# " not in r["detail"]
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (b'{"a": 1}', "application/json"))
    r2 = await _call(tool, url="https://example.com/api")
    assert r2["ok"] is True and '{"a": 1}' in r2["detail"]


async def test_web_fetch_content_type_routing(tmp_path, monkeypatch):
    """图片指路 look_image；二进制类型明确不支持。"""
    _, tool = _setup(tmp_path)
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (b"\x89PNG\r\n\x1a\n", "image/png"))
    r = await _call(tool, url="https://example.com/x.png")
    assert r["ok"] is False and "look_image" in r["error"]
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (b"PK\x03\x04zip", "application/zip"))
    r2 = await _call(tool, url="https://example.com/x.zip")
    assert r2["ok"] is False and "不支持" in r2["error"]


async def test_web_fetch_html_sniff_on_bare_ctype(tmp_path, monkeypatch):
    """Content-Type 缺失/谎报但字节是 HTML：按内容嗅探走正文提取。"""
    _, tool = _setup(tmp_path)
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (PAGE.encode("utf-8"), ""))
    r = await _call(tool, url="https://example.com/a")
    assert r["ok"] is True and "# 测试页面" in r["detail"]


async def test_web_fetch_truncation(tmp_path, monkeypatch):
    """超长正文截断（… 标记 + 事实化提示）。"""
    cfg, tool = _setup(tmp_path)
    cfg.web_fetch_max_chars = 200
    long_page = "<html><body><p>" + "长" * 5000 + "</p></body></html>"
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (long_page.encode("utf-8"), "text/html"))
    r = await _call(tool, url="https://example.com/long")
    assert r["ok"] is True and r["truncated"] is True
    assert "只保留了前 200 字符" in r["detail"]
    assert "长" * 199 in r["detail"]


async def test_web_fetch_bad_url_receipts(tmp_path):
    """非法输入 → 回执（不抛穿）。"""
    _, tool = _setup(tmp_path)
    for url in ("", "ftp://x.com", "not-a-url"):
        r = await _call(tool, url=url)
        assert r["ok"] is False


async def test_web_fetch_ssrf(tmp_path, monkeypatch):
    """SSRF（内网拒绝/fake-ip 豁免）走共用网络层（真检查零连接）。"""
    cfg, tool = _setup(tmp_path)
    # 内网：被拒（assert_public_http 在任何网络动作前）
    r = await _call(tool, url="http://192.168.1.1/index.html")
    assert r["ok"] is False and "公网" in r["error"]
    # fake-ip 豁免后可通过 SSRF 检查，进入取回（用假 http_get 兑底）
    cfg.ssrf_exclude_ips = "198.18.0.0/16"
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (PAGE.encode("utf-8"), "text/html"))
    r2 = await _call(tool, url="http://198.18.0.5/index.html")
    assert r2["ok"] is True


async def test_web_fetch_size_cap(tmp_path, monkeypatch):
    """大小上限：真实 http_get 包装链（monkeypatch 到 urlopen 层；
    全网段豁免绕开 SSRF——本机代理 fake-ip 会误拦 example.com）。"""
    cfg, tool = _setup(tmp_path)
    cfg.ssrf_exclude_ips = "0.0.0.0/0,::/0"  # 全豁免绕开 SSRF（本机代理 v4+v6 fake-ip 会误拦）

    class _Big:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        headers = {"Content-Type": "text/html"}

        def read(self, n=-1):
            return b"<html>" + b"x" * (6 * 1_048_576)

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Big())
    r = await _call(tool, url="https://example.com/big")
    assert r["ok"] is False and "大小上限" in r["error"]


async def test_web_fetch_empty_body(tmp_path, monkeypatch):
    """纯脚本渲染页（无正文）→ 明确回执而非空回执。"""
    _, tool = _setup(tmp_path)
    monkeypatch.setattr(kw, "http_get", lambda url, mb, allow=(), ua="": (b"<html><head><script>render()</script></head><body><div id=app></div></body></html>", "text/html"))
    r = await _call(tool, url="https://example.com/spa")
    assert r["ok"] is False and "正文" in r["error"]


def test_html_to_text_units():
    """提取器纯函数单测：裸链接不重复渲染、空锚丢弃、畸形 HTML 不炸。"""
    title, body = kw.html_to_text('<p>看 <a href="https://a.com">https://a.com</a> 和 <a href="https://b.com"></a> 完</p>')
    assert title == ""
    assert "https://a.com" in body and "](https://a.com)" not in body  # 文本即 URL：不重复
    assert "b.com" not in body  # 空锚丢弃
    title2, body2 = kw.html_to_text("<html><title>T</title><body><p>a</p><div><b>broken")
    assert title2 == "T" and "a" in body2 and "broken" in body2  # 未闭合不炸


def test_decode_text_fallbacks():
    """编码探测：头声明 > BOM > meta > utf-8 > gb18030。"""
    assert kw.decode_text("你好".encode(), "text/html; charset=utf-8") == "你好"
    assert kw.decode_text("你好".encode("gb18030"), "text/html") == "你好"  # meta/头都没有 → utf-8 失败 → gb18030
    assert kw.decode_text("你好".encode("utf-16"), "") == "你好"  # BOM
    assert kw.decode_text(bytes([0xEF, 0xBB, 0xBF]) + "你好".encode(), "") == "你好"  # utf-8-sig
    assert kw.decode_text(b"\xff\xfe\x4f\x60", "") != ""  # 乱码也给出结果（replacement 兜底）
