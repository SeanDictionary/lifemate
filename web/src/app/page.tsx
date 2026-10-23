"use client";
/** 概览：生命标识 + 状态卡 + 最近动态（档案流：SSE 实时推送 + 30s 对账兑底） */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { HistoryResp, ObserveRecord, StatusResp } from "@/lib/types";
import { fmtUptime } from "@/lib/format";
import { mergeRecords } from "@/lib/records";
import { useRecords } from "@/hooks/use-records";
import { EventCard, recordToCards } from "@/components/history/event-card";
import { Orb } from "@/components/layout/orb";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";

function Stat({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <Card className="py-0">
      <CardContent className="p-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={`mt-0.5 truncate text-sm font-medium ${mono ? "font-mono" : ""}`} title={value}>
          {value}
        </div>
      </CardContent>
    </Card>
  );
}

export default function OverviewPage() {
  const { data: st } = useQuery({
    queryKey: ["status"],
    queryFn: () => apiGet<StatusResp>("/api/status"),
    refetchInterval: 3_000,
  });
  // 最近动态：档案流——初始播种 + SSE 实时追加，30s 对账兑底断线
  const { data: feed } = useQuery({
    queryKey: ["recent-feed"],
    queryFn: () => apiGet<HistoryResp>("/api/history?tail=30"),
    refetchInterval: 30_000,
  });
  const { subscribe } = useRecords();
  const [live, setLive] = useState<ObserveRecord[]>([]);
  useEffect(() => {
    const un = subscribe((rec) =>
      setLive((prev) => (prev.length > 200 ? [...prev.slice(-200), rec] : [...prev, rec])),
    );
    return un;
  }, [subscribe]);
  const cards = useMemo(
    () =>
      mergeRecords(feed?.rows ?? [], live)
        .slice(-30)
        .flatMap((r) =>
          recordToCards(r).map((m, j) => ({
            key: `${r.seq ?? r.ts}-${j}-${m.kind}`,
            model: m,
          })),
        )
        .reverse(),
    [feed, live],
  );

  return (
    <div className="space-y-6">
      {/* 生命状态区 */}
      <Card>
        <CardContent className="flex items-center gap-6 pl-6 pr-6 pt-4 pb-4">
          <Orb size={72} />
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold">此刻的它</h1>
              {st && (
                <Badge variant={st.busy ? "default" : "secondary"}>
                  {st.busy ? "思考中" : "休息中"}
                </Badge>
              )}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {st
                ? `会话 ${st.session_id} · 模型 ${st.model}`
                : "连接中…"}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* 状态卡 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="存活时长" value={st ? fmtUptime(st.uptime_s) : "—"} mono={false} />
        <Stat label="已装载插件" value={st ? st.plugins.join("、") || "无" : "—"} mono={false} />
        <Stat label="工具数" value={st ? String(st.tool_count) : "—"} />
        <Stat label="版本" value={st ? `v${st.version}` : "—"} />
        <Stat label="会话" value={st ? st.session_id : "—"} />
      </div>

      {/* 最近动态（SSE 实时推送 + 30s 对账，最新在上） */}
      <Card>
        <CardContent className="p-0">
          <div className="flex items-center gap-2 px-4 pb-4 text-sm font-medium">
            最近动态
            <span className="text-[11px] font-normal text-muted-foreground">
              {feed
                ? feed.ok
                  ? `档案尾部 ${feed.count} 条 · 实时推送`
                  : `读取失败：${feed.error ?? "未知错误"}`
                : "加载中…"}
            </span>
          </div>
          <ScrollArea className="h-[calc(100vh-24rem)] min-h-64">
            <div className="space-y-2 px-4 pb-4">
              {cards.length === 0 && (
                <div className="py-12 text-center text-sm text-muted-foreground">
                  暂无记录（它可能还没醒过）
                </div>
              )}
              {cards.map((c) => (
                <EventCard key={c.key} model={c.model} />
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
