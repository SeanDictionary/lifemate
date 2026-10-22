"use client";
/** 概览：生命标识 + 状态卡 + 最近动态（observe 档案尾部轮询，近实时） */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { HistoryResp, StatusResp } from "@/lib/types";
import { fmtUptime } from "@/lib/format";
import { EventCard, recordToCards } from "@/components/history/event-card";
import { Orb } from "@/components/layout/orb";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";

function Stat({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={`mt-1 truncate text-sm font-medium ${mono ? "font-mono" : ""}`} title={value}>
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
  // 最近动态：档案尾部（date 缺省=最新有档案日），3s 轮询近实时
  const { data: feed } = useQuery({
    queryKey: ["recent-feed"],
    queryFn: () => apiGet<HistoryResp>("/api/history?tail=30"),
    refetchInterval: 3_000,
  });
  const cards = useMemo(
    () =>
      (feed?.rows ?? []).flatMap((r) =>
        recordToCards(r).map((m) => ({ key: `${r.seq ?? r.ts}-${m.kind}`, model: m })),
      ).reverse(),
    [feed],
  );

  return (
    <div className="space-y-6">
      {/* 生命状态区 */}
      <Card>
        <CardContent className="flex items-center gap-6 p-6">
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

      {/* 最近动态（档案尾部 + 3s 轮询，最新在上） */}
      <Card>
        <CardContent className="p-0">
          <div className="flex items-center gap-2 px-4 pt-4 pb-2 text-sm font-medium">
            最近动态
            <span className="text-[11px] font-normal text-muted-foreground">
              {feed
                ? feed.ok
                  ? `档案尾部 ${feed.count} 条 · 3s 轮询`
                  : `读取失败：${feed.error ?? "未知错误"}`
                : "加载中…"}
            </span>
          </div>
          <ScrollArea className="h-96">
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
