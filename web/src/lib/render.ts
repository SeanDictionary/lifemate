/**
 * 记录/事件 → 展示行：replay.py 的 render 语义移植。
 * 图标语言与后端三处（console/replay/host）保持同一套通用 emoji。
 */
import type { SseEvent } from "./types";
import { brief } from "./format";

export const GLYPHS: Record<string, string> = {
  wake: "☀️",
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
  if (ev.type === "wake") return [{ icon: "☀️", main: `${t} 唤醒 trigger=${get("trigger")}` }];
  if (ev.type === "rest") return [{ icon: "🌙", main: `${t} 休息 ${get("reason")}` }];
  if (ev.type === "session_rotated") return [{ icon: "🔄", main: `${t} 会话轮换` }];
  if (ev.type === "error") return [{ icon: "❌", main: `${t} 异常 ${get("where")}: ${brief(get("error"), 200)}` }];
  return [{ icon: glyphOf(ev.type), main: `${t} ${ev.type}` }];
}
