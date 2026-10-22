"use client";
/** 概览：生命标识 + 状态卡 + 最近动态 */
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { StatusResp } from "@/lib/types";
import { fmtUptime } from "@/lib/format";
import { renderEvent } from "@/lib/render";
import { useSse } from "@/hooks/use-sse";
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
    refetchInterval: 10_000,
  });
  const { events } = useSse();
  const recent = events.slice(-14).reverse();

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

      {/* 最近动态 */}
      <Card>
        <CardContent className="p-0">
          <div className="px-4 pt-4 pb-2 text-sm font-medium">最近动态（实时）</div>
          <ScrollArea className="h-72">
            <div className="space-y-1 px-4 pb-4 font-mono text-xs">
              {recent.length === 0 && (
                <div className="py-8 text-center text-muted-foreground">
                  等待事件…（它可能正在休息）
                </div>
              )}
              {recent.map((ev, i) =>
                renderEvent(ev).map((row, j) => (
                  <div key={`${i}-${j}`} className="flex gap-2 leading-relaxed">
                    <span className="shrink-0">{row.icon}</span>
                    <span className="min-w-0 break-all">{row.main}</span>
                    {row.dim && (
                      <span className="shrink-0 text-muted-foreground">{row.dim}</span>
                    )}
                  </div>
                )),
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
