"use client";
/**
 * 全局 SSE 连接：EventSource 生命周期 + 事件环形缓冲（300 条）+ 订阅分发。
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
import type { SseEvent } from "@/lib/types";

export type SseStatus = "connecting" | "open" | "closed";

interface SseCtx {
  status: SseStatus;
  events: SseEvent[];
  subscribe: (cb: (ev: SseEvent) => void) => () => void;
}

const Ctx = createContext<SseCtx>({
  status: "connecting",
  events: [],
  subscribe: () => () => {},
});

export function SseProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SseStatus>("connecting");
  const [events, setEvents] = useState<SseEvent[]>([]);
  const listeners = useRef(new Set<(ev: SseEvent) => void>());

  useEffect(() => {
    let es: EventSource | null = null;
    let stopped = false;

    const connect = () => {
      const t = getToken();
      es = new EventSource(
        t ? `/api/events?token=${encodeURIComponent(t)}` : "/api/events",
      );
      es.onopen = () => setStatus("open");
      es.onerror = () => {
        if (es && es.readyState === EventSource.CLOSED) {
          setStatus("closed");
          window.dispatchEvent(new CustomEvent("lm:unauthorized"));
        } else {
          setStatus("connecting"); // EventSource 将自动重连
        }
      };
      es.onmessage = (e: MessageEvent<string>) => {
        try {
          const ev = JSON.parse(e.data) as SseEvent;
          setEvents((prev) => {
            const next = [...prev, ev];
            return next.length > 300 ? next.slice(-300) : next;
          });
          listeners.current.forEach((cb) => cb(ev));
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

  const subscribe = useCallback((cb: (ev: SseEvent) => void) => {
    listeners.current.add(cb);
    return () => listeners.current.delete(cb);
  }, []);

  return (
    <Ctx.Provider value={{ status, events, subscribe }}>{children}</Ctx.Provider>
  );
}

export function useSse() {
  return useContext(Ctx);
}
