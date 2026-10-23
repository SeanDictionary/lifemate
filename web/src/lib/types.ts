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
  seq?: number;
  sid?: string;
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
  error?: string;
  available?: string[];
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

/** 配置字段声明（/api/config/schema——设置页数据驱动渲染的依据） */
export interface SchemaField {
  type: "string" | "number" | "bool" | "array" | "object";
  default: unknown;
  description: string;
  group?: string;
  secret?: boolean;
  restart?: boolean;
  env_var?: string;
}

export interface ConfigSchemaResp {
  ok: boolean;
  fields: Record<string, SchemaField>;
  plugins: Record<string, Record<string, SchemaField>>;
  /** 当前真被环境变量占用的主配置键 */
  env_set: string[];
}

export interface ApplyResult {
  ok: boolean;
  applied: string[];
  skipped_masked: string[];
  unknown: string[];
  env_locked: string[];
}

export interface SkillsResp {
  ok: boolean;
  skills: { name: string; description: string; source: string }[];
}

export interface SkillDetailResp {
  ok: boolean;
  name: string;
  content: string;
  source: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  inputSchema: {
    type?: string;
    properties?: Record<string, { type?: string; description?: string }>;
    required?: string[];
  };
  interruptible: boolean;
  source: string;
}

export interface ToolsResp {
  ok: boolean;
  tools: ToolInfo[];
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
