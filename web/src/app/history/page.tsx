"use client";
/** 历史：observe 档案（日期/rid 过滤 + 尾部翻页倒序）· 事件卡片视图 · 3s 轮询近实时 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { DatesResp, HistoryResp } from "@/lib/types";
import { EventCard, recordToCards } from "@/components/history/event-card";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const PAGE = 200; // 卡片比纯文本行重，分页收紧

export default function HistoryPage() {
  const [date, setDate] = useState("");
  const [rid, setRid] = useState("");
  const [older, setOlder] = useState(0); // 从尾部跳过的条数（0=最新一页）

  const dates = useQuery({
    queryKey: ["dates"],
    queryFn: () => apiGet<DatesResp>("/api/dates"),
    refetchInterval: 3_000,
  });
  const effectiveDate = date || dates.data?.dates.at(-1) || "";
  const effectiveRid = rid.trim();

  // 尾部翻页 + 3s 轮询：首页即最新记录，新到达时窗口自然滑动
  const history = useQuery({
    queryKey: ["history", effectiveDate, effectiveRid, older],
    queryFn: () => {
      const p = new URLSearchParams({
        date: effectiveDate,
        offset: String(older),
        limit: String(PAGE),
        tail: String(PAGE),
      });
      if (effectiveRid) p.set("rid", effectiveRid);
      return apiGet<HistoryResp>(`/api/history?${p}`);
    },
    enabled: !!effectiveDate,
    refetchInterval: 3_000,
  });

  const total = history.data?.total ?? 0;
  const cards = useMemo(
    () =>
      (history.data?.rows ?? []).flatMap((r) =>
        recordToCards(r).map((m) => ({
          key: `${r.seq ?? r.ts}-${m.kind}-${m.label}`,
          model: m,
        })),
      ).reverse(), // 倒序：最新恒在顶部
    [history.data],
  );

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">历史</h1>

      <div className="flex flex-wrap items-center gap-2">
        <div className="w-44">
          <Select
            value={effectiveDate}
            onValueChange={(v) => {
              setDate(v);
              setOlder(0);
            }}
          >
            <SelectTrigger size="sm">
              <SelectValue
                placeholder={dates.isLoading ? "加载中…" : "选择日期"}
              />
            </SelectTrigger>
            <SelectContent>
              {(dates.data?.dates ?? []).map((d) => (
                <SelectItem key={d} value={d}>
                  {d}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Input
          className="h-8 w-40 font-mono text-xs"
          placeholder="rid 过滤（链路回放）"
          value={rid}
          onChange={(e) => {
            setRid(e.target.value);
            setOlder(0);
          }}
        />
        {effectiveRid && (
          <Button variant="ghost" size="sm" onClick={() => setRid("")}>
            清除 rid ✕
          </Button>
        )}
        <span className="text-xs text-muted-foreground">
          {history.data ? `共 ${total} 条 · 3s 轮询` : ""}
        </span>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!history.data || older === 0}
            onClick={() => setOlder(Math.max(0, older - PAGE))}
          >
            ← 更近
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!history.data || older + PAGE >= total}
            onClick={() => setOlder(older + PAGE)}
          >
            更早 →
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-15rem)]">
            <div className="space-y-2 p-4">
              {history.isLoading && (
                <div className="space-y-2">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-12 w-3/4" />
                  <Skeleton className="h-16 w-5/6" />
                </div>
              )}
              {history.data && history.data.rows.length === 0 && (
                <div className="py-16 text-center text-sm text-muted-foreground">
                  该条件下无记录
                </div>
              )}
              {cards.map((c) => (
                <EventCard key={c.key} model={c.model} onRid={(v) => {
                  setRid(v);
                  setOlder(0);
                }} />
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
