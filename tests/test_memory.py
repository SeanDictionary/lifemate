"""记忆系统单测。"""

import json
import time

from lifemate.memory.store import Store
from lifemate.memory.tools import build_memory_tools


def _store(tmp_path):
    return Store(tmp_path / "memory.db")


def test_episode_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.record_episode("今天开始 v2 重构", kind="life")
    rows = s.recent_episodes(n=5)
    assert len(rows) == 1 and "v2 重构" in rows[0]["summary"]


def test_curiosity_dedup_and_resolve(tmp_path):
    s = _store(tmp_path)
    assert s.add_curiosity("pi 的会话机制") is True
    assert s.add_curiosity("pi 的会话机制") is False  # 7 天去重
    rows = s.pending_curiosity()
    assert len(rows) == 1
    s.resolve_curiosity(rows[0]["id"], "已了解")
    assert s.pending_curiosity() == []


def test_need_dedup(tmp_path):
    s = _store(tmp_path)
    assert s.add_need("想要视觉能力", "想看图片") is True
    assert s.add_need("想要视觉能力", "") is False
    rows = s.pending_needs()
    s.mark_need_raised(rows[0]["id"])
    assert s.pending_needs() == []


def test_reminders_due_and_daily_roll(tmp_path):
    s = _store(tmp_path)
    s.add_reminder(time.time() - 10, "看一眼新消息")  # 已到期
    s.add_reminder(time.time() + 3600, "一小时后写作")  # 未到期
    s.add_reminder(time.time() - 10, "每晚复盘", repeat="daily")  # 到期但滚动
    due = s.due_reminders()
    assert len(due) == 2
    pend = s.pending_reminders()
    # 一次性已标记完成被移出；daily 滚到明天仍在
    assert {r["intent"] for r in pend} == {"一小时后写作", "每晚复盘"}
    assert pend[-1]["due_at"] > time.time()  # daily 已滚到未来


def test_rebuild_packet(tmp_path):
    s = _store(tmp_path)
    assert s.latest_packet() is None
    s.save_packet("我是大肥鱼，正在过 v2 的日子")
    assert "v2" in (s.latest_packet() or {}).get("content", "")


async def test_memory_tools_callable(tmp_path):
    s = _store(tmp_path)
    tools = {t.name: t for t in build_memory_tools(s)}
    assert "record_episode" in tools and "save_rebuild_packet" in tools
    r = await tools["record_episode"].func({"summary": "工具链验证"})
    assert '"ok": true' in r
    r = await tools["recent_episodes"].func({})
    assert "工具链验证" in r
    r = await tools["set_reminder"].func({"delay_minutes": 0.1, "intent": "测试"})
    assert '"ok": true' in r
    r = await tools["latest_rebuild_packet"].func({})
    assert '"ok": false' in r  # 还没有重建包


async def test_memory_tools_readable_times(tmp_path):
    """心智读记忆看到人读时间，不看到裸 epoch。"""
    s = _store(tmp_path)
    s.record_episode("记了一件事")
    s.add_curiosity("好奇的事", "")
    s.add_need("想要个新器官", "")
    s.add_reminder(time.time() + 1800, "半小时后办事")
    by = {t.schema["name"]: t for t in build_memory_tools(s)}

    eps = json.loads(await by["recent_episodes"].func({}))
    assert "time" in eps[0] and "ts" not in eps[0]
    cur = json.loads(await by["list_curiosity"].func({}))
    assert "time" in cur[0]
    needs = json.loads(await by["list_needs"].func({}))
    assert "time" in needs[0]
    rems = json.loads(await by["list_reminders"].func({}))
    assert "due" in rems[0] and "分钟" in rems[0]["due"]  # 半小时后 → 相对时间

    s.save_packet("写给下一个自己的信")
    out = json.loads(await by["latest_rebuild_packet"].func({}))
    assert out["ok"] and "写给下一个自己" in out["content"]
    assert out["written"]  # 写下时刻（人读）


def test_latest_packet_returns_ts(tmp_path):
    s = _store(tmp_path)
    assert s.latest_packet() is None
    before = time.time()
    s.save_packet("v1")
    row = s.latest_packet()
    assert row["content"] == "v1" and row["ts"] >= before


def test_store_image_record_touch_stale(tmp_path):
    s = Store(tmp_path / "memory.db")
    s.record_image("h1", "h1.png", 100, "一只猫")
    row = s.find_image("h1")
    assert row["description"] == "一只猫" and row["hit_count"] == 0
    # 空描述不清盖
    s.record_image("h1", "h1.png", 100, "")
    assert s.find_image("h1")["description"] == "一只猫"
    # 新描述覆盖
    s.record_image("h1", "h1.png", 100, "一只橘猫")
    assert s.find_image("h1")["description"] == "一只橘猫"
    # touch
    s.touch_image("h1")
    assert s.find_image("h1")["hit_count"] == 1
    # stale / forget
    s.record_image("h2", "h2.png", 50, "旧图")
    old = time.time() - 40 * 86400
    s._exec("UPDATE images SET last_seen = ? WHERE hash = 'h2'", (old,))
    assert [r["hash"] for r in s.stale_images(time.time() - 30 * 86400)] == ["h2"]
    s.forget_image("h2")
    assert s.find_image("h2") is None
    assert s.stats()["images"] == 1


# ── 修正链与重开（防固化） ─────────────────────────────


def _tools(tmp_path):
    from conftest import make_config

    return build_memory_tools(_store(tmp_path), make_config(tmp_path))


async def _call(tools, name, **args):
    import json as _json

    tool = next(t for t in tools if t.schema["name"] == name)
    return _json.loads(await tool.func(args))


async def test_empty_list_receipts_give_guidance(tmp_path):
    """空回执给语义：自省时刻递笔，而不是死符号 []。"""
    tools = _tools(tmp_path)
    r = await _call(tools, "list_curiosity")
    assert r["ok"] is True and "兴趣清单是空的" in r["note"] and "add_curiosity" in r["note"]
    r2 = await _call(tools, "list_needs")
    assert r2["ok"] is True and "需求清单是空的" in r2["note"]


async def test_revise_episode_supersedes_and_reverts(tmp_path):
    """修正链：新认识顶替旧认识（旧条退出回忆），revert 回滚恢复。"""
    s = _store(tmp_path)
    old_id = s.record_episode("群里盘问都不该接", kind="insight")
    new_id = s.record_episode("点我了可以接，短句口语即可", kind="insight")
    s.supersede_episode(old_id, new_id)
    # 旧条退出回忆，新条在场
    rows = s.recent_episodes(n=10)
    assert all(r["id"] != old_id for r in rows) and any(r["id"] == new_id for r in rows)
    # 回忆行带 id（修正引用凭据）
    assert all("id" in r for r in rows)
    # 回滚：旧条恢复，修正记录保留为普通记忆
    restored = s.revert_episode(new_id)
    assert restored == old_id
    rows2 = s.recent_episodes(n=10)
    assert any(r["id"] == old_id for r in rows2) and any(r["id"] == new_id for r in rows2)


async def test_revise_and_revert_via_tools(tmp_path):
    tools = _tools(tmp_path)
    s = _store(tmp_path)
    old_id = s.record_episode("旧认识：别接盘问", kind="insight")
    r = await _call(tools, "revise_episode", id=old_id, summary="新认识：点我了再接", why="实测顶回去效果更好")
    assert r["ok"] is True and r["old"]["id"] == old_id and r["new"]["id"] != old_id
    assert "revert_episode" in r["note"]
    # 旧条退出回忆
    assert all(row["id"] != old_id for row in s.recent_episodes(n=10))
    # 回滚
    r2 = await _call(tools, "revert_episode", id=r["new"]["id"])
    assert r2["ok"] is True and r2["restored"] == old_id
    assert any(row["id"] == old_id for row in s.recent_episodes(n=10))
    # 坏 id 回执明确
    r3 = await _call(tools, "revert_episode", id=99999)
    assert r3["ok"] is False


async def test_reopen_curiosity_via_tool(tmp_path):
    tools = _tools(tmp_path)
    s = _store(tmp_path)
    assert s.add_curiosity("猫猫表情包生态") is True
    cid = s.pending_curiosity()[0]["id"]
    await _call(tools, "resolve_curiosity", id=cid, result="看腻了")
    assert s.pending_curiosity() == []
    r = await _call(tools, "reopen_curiosity", id=cid)
    assert r["ok"] is True and s.pending_curiosity()[0]["id"] == cid
    # 非 done 状态重开 → 明确回执
    r2 = await _call(tools, "reopen_curiosity", id=cid)
    assert r2["ok"] is False


def test_curiosity_readd_after_resolve_allowed(tmp_path):
    """去重只算还在清单上的：弄明白过的兴趣可以重新捡起（防固化）。"""
    s = _store(tmp_path)
    assert s.add_curiosity("精酿啤酒") is True
    s.resolve_curiosity(s.pending_curiosity()[0]["id"], "喝过了还行")
    assert s.add_curiosity("精酿啤酒") is True  # resolve 后不算重复
    assert len(s.pending_curiosity()) == 1


def test_all_needs_includes_raised(tmp_path):
    """面板视角：全量需求含已提出（心智工具仍只看待办 pending_needs）。"""
    s = _store(tmp_path)
    s.add_need("read_forward 封装", "手搓费脑")
    s.add_need("另一个需求", "")
    rows = s.pending_needs()
    s.mark_need_raised(rows[0]["id"])
    assert len(s.pending_needs()) == 1  # 心智视角：只剩待提
    all_rows = s.all_needs()
    assert len(all_rows) == 2 and {r["status"] for r in all_rows} == {"filed", "raised"}  # 面板视角：全在


def test_all_reminders_and_curiosity_for_panel(tmp_path):
    """面板视角全量：提醒含已兑现、兴趣含已弄明白（心智仍只看待办/清单）。"""
    import time as _time

    s = _store(tmp_path)
    s.add_reminder(_time.time() - 60, "看一眼群")
    s.add_reminder(_time.time() + 3600, "晚上问主人")
    s.due_reminders(5)  # 到期的兑现 → done
    rows = s.all_reminders()
    assert len(rows) == 2 and {r["status"] for r in rows} == {"done", "pending"}
    assert len(s.pending_reminders()) == 1  # 心智/轮询器视角不变
    assert "done_at" in rows[0]

    s.add_curiosity("精酿啤酒")
    cid = s.pending_curiosity()[0]["id"]
    s.resolve_curiosity(cid, "喝过了还行")
    all_c = s.all_curiosity()
    assert len(all_c) == 1 and all_c[0]["status"] == "done" and "还行" in all_c[0]["result"]
    assert s.pending_curiosity() == []


def test_search_images_keyword_hash_recent(tmp_path):
    s = _store(tmp_path)
    # h1 猫（新）、h2 猫表情包（旧）、h3 截图、h4 无描述
    s.record_image("a" * 64, "a.png", 100, "一只橘猫趴在键盘上")
    s.record_image("b" * 64, "b.png", 200, "猫猫表情包：一只猫在敲键盘")
    s.record_image("c" * 64, "c.png", 300, "聊天截图")
    s.record_image("d" * 64, "d.png", 400, "")
    s._exec("UPDATE images SET last_seen = ? WHERE hash = ?", (time.time() - 3600, "b" * 64))
    s._exec("UPDATE images SET last_seen = ? WHERE hash = ?", (time.time() - 1800, "a" * 64))
    s._exec("UPDATE images SET last_seen = ? WHERE hash = ?", (time.time() - 7200, "c" * 64))

    # 关键词：描述模糊匹配，常用在前（a 比 b 新）
    rows = s.search_images("猫")
    assert [r["hash"] for r in rows] == ["a" * 64, "b" * 64]
    # LIKE 通配符按字面匹配：查询含 % 不误命中
    assert s.search_images("%") == []
    # hash 精确
    rows = s.search_images("C" * 64)  # 大写也归一
    assert len(rows) == 1 and rows[0]["hash"] == "c" * 64
    assert s.search_images("f" * 64) == []
    # 空查询=最近看过（无描述的也可见，按 last_seen 倒序）
    rows = s.search_images("")
    assert [r["hash"] for r in rows] == ["d" * 64, "a" * 64, "b" * 64, "c" * 64]


async def test_find_image_tool_returns_path_and_receipt(tmp_path):
    s = _store(tmp_path)
    s.record_image("a" * 64, "a.png", 2048, "一只橘猫趴在键盘上")

    class _Cfg:
        data_dir = str(tmp_path)
        timezone = ""

    tools = {t.name: t for t in build_memory_tools(s, _Cfg())}
    r = json.loads(await tools["find_image"].func({"query": "猫"}))
    assert r["ok"] and len(r["content"]) == 1
    row = r["content"][0]
    assert row["file"] == str(tmp_path / "images" / "a.png")
    assert "橘猫" in row["description"] and row["size_kb"] == 2
    assert "file 是本机路径" in r["note"]

    # 查不到给引导；空档案与空结果语义分开
    r = json.loads(await tools["find_image"].func({"query": "不存在的词"}))
    assert r["ok"] and r["content"] == [] and "换个关键词" in r["note"]
    s2 = _store(tmp_path / "empty.db")
    tools2 = {t.name: t for t in build_memory_tools(s2, _Cfg())}
    r = json.loads(await tools2["find_image"].func({}))
    assert "图片档案还是空的" in r["note"]

    # query 留空=最近的图（cfg 缺省时路径仍可拼出）
    r = json.loads(await tools["find_image"].func({}))
    assert r["content"][0]["file"].endswith("a.png")
