"use client";
/** 对话：环形缓冲首屏 + SSE webui_msg 增量 + POST /api/say */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";
import type { ChatResp, Receipt } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { useSse } from "@/hooks/use-sse";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

interface Item {
  dir: "in" | "out";
  text: string;
  ts: number;
}

export default function ChatPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { subscribe } = useSse();

  // 首屏：环形缓冲补拉（刷新不丢对话）
  const initial = useQuery({
    queryKey: ["chat"],
    queryFn: () => apiGet<ChatResp>("/api/chat?limit=200"),
  });
  useEffect(() => {
    if (initial.data && items.length === 0) {
      setItems(initial.data.messages);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial.data]);

  // 增量：webui_msg（它的话）
  useEffect(
    () =>
      subscribe((ev) => {
        if (ev.type === "webui_msg") {
          const text = String(ev.data?.text ?? "");
          if (text) setItems((prev) => [...prev, { dir: "out", text, ts: ev.ts }]);
        }
      }),
    [subscribe],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    try {
      await apiPost<Receipt>("/api/say", { text });
      setItems((prev) => [...prev, { dir: "in", text, ts: Date.now() / 1000 }]);
    } catch (e) {
      toast.error(`发送失败：${(e as Error).message}`);
      setDraft(text);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">对话</h1>
        <span className="text-xs text-muted-foreground">
          你说的话成为信封直达心智；它用 webui_send 回话
        </span>
      </div>

      <Card>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-16rem)]">
            <div className="space-y-3 p-4">
              {items.length === 0 && (
                <div className="py-16 text-center text-sm text-muted-foreground">
                  还没有对话——说第一句话吧
                </div>
              )}
              {items.map((m, i) => (
                <div key={i} className={`flex ${m.dir === "in" ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[75%] rounded-xl px-3.5 py-2 text-sm leading-relaxed ${
                      m.dir === "in"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted"
                    }`}
                  >
                    <div className="whitespace-pre-wrap break-words">{m.text}</div>
                    <div
                      className={`mt-1 text-[10px] ${
                        m.dir === "in" ? "text-primary-foreground/60" : "text-muted-foreground"
                      }`}
                    >
                      {fmtTime(m.ts)} {m.dir === "out" ? "它" : "你"}
                    </div>
                  </div>
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
          <div className="flex gap-2 border-t p-3">
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
              placeholder="对它说…（回车发送）"
              disabled={sending}
            />
            <Button onClick={send} disabled={sending || !draft.trim()}>
              发送
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
