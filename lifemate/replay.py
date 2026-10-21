"""回放查看器：像翻 pi 会话一样翻它的一天。

用法：
    python -m lifemate.replay                  # 今天
    python -m lifemate.replay 2026-10-21       # 指定日期
    python -m lifemate.replay --rid r8f2       # 只看一次运行
    python -m lifemate.replay --tail 50        # 最后 50 行
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _t(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _brief(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def render(line: dict) -> str:
    ts = _t(line.get("ts", 0))
    rid = line.get("rid", "")
    tag = f"[{rid}] " if rid else ""
    t = line.get("type")

    if t == "msg":
        role = line.get("role")
        if role == "user":
            env = line.get("env") or {}
            meta = "·".join(str(x) for x in (
                env.get("kind", ""), env.get("source", ""),
                env.get("delivery", "")) if x)
            head = f"（{meta}）" if meta else ""
            return f"{ts} ✉ {tag}{head}{_brief(line.get('content', ''), 400)}"
        if role == "assistant":
            out = []
            thinking = (line.get("thinking") or "").strip()
            if thinking:
                out.append(f"{ts}   💭 {tag}{_brief(thinking, 300)}")
            content = (line.get("content") or "").strip()
            if content:
                out.append(f"{ts} 💬 {tag}{_brief(content, 400)}")
            for tc in line.get("tool_calls") or []:
                out.append(f"{ts}   🔧→ {tc.get('name')}({_brief(tc.get('arguments', ''), 120)})")
            usage = line.get("prompt_tokens") or 0
            if usage:
                out.append(f"{ts}   ⚙ {line.get('model', '')} "
                           f"in={usage} out={line.get('completion_tokens', 0)} "
                           f"{line.get('latency_ms', 0)}ms "
                           f"stop={line.get('stop_reason', '')}")
            return "\n".join(out) or f"{ts} 💬 {tag}（空回复）"
        if role == "tool":
            ok = "ok" if line.get("ok") else "FAIL"
            return (f"{ts}   ↳ {line.get('name')} → {ok} "
                    f"{line.get('latency_ms', 0)}ms: {_brief(line.get('content', ''))}")
        return f"{ts} ？ {line}"

    if t == "run":
        return f"{ts} ▶ run {rid} kind={line.get('kind', '')} source={line.get('source', '')}"
    if t == "run_end":
        return f"{ts} ■ run_end {rid} reason={line.get('reason', '')}"
    if t == "rotate":
        return f"{ts} ↻ 会话轮换 {line.get('old')} → {line.get('new')}（播种 {line.get('seed_chars', 0)} 字）"
    if t == "compact":
        return (f"{ts} ⇲ 压缩：丢弃 {line.get('dropped')} 条、保留 {line.get('kept')} 条、"
                f"摘要 {line.get('summary_chars')} 字（in={line.get('prompt_tokens', 0)}）")
    if t == "queue_drop":
        return f"{ts} ✂ 丢弃排队信封 {line.get('count')} 封（{line.get('reason')}）"
    if t == "boot":
        return (f"{ts} ⏻ 启动 v{line.get('version')} persona={line.get('persona_hash')} "
                f"tools={len(line.get('tools', []))}")
    if t == "session_load":
        return f"{ts} ⏦ 恢复会话 {line.get('file')}（{line.get('msgs')} 条消息）"
    if t == "config_reload":
        return f"{ts} ↻ 配置热重载：{', '.join(line.get('changed', []))}"
    if t == "host_stop":
        return f"{ts} ⏼ 退出（{line.get('reason')}）"
    if t == "error":
        return f"{ts} ✖ 异常 {line.get('where')}: {_brief(line.get('error', ''), 200)}"
    return f"{ts} · {t} {json.dumps({k: v for k, v in line.items() if k not in ('ts', 'type')}, ensure_ascii=False)[:200]}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="lifemate.replay",
                                 description="回放观测档案")
    ap.add_argument("date", nargs="?", default=None,
                    help="日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--rid", default=None, help="只看指定运行")
    ap.add_argument("--tail", type=int, default=None, help="只看最后 N 行")
    args = ap.parse_args()

    data_dir = Path(os.environ.get("LIFEMATE_DATA_DIR", "data"))
    observe = data_dir / "observe"
    date = args.date or time.strftime("%Y-%m-%d")
    path = observe / f"{date}.jsonl"
    if not path.exists():
        avail = sorted(f.stem for f in observe.glob("*.jsonl")) if observe.exists() else []
        sys.exit(f"无档案 {path}。可用日期：{', '.join(avail) or '（无）'}")

    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            lines.append(json.loads(raw))
        except ValueError:
            continue
    if args.rid:
        lines = [x for x in lines if x.get("rid") == args.rid]
    if args.tail:
        lines = lines[-args.tail:]

    print(f"── 回放 {date}（{len(lines)} 行）──")
    for line in lines:
        out = render(line)
        if out:
            print(out)


if __name__ == "__main__":
    main()
