"""网络检索（web_search）单测：门控回执 + provider 映射归一 + 格式化截断。

HTTP 层 monkeypatch _http_post_json——零网络； canned 响应体模拟
Tavily / 博查的真实线格式（含无摘要条目、脏响应体、错误码）。
"""

import json
import urllib.error

from conftest import make_config

from lifemate.kernel import web as kw
from lifemate.kernel.tools import make_web_search_tool


def _setup(tmp_path, provider="tavily", key="sk-test"):
    cfg = make_config(tmp_path)
    cfg.web_search_provider = provider
    cfg.web_search_api_key = key
    tool = make_web_search_tool(cfg)
    return cfg, tool


async def _call(tool, **args) -> dict:
    return json.loads(await tool.func(args))


TAVILY_OK = {
    "query": "test",
    "answer": "  Messi is\nan Argentine\nfootballer.  ",
    "results": [
        {"title": "Messi", "url": "https://a.com/1", "content": "  Lionel Messi,\nborn 1987.  ", "score": 0.99, "published_date": "2026-10-01"},
        {"title": "", "url": "https://a.com/2", "content": "no title entry"},  # 标题缺失用 hostname
        {"title": "无摘要", "url": "https://a.com/3", "content": ""},  # 无摘要丢弃
        {"title": "坏条目", "url": "", "content": "no url"},  # 无 URL 丢弃
        "not-a-dict",  # 非对象跳过
    ],
    "response_time": 1.2,
}

BOCHA_OK = {
    "code": 200,
    "data": {
        "webPages": {
            "value": [
                {"name": "阿里巴巴 ESG", "url": "https://b.com/1", "summary": "  阿里巴巴发布\n2024 年 ESG 报告。  ", "snippet": "搜索摘要兑底", "dateLastCrawled": "2026-10-01T00:00:00Z"},
                {"name": "只有 snippet", "url": "https://b.com/2", "snippet": "搜索摘要", "summary": ""},
                {"name": "全空", "url": "https://b.com/3", "summary": "", "snippet": ""},  # 丢弃
            ]
        }
    },
}


async def test_web_search_unconfigured_clear_receipt(tmp_path):
    """未配置 provider：不尝试、零网络，回执明确告知（可转告主人）。"""
    cfg, tool = _setup(tmp_path, provider="", key="")
    r = await _call(tool, query="今天天气")
    assert r["ok"] is False and "未配置" in r["error"] and "web_search_provider" in r["error"]
    # 认识但缺密钥
    cfg.web_search_provider = "tavily"
    r2 = await _call(tool, query="今天天气")
    assert r2["ok"] is False and "web_search_api_key" in r2["error"]
    # 不认识的服务商
    cfg.web_search_api_key = "k"
    cfg.web_search_provider = "google"
    r3 = await _call(tool, query="今天天气")
    assert r3["ok"] is False
    # 空 query
    cfg.web_search_provider = "tavily"
    r4 = await _call(tool, query="  ")
    assert r4["ok"] is False and "query" in r4["error"]


async def test_web_search_tavily_mapping_and_format(tmp_path, monkeypatch):
    _, tool = _setup(tmp_path)
    calls = []

    def fake_post(url, headers, body):
        calls.append((url, headers, body))
        return TAVILY_OK

    monkeypatch.setattr(kw, "http_post_json", fake_post)
    r = await _call(tool, query="Messi")
    assert r["ok"] is True
    url, headers, body = calls[0]
    assert url == "https://api.tavily.com/search"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["query"] == "Messi" and body["max_results"] == 8 and body["include_answer"] is True
    d = r["detail"]
    # 答案空白归一 + 来源列表 + 防注入前置 + 引用指令
    assert "Messi is an Argentine footballer." in d
    assert d.startswith(kw.WEB_NOTICE)
    assert "[Messi](https://a.com/1)" in d and "born 1987." in d and "（2026-10-01）" in d
    assert "[a.com](https://a.com/2)" in d  # 标题缺失用 hostname
    assert "a.com/3" not in d and "坏条目" not in d  # 无摘要/无 URL 丢弃
    assert "引用来源时用 markdown 链接标注" in d
    assert r["count"] == 2 and r["truncated"] is False


async def test_web_search_bocha_mapping(tmp_path, monkeypatch):
    _, tool = _setup(tmp_path, provider="bocha")
    calls = []

    def fake_post(url, headers, body):
        calls.append((url, headers, body))
        return BOCHA_OK

    monkeypatch.setattr(kw, "http_post_json", fake_post)
    r = await _call(tool, query="阿里巴巴 ESG")
    url, headers, body = calls[0]
    assert url == "https://api.bochaai.com/v1/web-search"
    assert body["count"] == 8 and body["summary"] is True
    d = r["detail"]
    assert "[阿里巴巴 ESG](https://b.com/1)" in d
    assert "阿里巴巴发布 2024 年 ESG 报告。" in d and "搜索摘要兑底" not in d  # summary 优先
    assert "[只有 snippet](https://b.com/2)" in d and "搜索摘要" in d  # snippet 兑底
    assert "b.com/3" not in d
    assert "2026-10-01" in d  # dateLastCrawled 截到日期
    assert r["count"] == 2


async def test_web_search_base_url_override(tmp_path, monkeypatch):
    """自建反代：base_url 拼接各家路径。"""
    cfg, tool = _setup(tmp_path, provider="bocha")
    cfg.web_search_base_url = "https://my-proxy.example.com/"
    seen = []

    def fake_post(url, headers, body):
        seen.append(url)
        return BOCHA_OK

    monkeypatch.setattr(kw, "http_post_json", fake_post)
    await _call(tool, query="x")
    assert seen == ["https://my-proxy.example.com/v1/web-search"]


async def test_web_search_dedup_and_cap(tmp_path, monkeypatch):
    """URL 去重 + 条数上限裁剪（truncated 事实化）。"""
    cfg, tool = _setup(tmp_path)
    cfg.web_search_max_results = 2
    dup = {
        "results": [
            {"title": "A", "url": "https://a.com/1", "content": "第一"},
            {"title": "A 重复", "url": "https://a.com/1", "content": "重复"},
            {"title": "B", "url": "https://b.com/2", "content": "第二"},
            {"title": "C", "url": "https://c.com/3", "content": "第三"},
        ]
    }
    monkeypatch.setattr(kw, "http_post_json", lambda u, h, b: dup)
    r = await _call(tool, query="q")
    assert r["count"] == 2 and r["truncated"] is True
    assert r["detail"].count("a.com/1") == 1  # 去重
    assert "只显示前 2 条" in r["detail"]
    assert "b.com/2" in r["detail"] or "c.com/3" in r["detail"]


async def test_web_search_summary_cap(tmp_path, monkeypatch):
    """单条摘要截断（… 标记 + 空白归一）。"""
    cfg, tool = _setup(tmp_path)
    cfg.web_search_summary_max_chars = 120
    long = "很" * 300
    resp = {"results": [{"title": "长文", "url": "https://a.com/1", "content": long}]}
    monkeypatch.setattr(kw, "http_post_json", lambda u, h, b: resp)
    r = await _call(tool, query="q")
    assert "很" * 119 + "…" in r["detail"]


async def test_web_search_empty_results(tmp_path, monkeypatch):
    monkeypatch.setattr(kw, "http_post_json", lambda u, h, b: {"results": []})
    _, tool = _setup(tmp_path)
    r = await _call(tool, query="不存在的词")
    assert r["ok"] is True and "没有搜到" in r["detail"] and r["count"] == 0


async def test_web_search_provider_errors(tmp_path, monkeypatch):
    """HTTP 错误 / 脏响应体 / 形状异常 → 回执带人话，不抛穿。
    错误由真实 _http_post_json 包装链产生（monkeypatch 到 urlopen 层）。"""
    _, tool = _setup(tmp_path)

    def http_429(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", http_429)
    r = await _call(tool, query="q")
    assert r["ok"] is False and "HTTP 429" in r["error"]

    def dirty_200(url, headers, body):
        return {"unexpected": "网关把上游错误包成 200+对象"}

    monkeypatch.setattr(kw, "http_post_json", dirty_200)
    r2 = await _call(tool, query="q")
    assert r2["ok"] is False and "形状异常" in r2["error"]

    def bocha_code_err(url, headers, body):
        return {"code": 401, "message": "invalid api key"}

    monkeypatch.setattr(kw, "http_post_json", bocha_code_err)
    _, tool2 = _setup(tmp_path, provider="bocha")
    r3 = await _call(tool2, query="q")
    assert r3["ok"] is False and "code=401" in r3["error"]


async def test_web_search_network_failure_receipt(tmp_path, monkeypatch):
    """网络层异常（超时/断网）→ 真实包装链转回执。"""

    class _Resp:
        def __enter__(self):
            raise OSError("connection timed out")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    _, tool = _setup(tmp_path)
    r = await _call(tool, query="q")
    assert r["ok"] is False and "搜索失败" in r["error"] and "timed out" in r["error"]


async def test_web_search_internal_exception_receipt(tmp_path, monkeypatch):
    """provider 映射自身意外异常 → 回执制兑底（不抛穿思维循环）。"""

    def boom(url, headers, body):
        raise RuntimeError("映射代码 bug")

    monkeypatch.setattr(kw, "http_post_json", boom)
    _, tool = _setup(tmp_path)
    r = await _call(tool, query="q")
    assert r["ok"] is False and "内部异常" in r["error"]
