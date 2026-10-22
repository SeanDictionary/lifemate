/**
 * 记录/事件 → 展示行：replay.py 的 render 语义移植。
 * 图标语言与后端三处（console/replay/host）保持同一套通用 emoji。
 */
import type { ObserveRecord, SseEvent } from "./types";
import { brief } from "./format";

export const GLYPHS: Record<string, string> = {
  wake: "🌅",
  assistant_msg: "💬",
  tool_call: "🔧",
  tool_result: "📥",
  rest: "🌙",
  session_rotated: "🔄",
  compacted: "📦",
  queue_drop: "🚫",
  error: "❌",
  boot: "🚀",
  session_load: "📂",
  config_reload: "🔁",
  host_stop: "🏁",
  user_msg: "📨",
  webui_msg: "📨",
};

export function glyphOf(type: string): string {
  return GLYPHS[type] ?? "⚪";
}

/** 一条展示行 */
export interface DisplayRow {
  icon: string;
  main: string;      // 主文本（等宽区）
  dim?: string;      // 次要信息（元数据/用量）
}

function ts(t: number): string {
  return new Date(t * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

function ridTag(rid: string | undefined): string {
  return rid ? `[${rid}] ` : "";
}

/** observe 档案记录 → 展示行（replay.py render 的 TS 版） */
export function renderRecord(r: ObserveRecord): DisplayRow[] {
  const t = ts(r.ts ?? 0);
  const tag = ridTag(r.rid);
  const out: DisplayRow[] = [];

  if (r.type === "msg") {
    if (r.role === "user") {
      out.push({ icon: "📨", main: `${t} ${tag}${brief(r.content, 400)}` });
    } else if (r.role === "assistant") {
      if (r.thinking) out.push({ icon: "💭", main: `${t} ${tag}${brief(r.thinking, 300)}`, dim: "思考" });
      if (r.content) out.push({ icon: "💬", main: `${t} ${tag}${brief(r.content, 400)}` });
      for (const tc of r.tool_calls ?? [])
        out.push({ icon: "🔧", main: `${t} ${tag}${tc.name}(${brief(tc.arguments, 120)})` });
      if (r.prompt_tokens)
        out.push({
          icon: "📊",
          main: `${t} ${r.model ?? ""}`,
          dim: `in=${r.prompt_tokens} out=${r.completion_tokens ?? 0} ${r.latency_ms ?? 0}ms stop=${r.stop_reason ?? ""}`,
        });
    } else if (r.role === "tool") {
      out.push({
        icon: "📥",
        main: `${t} ${r.name ?? ""} → ${r.ok ? "ok" : "FAIL"} ${r.latency_ms ?? 0}ms`,
        dim: brief(String(r.content ?? ""), 200),
      });
    }
    return out.length ? out : [{ icon: "⚪", main: `${t} ${tag}（空记录）` }];
  }

  switch (r.type) {
    case "wake":
      return [{ icon: "🌅", main: `${t} 唤醒 ${r.rid ?? ""} trigger=${r.trigger ?? ""}` }];
    case "rest":
      return [{ icon: "🌙", main: `${t} 休息 reason=${r.reason ?? ""}` }];
    case "session_rotated":
      return [{ icon: "🔄", main: `${t} 会话轮换（播种 ${r.seed_chars ?? 0} 字）` }];
    case "compact":
      return [{ icon: "📦", main: `${t} 压缩：丢弃 ${r.dropped} 条、保留 ${r.kept} 条` }];
    case "queue_drop":
      return [{ icon: "🚫", main: `${t} 丢弃排队信封 ${r.count} 封（${r.reason}）` }];
    case "boot":
      return [{ icon: "🚀", main: `${t} 启动 v${r.version} tools=${(r.tools as string[] | undefined)?.length ?? 0}` }];
    case "session_load":
      return [{ icon: "📂", main: `${t} 恢复会话（${r.msgs} 条消息）` }];
    case "config_reload":
      return [{ icon: "🔁", main: `${t} 配置热重载：${(r.changed as string[] | undefined)?.join(", ")}` }];
    case "host_stop":
      return [{ icon: "🏁", main: `${t} 退出（${r.reason}）` }];
    case "error":
      return [{ icon: "❌", main: `${t} 异常 ${r.where ?? ""}: ${brief(String(r.error ?? ""), 200)}` }];
    default:
      return [{ icon: "⚪", main: `${t} ${r.type}` }];
  }
}

/** 实时 SSE 事件 → 展示行 */
export function renderEvent(ev: SseEvent): DisplayRow[] {
  const d = ev.data ?? {};
  const get = (k: string) => (d[k] === undefined || d[k] === null ? "" : String(d[k]));
  const t = ts(ev.ts);

  if (ev.type === "assistant_msg") {
    const rows: DisplayRow[] = [];
    if (get("content")) rows.push({ icon: "💬", main: `${t} ${brief(get("content"), 400)}` });
    for (const tc of (d.tool_calls as { name: string; arguments: string }[] | undefined) ?? [])
      rows.push({ icon: "🔧", main: `${t} ${tc.name}(${brief(tc.arguments, 120)})` });
    return rows.length ? rows : [{ icon: "💬", main: `${t}（空回复）` }];
  }
  if (ev.type === "webui_msg") {
    return [{ icon: "📨", main: `${t} ${brief(get("text"), 400)}` }];
  }
  if (ev.type === "tool_call")
    return [{ icon: "🔧", main: `${t} ${get("name")}(${brief(get("arguments"), 120)})` }];
  if (ev.type === "tool_result")
    return [{ icon: "📥", main: `${t} ${get("name")} → ${get("ok") === "true" || d.ok ? "ok" : "FAIL"} ${get("latency_ms")}ms`, dim: brief(get("content"), 160) }];
  if (ev.type === "wake") return [{ icon: "🌅", main: `${t} 唤醒 trigger=${get("trigger")}` }];
  if (ev.type === "rest") return [{ icon: "🌙", main: `${t} 休息 ${get("reason")}` }];
  if (ev.type === "session_rotated") return [{ icon: "🔄", main: `${t} 会话轮换` }];
  if (ev.type === "error") return [{ icon: "❌", main: `${t} 异常 ${get("where")}: ${brief(get("error"), 200)}` }];
  return [{ icon: glyphOf(ev.type), main: `${t} ${ev.type}` }];
}
