"use client";
/** 历史回放：observe 档案（日期/rid 过滤 + 分页） */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { DatesResp, HistoryResp } from "@/lib/types";
import { renderRecord } from "@/lib/render";
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

const PAGE = 500;

export default function HistoryPage() {
  const [date, setDate] = useState("");
  const [rid, setRid] = useState("");
  const [offset, setOffset] = useState(0);

  const dates = useQuery({
    queryKey: ["dates"],
    queryFn: () => apiGet<DatesResp>("/api/dates"),
  });
  // 默认选最新日期
  const effectiveDate = date || dates.data?.dates.at(-1) || "";
  const effectiveRid = rid.trim();

  const history = useQuery({
    queryKey: ["history", effectiveDate, effectiveRid, offset],
    queryFn: () => {
      const p = new URLSearchParams({ date: effectiveDate, offset: String(offset), limit: String(PAGE) });
      if (effectiveRid) p.set("rid", effectiveRid);
      return apiGet<HistoryResp>(`/api/history?${p}`);
    },
    enabled: !!effectiveDate,
  });

  const total = history.data?.total ?? 0;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">历史回放</h1>

      <div className="flex flex-wrap items-center gap-2">
        <div className="w-44">
          <Select value={effectiveDate} onValueChange={(v) => { setDate(v); setOffset(0); }}>
            <SelectTrigger size="sm">
              <SelectValue placeholder={dates.isLoading ? "加载中…" : "选择日期"} />
            </SelectTrigger>
            <SelectContent>
              {(dates.data?.dates ?? []).map((d) => (
                <SelectItem key={d} value={d}>{d}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Input
          className="h-8 w-40 font-mono text-xs"
          placeholder="rid 过滤（链路回放）"
          value={rid}
          onChange={(e) => { setRid(e.target.value); setOffset(0); }}
        />
        <span className="text-xs text-muted-foreground">
          {history.data ? `共 ${total} 条` : ""}
        </span>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm" variant="outline" disabled={offset === 0 || !history.data}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            上一页
          </Button>
          <Button
            size="sm" variant="outline"
            disabled={!history.data || offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            下一页
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-15rem)]">
            <div className="space-y-1 p-4 font-mono text-xs">
              {history.isLoading && (
                <div className="space-y-2 p-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-4 w-1/2" />
                  <Skeleton className="h-4 w-2/3" />
                </div>
              )}
              {history.data && history.data.rows.length === 0 && (
                <div className="py-16 text-center text-muted-foreground">
                  该条件下无记录
                </div>
              )}
              {history.data?.rows.flatMap((r, i) =>
                renderRecord(r).map((row, j) => (
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
