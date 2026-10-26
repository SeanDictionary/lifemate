"""看图（look_image）单测：感知记忆去重 + 四形态输入归一 + 回执制。

FakeVisionLLM 记录 vision 调用次数——缓存命中断言的核心是"不重复识别"。
"""

import base64
import json
from pathlib import Path

import pytest
from conftest import make_config

from lifemate.kernel import web as kw
from lifemate.kernel.contracts import LLMResponse
from lifemate.kernel.tools import _resolve_image, make_look_image_tool  # noqa: F401 供 SSRF 用例
from lifemate.memory.store import Store

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # 可嗅探的最小 PNG 骨架


class FakeVisionLLM:
    """vision 调用全部返回固定描述，计数供缓存断言。"""

    def __init__(self) -> None:
        self.vision_calls: list[tuple[str, str]] = []  # (text, media_type)

    async def vision(self, text: str, image_b64: str, media_type: str = "image/jpeg") -> LLMResponse:
        self.vision_calls.append((text, media_type))
        return LLMResponse(content=f"一张测试图（{media_type}）")


def _setup(tmp_path, vision_model: str = "fake-vision"):
    cfg = make_config(tmp_path)
    cfg.llm_vision_model = vision_model
    store = Store(tmp_path / "memory.db")
    llm = FakeVisionLLM()
    tool = make_look_image_tool(llm, store, cfg)
    return cfg, store, llm, tool


async def _call(tool, **args) -> dict:
    return json.loads(await tool.func(args))


def _write_png(path: Path, data: bytes = PNG_MAGIC) -> Path:
    path.write_bytes(data)
    return path


async def test_look_image_unconfigured_clear_receipt(tmp_path):
    """vision 模型未配置：不尝试、不归档，回执明确告知（可转告主人）。"""
    cfg, store, llm, tool = _setup(tmp_path, vision_model="")
    p = _write_png(tmp_path / "a.png")
    r = await _call(tool, image=str(p))
    assert r["ok"] is False and "未配置" in r["error"] and "llm_vision_model" in r["error"]
    assert len(llm.vision_calls) == 0
    assert store.stats()["images"] == 0  # 未归档：看不了的图不留档
    assert not (Path(cfg.data_dir) / "images").exists()


async def test_look_image_describe_and_cache(tmp_path):
    cfg, store, llm, tool = _setup(tmp_path)
    p = _write_png(tmp_path / "a.png")
    r = await _call(tool, image=str(p))
    assert r["ok"] is True and "测试图" in r["detail"]
    assert len(llm.vision_calls) == 1
    # 原图已归档、描述已入库
    row = store.find_image(__import__("hashlib").sha256(PNG_MAGIC).hexdigest())
    assert row is not None and "测试图" in row["description"]
    assert (Path(cfg.data_dir) / "images" / row["path"]).is_file()
    # 同内容不同路径：缓存命中，不再识别
    p2 = _write_png(tmp_path / "b.png")
    r2 = await _call(tool, image=str(p2))
    assert r2["ok"] is True and "看过" in r2["note"]
    assert len(llm.vision_calls) == 1
    assert store.find_image(row["hash"])["hit_count"] == 1


async def test_look_image_question_reruns_vision(tmp_path):
    _, store, llm, tool = _setup(tmp_path)
    p = _write_png(tmp_path / "a.png")
    await _call(tool, image=str(p))  # 通用描述一次
    r = await _call(tool, image=str(p), question="图里有几个字？")
    assert r["ok"] is True
    assert len(llm.vision_calls) == 2
    assert "几个字" in llm.vision_calls[1][0]
    # 已有通用描述：细节问答不覆盖
    row = store.find_image(__import__("hashlib").sha256(PNG_MAGIC).hexdigest())
    assert row["description"].startswith("一张测试图")


async def test_look_image_question_seeds_description(tmp_path):
    """带问题看新图：问答本身就是解释——存为首个描述，之后重看走缓存零 vision。"""
    _, store, llm, tool = _setup(tmp_path)
    p = _write_png(tmp_path / "a.png")
    r = await _call(tool, image=str(p), question="这是什么梗？")
    assert r["ok"] is True and "梗" in llm.vision_calls[0][0]
    h = __import__("hashlib").sha256(PNG_MAGIC).hexdigest()
    assert "测试图" in store.find_image(h)["description"]
    # 再不带问题看：缓存命中，不再调 vision
    r2 = await _call(tool, image=str(p))
    assert r2["ok"] is True and "看过" in r2["note"]
    assert len(llm.vision_calls) == 1


async def test_look_image_input_forms(tmp_path):
    _, store, llm, tool = _setup(tmp_path)
    # data URI
    r = await _call(tool, image="data:image/png;base64," + base64.b64encode(PNG_MAGIC).decode())
    assert r["ok"] is True
    # base64:// 前缀（同内容 → 缓存命中，vision 仍只一次）
    r2 = await _call(tool, image="base64://" + base64.b64encode(PNG_MAGIC).decode())
    assert r2["ok"] is True and "看过" in r2["note"]
    assert len(llm.vision_calls) == 1
    # 本地路径
    p = _write_png(tmp_path / "c.png")
    r3 = await _call(tool, image=str(p))
    assert r3["ok"] is True
    # 全部归一到同一 hash
    h = __import__("hashlib").sha256(PNG_MAGIC).hexdigest()
    assert store.find_image(h)["hit_count"] >= 1


async def test_look_image_bad_inputs(tmp_path):
    _, store, llm, tool = _setup(tmp_path)
    # 不存在
    r = await _call(tool, image=str(tmp_path / "nope.png"))
    assert r["ok"] is False and "失败" in r["error"]
    # 非图片内容
    p = tmp_path / "text.png"
    p.write_bytes(b"hello world, not an image")
    r2 = await _call(tool, image=str(p))
    assert r2["ok"] is False and "不是可识别的图片" in r2["error"]
    # 空 image
    r3 = await _call(tool, image="")
    assert r3["ok"] is False
    assert len(llm.vision_calls) == 0


async def test_look_image_ssrf_rejected():
    # 内网/回环地址拒绝（解析在连接前，零网络）
    for url in ("http://127.0.0.1/x.png", "http://localhost/x.png", "http://192.168.1.1/x.png", "http://[::1]/x.png", "ftp://example.com/x.png"):
        with pytest.raises(Exception, match="公网|http/https|失败"):
            _resolve_image(url, 1024)


def test_allow_cidrs_fake_ip_exemption():
    """本机代理 TUN/fake-ip 豁免：Clash 默认 198.18.0.0/16 解析成保留段。"""
    # 未豁免：fake-ip 段被拒
    with pytest.raises(Exception, match="公网"):
        kw.assert_public_http("http://198.18.0.5/x.png", [])
    # 坏条目告警跳过、空条目忽略
    nets = kw.parse_allow_cidrs("198.18.0.0/16, not-a-cidr,,  ")
    assert len(nets) == 1
    # 豁免后通过检查（纯检查零连接）
    kw.assert_public_http("http://198.18.0.5/x.png", nets)
    # 豁免不放松其他私网
    with pytest.raises(Exception, match="公网"):
        kw.assert_public_http("http://127.0.0.1/x.png", nets)
    with pytest.raises(Exception, match="公网"):
        kw.assert_public_http("http://192.168.1.1/x.png", nets)
    # 空配置 → 空豁免
    assert kw.parse_allow_cidrs("") == []
