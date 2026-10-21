"""基建测试：重试/背压/自愈/WAL/备份/清理/单实例锁/日志。"""
import asyncio
import logging
import sqlite3

import pytest

from lifemate.kernel.contracts import QUEUE_DROP, Envelope
from lifemate.kernel.llm import _FailStreak, chat_with_retry, is_retryable
from lifemate.utils.instance import acquire, release
from lifemate.utils.log import setup_logging
from lifemate.utils.recorder import Recorder

from conftest import FakeLLM, make_config, make_persona, make_session, text_reply
from lifemate.kernel.compaction import Compactor
from lifemate.kernel.mind import SelfKernel
from lifemate.memory.store import Store


class TransientError(Exception):
    pass


# ── 1. LLM 重试 ────────────────────────────────────────

async def test_retry_transient_then_success():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("网关抖动")
        return text_reply("成功")

    TransientError.__name__ = "APIConnectionError"  # 类名匹配可重试
    try:
        resp = await chat_with_retry(flaky, max_retries=3, backoff_s=0)
    finally:
        TransientError.__name__ = "TransientError"

    assert resp.content == "成功" and calls["n"] == 3


async def test_retry_permanent_raises_immediately():
    calls = {"n": 0}

    async def broken():
        calls["n"] += 1
        raise ValueError("参数错误")  # 不可重试

    with pytest.raises(ValueError):
        await chat_with_retry(broken, max_retries=3, backoff_s=0)
    assert calls["n"] == 1


async def test_retry_exhausted_alerts_streak():
    alerts: list[int] = []
    streak = _FailStreak(threshold=2, on_alert=alerts.append)

    async def always_dead():
        raise TransientError("彻底挂了")

    TransientError.__name__ = "RateLimitError"
    try:
        for _ in range(2):
            with pytest.raises(TransientError):
                await chat_with_retry(always_dead, max_retries=1, backoff_s=0,
                                      fail_streak=streak)
    finally:
        TransientError.__name__ = "TransientError"
    assert streak.count == 2 and alerts == [2]  # 第二次失败触发一次告警


def test_is_retryable_classification():
    original = TransientError.__name__
    try:
        TransientError.__name__ = "InternalServerError"
        assert is_retryable(TransientError()) is True
        TransientError.__name__ = "AuthenticationError"
        assert is_retryable(TransientError()) is False
        TransientError.__name__ = "UnknownBlah"
        assert is_retryable(TransientError()) is False
    finally:
        TransientError.__name__ = original


# ── 5. 排队背压 ────────────────────────────────────────

async def test_queue_backpressure_drops_oldest(tmp_path):
    llm = FakeLLM([text_reply("嗯")])
    cfg = make_config(tmp_path)
    cfg.queue_max_steer = 3
    k = SelfKernel(llm=llm, persona=make_persona(tmp_path),
                   session=make_session(tmp_path),
                   compactor=Compactor(llm.chat, cfg), cfg=cfg)
    drops: list[dict] = []
    k.subscribe(lambda ev: drops.append(ev.data) if ev.type == QUEUE_DROP else None)

    for i in range(6):
        k._steer_q.append(Envelope(text=f"信封{i}"))
    k._trim_steer()
    assert len(k._steer_q) == 3
    # 丢最旧，保最新
    assert [e.text for e in k._steer_q] == ["信封3", "信封4", "信封5"]
    assert drops and drops[0]["reason"] == "overflow_steer"
    assert drops[0]["texts"] == ["信封0", "信封1", "信封2"]


# ── 6. watchdog 自愈 ──────────────────────────────────

async def test_heal_revives_zombie_queue(tmp_path):
    """task 死了但队列非满 → heal 重新拉起运行。"""
    llm = FakeLLM([text_reply("复活后处理")])
    k = SelfKernel(llm=llm, persona=make_persona(tmp_path),
                   session=make_session(tmp_path),
                   compactor=Compactor(llm.chat, make_config(tmp_path)),
                   cfg=make_config(tmp_path))
    k._task = None  # 模拟 task 消亡
    k._steer_q.append(Envelope(text="僵尸队列里的信"))
    info = await k.heal()
    assert info["healed"] == "revived"
    await k.wait_idle()
    assert any(m.get("content") == "僵尸队列里的信"
               for m in k._session.messages)


# ── 7/8. WAL / 备份 / 保留期清理 ──────────────────────

def test_sqlite_wal_mode(tmp_path):
    s = Store(tmp_path / "m.db")
    mode = s._con.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_store_backup_roundtrip(tmp_path):
    s = Store(tmp_path / "m.db")
    s.record_episode("值得备份的一生片段")
    dest = s.backup(tmp_path / "backups" / "copy.db")
    check = sqlite3.connect(dest)
    rows = check.execute("SELECT summary FROM episodes").fetchall()
    check.close()
    assert rows and "值得备份" in rows[0][0]


def test_observe_retention_cleanup(tmp_path):
    import os
    r = Recorder(tmp_path / "observe")
    r.event("boot", x=1)
    r.close()
    # 把档案时间改到 100 天前
    f = list((tmp_path / "observe").glob("*.jsonl"))[0]
    old = time.time() - 100 * 86400
    os.utime(f, (old, old))
    import time as _t
    assert r.cleanup(90) == 1
    assert not list((tmp_path / "observe").glob("*.jsonl"))
    assert r.cleanup(0) == 0  # 0=永久保留


import time  # noqa: E402


# ── 4. 单实例锁 ────────────────────────────────────────

def test_single_instance_lock(tmp_path):
    lock1 = acquire(tmp_path)
    with pytest.raises(SystemExit):
        acquire(tmp_path)
    release(lock1)
    lock2 = acquire(tmp_path)  # 释放后可重新获取
    release(lock2)


# ── 2. 全局日志 ────────────────────────────────────────

def test_log_file_written(tmp_path):
    path = setup_logging(tmp_path, max_bytes=1024, backup_count=1)
    logging.getLogger("lifemate.test").warning("一条测试日志")
    for h in logging.getLogger().handlers:
        h.flush()
    content = path.read_text(encoding="utf-8")
    assert "一条测试日志" in content and path.suffix == ".log"
    # 幂等：重复 setup 不叠加 handler
    n = len(logging.getLogger().handlers)
    setup_logging(tmp_path)
    assert len(logging.getLogger().handlers) == n


def test_log_console_off(tmp_path):
    """console=False（--silence）：只写文件，不挂终端 handler。"""
    setup_logging(tmp_path, console=False)
    handlers = logging.getLogger().handlers
    assert not any(type(h) is logging.StreamHandler for h in handlers)
    logging.getLogger("lifemate.test").warning("静默测试")
    for h in handlers:
        h.flush()
    assert "静默测试" in (tmp_path / "logs" / "lifemate.log").read_text(
        encoding="utf-8")
