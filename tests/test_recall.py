"""档案回查（recall）单测：三级保真 + 任意访问（翻页/日期/搜索）+ 预算兜底。

档案夹具手写 jsonl（坏行/跨天/长字段都覆盖）；零依赖 Recorder 真写。
"""

import json
import time
from pathlib import Path

from conftest import make_config

from lifemate.kernel.tools import make_recall_tool


def _row(seq: int, rid: str, **kw) -> dict:
    """档案行骨架（recorder 契约字段）。"""
    row = {"type": "msg", "ts": kw.pop("ts", time.time()), "seq": seq, "sid": "s1", "rid": rid}
    row.update(kw)
    return row


def _setup(tmp_path, days: dict[str, list[dict]]):
    cfg = make_config(tmp_path)
    obs = Path(cfg.data_dir) / "observe"
    obs.mkdir(parents=True, exist_ok=True)
    for date, rows in days.items():
        lines = [json.dumps(r, ensure_ascii=False) for r in rows]
        if date.endswith("+bad"):  # 注入坏行（半写状态）
            lines.insert(1, "{broken json")
            date = date[: -len("+bad")]
        (obs / f"{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg, make_recall_tool(cfg)


TODAY = time.strftime("%Y-%m-%d")
YDAY = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


async def _call(tool, **args) -> dict:
    return json.loads(await tool.func(args))


def _mk_days() -> dict:
    return {
        TODAY: [
            _row(1, "aaaaaa", role="user", content="[终端·主人] 对了，我有点好奇，DeepSeek 新模型？", env={"origin": "console", "kind": "direct"}),
            _row(2, "aaaaaa", role="assistant", thinking="我的知识可能不新，应该搜一下。", tool_calls=[{"name": "web_search", "arguments": '{"query": "DeepSeek"}'}]),
            _row(3, "aaaaaa", role="tool", name="web_search", ok=False, content='{"ok": false, "error": "web_search_api_key 未配置"}'),
            _row(4, "aaaaaa", role="assistant", content="想查来着，结果我这边用不了网。"),
            _row(5, "bbbbbb", type="rest", reason="converged"),
        ],
        YDAY: [
            _row(1, "cccccc", role="user", content="昨天聊过鲸鱼的话题", env={"origin": "qq", "kind": "ambient"}),
            _row(2, "cccccc", role="assistant", content="鲸鱼确实是挺好的"),
            _row(3, "dddddd", type="wake", trigger="reminder"),
        ],
    }


async def test_recall_overview_default_and_handles(tmp_path):
    """默认概览：最近活动、一行一条、#seq·rid6 句柄、时间标注。"""
    _, tool = _setup(tmp_path, _mk_days())
    r = await _call(tool)
    assert r["ok"] is True and r["count"] == 8  # 跨天穿透（今天 5 + 昨天 3）
    d = r["detail"]
    assert "#1·aaaaaa" in d and "#5·bbbbbb" in d
    assert "对了，我有点好奇" in d and "🔧 web_search" in d
    assert "🌙 休息 converged" in d
    assert r["returned"] <= 20


async def test_recall_overview_paging_and_date(tmp_path):
    _, tool = _setup(tmp_path, _mk_days())
    # 翻页：跳过最新 4 条（t5~t2）→ 窗口 = [今天#1, 昨天#3, 昨天#2]（旧→新呈现）
    r = await _call(tool, offset=4, n=3)
    assert r["ok"] is True and r["returned"] == 3
    assert "#1·aaaaaa" in r["detail"] and "#2·cccccc" in r["detail"]
    assert "#5·" not in r["detail"]  # 最新 4 条确实被跳过
    # 指定日期
    r2 = await _call(tool, date=YDAY)
    assert r2["ok"] is True and "鲸鱼" in r2["detail"]
    # 不存在的日期 → 明确回执
    r3 = await _call(tool, date="2020-01-01")
    assert r3["ok"] is False and "没有档案" in r3["error"]
    # 坏日期格式
    r4 = await _call(tool, date="昨天")
    assert r4["ok"] is False and "YYYY-MM-DD" in r4["error"]


async def test_recall_search(tmp_path):
    """关键词搜索：全档案、大小写不敏感、坏行跳过、命中计数。"""
    days = _mk_days()
    days[TODAY + "+bad"] = days.pop(TODAY)  # 注入坏行
    _, tool = _setup(tmp_path, days)
    r = await _call(tool, q="鲸鱼")
    assert r["ok"] is True and r["count"] == 2  # 昨天的 user+assistant
    assert "#1·cccccc" in r["detail"]
    r2 = await _call(tool, q="不存在的话题xyz")
    assert r2["ok"] is True and "没有匹配" in r2["detail"]


async def test_recall_rid_detail(tmp_path):
    """链路详情：全文回放含思考与完整回执、不可信声明前置。"""
    _, tool = _setup(tmp_path, _mk_days())
    r = await _call(tool, rid="aaaaaa")
    assert r["ok"] is True and r["count"] == 4
    d = r["detail"]
    assert d.splitlines()[0].startswith("（以下为档案原文回放")
    assert "（思考）我的知识可能不新" in d
    assert "想查来着，结果我这边用不了网。" in d
    assert '"ok": false' in d  # 工具回执逐字
    # rid 前缀（4 位）
    r2 = await _call(tool, rid="cccc")
    assert r2["ok"] is True and r2["count"] == 2
    # 太短的 rid
    r3 = await _call(tool, rid="ab")
    assert r3["ok"] is False
    # 不存在的 rid
    r4 = await _call(tool, rid="zzzzzz")
    assert r4["ok"] is False and "没有 rid 前缀" in r4["error"]


async def test_recall_seq_full_row(tmp_path):
    """单行全文：凭 #seq 句柄取完整原文（长字段不截）。"""
    days = _mk_days()
    long_thinking = "我在想" * 5000  # 远超 row_cap（15000 字）
    days[TODAY].append(_row(6, "eeeeee", role="assistant", thinking=long_thinking))
    cfg, tool = _setup(tmp_path, days)
    # 短行：完整原文
    r = await _call(tool, date=TODAY, seq=4)
    assert r["ok"] is True and r["truncated"] is False
    assert "想查来着，结果我这边用不了网。" in r["detail"]
    # 长行：row_cap 预算截断
    r2 = await _call(tool, date=TODAY, seq=6)
    assert r2["ok"] is True and r2["truncated"] is True
    assert len(r2["detail"]) < 12150
    # seq 不带 date
    r3 = await _call(tool, seq=4)
    assert r3["ok"] is False and "date" in r3["error"]
    # 不存在的 seq
    r4 = await _call(tool, date=TODAY, seq=99)
    assert r4["ok"] is False and "#99" in r4["error"]


async def test_recall_detail_budget_marks_seq(tmp_path):
    """链路详情超预算：截断并指路 seq 句柄。"""
    days = {TODAY: [_row(1, "ffffaa", role="assistant", thinking="深" * 8000)]}
    _, tool = _setup(tmp_path, days)
    r = await _call(tool, rid="ffffaa")
    assert r["ok"] is True and r["truncated"] is True
    assert "#seq 句柄" in r["detail"] and "已截断" in r["detail"]


async def test_recall_empty_and_n_cap(tmp_path):
    """空档案 → 明确回执；n 超上限被钳。"""
    _, tool = _setup(tmp_path, {})
    r = await _call(tool)
    assert r["ok"] is True and "还没有任何档案记录" in r["detail"]
    cfg = make_config(tmp_path)
    cfg.recall_tail_max = 2
    tool2 = make_recall_tool(cfg)
    _setup(tmp_path, _mk_days())
    r2 = await _call(tool2, n=99)
    assert r2["ok"] is True and r2["returned"] == 2
