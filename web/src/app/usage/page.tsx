"use client";
/** 用量分账：按模型聚合（来自 observe 档案） */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { DatesResp, UsageResp } from "@/lib/types";
import { fmtNum } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-mono text-lg">{value}</div>
    </div>
  );
}

export default function UsagePage() {
  const [date, setDate] = useState("");
  const dates = useQuery({
    queryKey: ["dates"],
    queryFn: () => apiGet<DatesResp>("/api/dates"),
  });
  const effectiveDate = date || dates.data?.dates.at(-1) || "";
  const usage = useQuery({
    queryKey: ["usage", effectiveDate],
    queryFn: () => apiGet<UsageResp>(`/api/usage?date=${effectiveDate}`),
    enabled: !!effectiveDate,
  });

  const models = Object.entries(usage.data?.by_model ?? {});
  const totals = models.reduce(
    (a, [, m]) => ({
      calls: a.calls + m.calls,
      in: a.in + m.tokens_in,
      out: a.out + m.tokens_out,
    }),
    { calls: 0, in: 0, out: 0 },
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">用量</h1>
        <div className="w-44">
          <Select value={effectiveDate} onValueChange={setDate}>
            <SelectTrigger size="sm">
              <SelectValue placeholder="选择日期" />
            </SelectTrigger>
            <SelectContent>
              {(dates.data?.dates ?? []).map((d) => (
                <SelectItem key={d} value={d}>{d}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Card>
        <CardContent className="grid grid-cols-3 gap-4 p-6">
          <Metric label="调用次数" value={String(totals.calls)} />
          <Metric label="输入 token" value={fmtNum(totals.in)} />
          <Metric label="输出 token" value={fmtNum(totals.out)} />
        </CardContent>
      </Card>

      {models.length === 0 && (
        <div className="py-12 text-center text-sm text-muted-foreground">
          {effectiveDate ? "当日无调用记录" : "无档案"}
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {models.map(([m, u]) => (
          <Card key={m}>
            <CardContent className="space-y-3 p-5">
              <div className="truncate font-mono text-sm" title={m}>{m}</div>
              <div className="grid grid-cols-4 gap-3">
                <Metric label="次数" value={String(u.calls)} />
                <Metric label="in" value={fmtNum(u.tokens_in)} />
                <Metric label="out" value={fmtNum(u.tokens_out)} />
                <Metric label="均延迟" value={`${u.latency_ms_avg}ms`} />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
