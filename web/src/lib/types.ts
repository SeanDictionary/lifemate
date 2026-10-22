/**
 * 契约镜像：lifemate/kernel/contracts.py + lifemate/server 的 API 响应。
 * 改契约两边同步（Python 侧改了字段，这里跟着改）。
 */

// ── 内核事件类型（MindEvent.type）─────────────────────────
export type MindEventType =
  | "wake"
  | "user_msg"
  | "assistant_msg"
  | "tool_call"
  | "tool_result"
  | "rest"
  | "session_rotated"
  | "compacted"
  | "queue_drop"
  | "error";

/** SSE 事件帧（/api/events） */
export interface SseEvent {
  type: MindEventType | "webui_msg" | string;
  ts: number;
  data: Record<string, unknown>;
}

/** observe 档案一行（/api/history rows；字段按记录类型而异，宽松 typing） */
export interface ObserveRecord {
  ts: number;
  type: string;
  rid?: string;
  role?: string;
  content?: string;
  thinking?: string;
  tool_calls?: { name: string; arguments: string }[];
  ok?: boolean;
  name?: string;
  latency_ms?: number;
  model?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  stop_reason?: string;
  trigger?: string;
  reason?: string;
  [k: string]: unknown;
}

// ── API 响应 ─────────────────────────────────────────────
export interface StatusResp {
  ok: boolean;
  version: string;
  uptime_s: number;
  session_id: string;
  model: string;
  busy: boolean;
  plugins: string[];
  tool_count: number;
  now: number;
}

export interface DatesResp {
  ok: boolean;
  dates: string[];
}

export interface HistoryResp {
  ok: boolean;
  count: number;
  total: number;
  rows: ObserveRecord[];
}

export interface ChatMessage {
  dir: "in" | "out";
  text: string;
  ts: number;
}

export interface ChatResp {
  ok: boolean;
  messages: ChatMessage[];
}

export interface PluginPanelMeta {
  title: string;
  icon: string;
}

export interface PluginDecl {
  name: string;
  panel: PluginPanelMeta | null;
}

export interface PluginsResp {
  ok: boolean;
  plugins: PluginDecl[];
}

export interface ConfigResp {
  ok: boolean;
  config: Record<string, unknown>;
}

export interface ApplyResult {
  ok: boolean;
  applied: string[];
  skipped_masked: string[];
  unknown: string[];
  env_locked: string[];
}

export interface UsageResp {
  ok: boolean;
  date: string;
  by_model: Record<
    string,
    { calls: number; tokens_in: number; tokens_out: number; latency_ms_avg: number }
  >;
}

export interface MemoryResp {
  ok: boolean;
  rows: Record<string, unknown>[];
}

/** 通用回执（与工具回执同形） */
export interface Receipt {
  ok: boolean;
  error?: string;
  [k: string]: unknown;
}
