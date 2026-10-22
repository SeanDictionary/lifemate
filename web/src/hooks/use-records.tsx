"use client";
/**
 * 档案记录流：/api/records SSE（recorder 落档行实时推送——与
 * /api/history 完全同形状同 seq）。历史/概览页用它替代高频轮询：
 * 初始 fetch 播种 + 实时追加 + sid+seq 去重，慢速对账轮询兜底断线。
 * EventSource 自带断线重连；token 变更时重建连接。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { getToken } from "@/lib/api";
import type { ObserveRecord } from "@/lib/types";

interface RecordsCtx {
  subscribe: (cb: (rec: ObserveRecord) => void) => () => void;
}

const DEFAULT_CTX: RecordsCtx = { subscribe: () => () => {} };

// Context 挂 globalThis：对 bundler 把本模块复制进多个 chunk 免疫（同 use-sse）
const g = globalThis as typeof globalThis & {
  __LM_RECORDS_CTX?: React.Context<RecordsCtx>;
};
const Ctx = (g.__LM_RECORDS_CTX ??= createContext<RecordsCtx>(DEFAULT_CTX));

const MAX_BUFFER = 500;

export function RecordsProvider({ children }: { children: ReactNode }) {
  const listeners = useRef(new Set<(rec: ObserveRecord) => void>());

  useEffect(() => {
    let es: EventSource | null = null;
    let stopped = false;

    const connect = () => {
      const t = getToken();
      es = new EventSource(
        t ? `/api/records?token=${encodeURIComponent(t)}` : "/api/records",
      );
      es.onmessage = (e: MessageEvent<string>) => {
        try {
          const rec = JSON.parse(e.data) as ObserveRecord;
          listeners.current.forEach((cb) => cb(rec));
        } catch {
          /* 坏帧忽略 */
        }
      };
    };

    connect();
    const onTokenChanged = () => {
      es?.close();
      if (!stopped) connect();
    };
    window.addEventListener("lm:token-changed", onTokenChanged);
    return () => {
      stopped = true;
      es?.close();
      window.removeEventListener("lm:token-changed", onTokenChanged);
    };
  }, []);

  const subscribe = useCallback((cb: (rec: ObserveRecord) => void) => {
    listeners.current.add(cb);
    return () => listeners.current.delete(cb);
  }, []);

  return <Ctx.Provider value={{ subscribe }} children={children} />;
}

export function useRecords() {
  return useContext(Ctx);
}
