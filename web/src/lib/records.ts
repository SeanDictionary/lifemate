"use client";
/** 档案记录合并：fetch 播种 + SSE 实时追加的去重合并（历史/概览共用）。 */
import type { ObserveRecord } from "@/lib/types";

/** 稳定标识：sid+seq（seq 是 recorder 单调计数，进程重启归零——sid 兜底区分） */
function keyOf(r: ObserveRecord): string {
  return r.seq !== undefined ? `${r.sid ?? ""}-${r.seq}` : `${r.ts}-${r.type}`;
}

/** 合并去重（fetch 与 SSE 重叠段只留一份），按时间正序返回。 */
export function mergeRecords(
  fetched: ObserveRecord[],
  live: ObserveRecord[],
): ObserveRecord[] {
  const seen = new Map<string, ObserveRecord>();
  for (const r of fetched) seen.set(keyOf(r), r);
  for (const r of live) seen.set(keyOf(r), r);
  return [...seen.values()].sort((a, b) => a.ts - b.ts);
}
