"use client";
/**
 * 历史事件卡片：按认知角色分色的圆角半透明卡片。
 * 颜色族——amber 发言(心智之声)/blue 信封(输入)/violet 工具调用(行动)/
 * teal 回执(反馈)/red 错误与 FAIL/sky 唤醒/indigo 休息/orange 丢弃/
 * slate 思考与系统事件。深浅主题通用（/10 底 /30 边框）。
 */
import { useState } from "react";
import { fmtTime } from "@/lib/format";
import type { ObserveRecord } from "@/lib/types";

export type CardKind =
  | "envelope"
  | "speech"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "wake"
  | "rest"
  | "drop"
  | "error"
  | "system";

const CARD_STYLE: Record<CardKind, string> = {
  envelope: "border-blue-500/30 bg-blue-500/10",
  speech: "border-amber-500/35 bg-amber-500/10",
  thinking: "border-slate-500/25 bg-slate-500/5",
  tool_call: "border-violet-500/30 bg-violet-500/10",
  tool_result: "border-teal-500/30 bg-teal-500/10",
  wake: "border-sky-500/30 bg-sky-500/10",
  rest: "border-indigo-500/30 bg-indigo-500/10",
  drop: "border-orange-500/30 bg-orange-500/10",
  error: "border-red-500/30 bg-red-500/10",
  system: "border-border bg-muted/40",
};

export interface EventCardModel {
  kind: CardKind;
  emoji: string;
  label: string;
  ts: number;
  rid?: string;
  body?: string;
  mono?: boolean;    // 等宽（工具参数/回执 JSON）
  italic?: boolean;  // 思考斜体弱化
  meta?: string;     // dim 元数据行
  fail?: boolean;    // 回执失败 → red 样式
}

/** observe 记录 → 卡片模型（assistant 拆多卡：思考/发言/每个工具调用） */
export function recordToCards(r: ObserveRecord): EventCardModel[] {
  const ts = r.ts ?? 0;
  const rid = r.rid;

  if (r.type === "msg") {
    if (r.role === "user") {
      const env = (r.env ?? {}) as Record<string, string>;
      const meta = [env.origin, env.kind, env.source, env.delivery]
        .filter(Boolean)
        .join(" · ");
      return [{
        kind: "envelope", emoji: "📨", label: "信封", ts, rid,
        body: r.content ?? "",
        meta: meta || undefined,
      }];
    }
    if (r.role === "assistant") {
      const out: EventCardModel[] = [];
      if ((r.thinking ?? "").trim())
        out.push({ kind: "thinking", emoji: "💭", label: "思考", ts, rid,
                   body: r.thinking, italic: true });
      if ((r.content ?? "").trim())
        out.push({
          kind: "speech", emoji: "💬", label: "发言", ts, rid,
          body: r.content,
          meta: r.prompt_tokens
            ? [r.model, `in ${r.prompt_tokens}`,
               `out ${r.completion_tokens ?? 0}`,
               `${r.latency_ms ?? 0}ms`, r.stop_reason]
              .filter(Boolean).join(" · ")
            : undefined,
        });
      for (const tc of r.tool_calls ?? [])
        out.push({ kind: "tool_call", emoji: "🔧", label: "工具调用", ts, rid,
                   body: `${tc.name}(${tc.arguments})`, mono: true });
      return out;
    }
    if (r.role === "tool") {
      return [{
        kind: "tool_result", emoji: "📥", label: "工具回执", ts, rid,
        body: String(r.content ?? ""), mono: true,
        meta: `${r.ok ? "ok" : "FAIL"} · ${r.latency_ms ?? 0}ms`,
        fail: r.ok === false,
      }];
    }
    return [];
  }

  switch (r.type) {
    case "wake":
      return [{
        kind: "wake", emoji: "☀️", label: "唤醒", ts, rid,
        body: r.trigger ? `trigger=${r.trigger}` : undefined,
        meta: [r.kind, r.source].filter(Boolean).join(" · ") || undefined,
      }];
    case "rest":
      return [{
        kind: "rest", emoji: "🌙", label: "休息", ts, rid,
        body: r.reason ? `reason=${r.reason}` : undefined,
        meta: r.timeout_s ? `timeout ${r.timeout_s}s` : undefined,
      }];
    case "session_rotated":
      return [{ kind: "system", emoji: "🔄", label: "会话轮换", ts,
                body: `播种 ${r.seed_chars ?? 0} 字` }];
    case "compact":
      return [{ kind: "system", emoji: "📦", label: "压缩", ts,
                body: `丢弃 ${r.dropped} 条、保留 ${r.kept} 条、摘要 ${r.summary_chars} 字` }];
    case "queue_drop":
      return [{ kind: "drop", emoji: "🚫", label: "丢弃", ts,
                body: `${r.count} 封（${r.reason}）` }];
    case "boot":
      return [{ kind: "system", emoji: "🚀", label: "启动", ts,
                body: `v${r.version} · 工具 ${(r.tools as string[] | undefined)?.length ?? 0} 个` }];
    case "session_load":
      return [{ kind: "system", emoji: "📂", label: "恢复会话", ts,
                body: `${r.msgs} 条消息` }];
    case "config_reload":
      return [{ kind: "system", emoji: "🔁", label: "配置重载", ts,
                body: (r.changed as string[] | undefined)?.join(", ") || "无变化" }];
    case "host_stop":
      return [{ kind: "system", emoji: "🏁", label: "退出", ts,
                body: String(r.reason ?? "") }];
    case "error":
      return [{ kind: "error", emoji: "❌", label: "异常", ts,
                body: `${r.where ?? ""}: ${r.error ?? ""}` }];
    default:
      return [{ kind: "system", emoji: "⚪", label: r.type, ts, rid }];
  }
}

export function EventCard({
  model,
  onRid,
}: {
  model: EventCardModel;
  onRid?: (rid: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const style = model.fail ? CARD_STYLE.error : CARD_STYLE[model.kind];
  const long = (model.body?.length ?? 0) > 160;

  return (
    <div className={`animate-in fade-in slide-in-from-top-2 duration-300 rounded-lg border px-3 py-2 text-sm ${style}`}>
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="text-sm leading-none">{model.emoji}</span>
        <span>{model.label}</span>
        {model.rid && (
          <button
            className="rounded border px-1.5 py-px font-mono hover:bg-foreground/10"
            title="按此周期过滤（链路回放）"
            onClick={() => onRid?.(model.rid!)}
          >
            {model.rid}
          </button>
        )}
        <span className="ml-auto font-mono">{fmtTime(model.ts)}</span>
      </div>
      {model.body && (
        <div
          className={`mt-1 whitespace-pre-wrap break-all ${
            model.mono ? "font-mono text-xs" : ""
          } ${model.italic ? "text-muted-foreground italic" : ""} ${
            long && !expanded ? "line-clamp-3" : ""
          }`}
          style={long ? { cursor: "pointer" } : undefined}
          onClick={long ? () => setExpanded((v) => !v) : undefined}
          title={long && !expanded ? "点击展开全文" : undefined}
        >
          {model.body}
        </div>
      )}
      {model.meta && (
        <div className="mt-1 font-mono text-[11px] text-muted-foreground">
          {model.meta}
        </div>
      )}
    </div>
  );
}
