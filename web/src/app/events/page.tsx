"use client";
/** 事件流：SSE 实时，按 rid（唤醒周期）分组折叠，类型过滤 */
import { useMemo, useState } from "react";
import { useSse } from "@/hooks/use-sse";
import { renderEvent } from "@/lib/render";
import type { SseEvent } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ConnDot } from "@/components/layout/theme-toggle";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";

interface Group {
  rid: string;
  events: SseEvent[];
}

function EventRow({ ev }: { ev: SseEvent }) {
  return (
    <>
      {renderEvent(ev).map((row, j) => (
        <div key={j} className="flex gap-2 leading-relaxed">
          <span className="shrink-0">{row.icon}</span>
          <span className="min-w-0 break-all">{row.main}</span>
          {row.dim && <span className="shrink-0 text-muted-foreground">{row.dim}</span>}
        </div>
      ))}
    </>
  );
}

export default function EventsPage() {
  const { events, status } = useSse();
  const [filter, setFilter] = useState("all");

  const filtered = useMemo(
    () => (filter === "all" ? events : events.filter((e) => e.type === filter)),
    [events, filter],
  );

  // 按 rid 分组：无 rid 的事件（webui_msg 等）单独成组
  const groups = useMemo(() => {
    const out: Group[] = [];
    for (const ev of filtered) {
      const rid = ev.data?.rid ? String(ev.data.rid) : ev.type === "wake" ? `wake-${ev.ts}` : "-";
      const last = out[out.length - 1];
      if (last && last.rid === rid) last.events.push(ev);
      else out.push({ rid, events: [ev] });
    }
    return out;
  }, [filtered]);

  const types = useMemo(() => [...new Set(events.map((e) => e.type))], [events]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">事件流</h1>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <ConnDot />
          {status === "open" ? "实时" : status === "connecting" ? "重连中" : "已断开"}
        </div>
        <div className="ml-auto w-44">
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger size="sm">
              <SelectValue placeholder="全部类型" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部类型</SelectItem>
              {types.map((t) => (
                <SelectItem key={t} value={t}>{t}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-13rem)]">
            <div className="space-y-2 p-4 font-mono text-xs">
              {groups.length === 0 && (
                <div className="py-16 text-center text-muted-foreground">
                  等待事件…（它可能正在休息）
                </div>
              )}
              {groups.map((g, i) => {
                const isCycle = g.rid !== "-";
                const wakeEv = g.events.find((e) => e.type === "wake");
                return (
                  <Collapsible key={i} defaultOpen>
                    <div className="rounded-md border">
                      {isCycle && (
                        <CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-accent/40">
                          <span>🌅 周期 {g.rid.slice(0, 10)}</span>
                          {wakeEv?.data?.trigger ? (
                            <Badge variant="outline" className="h-4 px-1.5 text-[10px]">
                              {String(wakeEv.data.trigger)}
                            </Badge>
                          ) : null}
                          <span className="ml-auto">{g.events.length} 事件</span>
                        </CollapsibleTrigger>
                      )}
                      <CollapsibleContent>
                        <div className="space-y-1 border-t px-3 py-2">
                          {g.events.map((ev, j) => (
                            <EventRow key={j} ev={ev} />
                          ))}
                        </div>
                      </CollapsibleContent>
                    </div>
                  </Collapsible>
                );
              })}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
