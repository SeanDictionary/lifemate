"use client";
/** 记忆浏览器：记忆 / 好奇 / 需求 / 提醒 / 图片（只读） */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { apiGet, authedUrl } from "@/lib/api";
import type { MemoryResp } from "@/lib/types";
import { fmtDate } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";

const TABS = [
  { key: "episodes", label: "记忆" },
  { key: "curiosity", label: "好奇" },
  { key: "needs", label: "需求" },
  { key: "reminders", label: "提醒" },
  { key: "images", label: "图片" },
] as const;

const FIELDS: Record<string, string[]> = {
  episodes: ["summary", "source", "ts"],
  curiosity: ["topic", "status", "result", "ts"],
  needs: ["what", "why", "status", "ts"],
  reminders: ["intent", "due_at", "repeat", "ts"],
};

function fmtSize(n: number): string {
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)}MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${n}B`;
}

function Rows({ table }: { table: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["memory", table],
    queryFn: () => apiGet<MemoryResp>(`/api/memory/${table}?limit=50`),
    refetchInterval: 3_000,
  });

  if (isLoading)
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-3/4" />
      </div>
    );
  if (!data?.rows?.length)
    return <div className="py-12 text-center text-sm text-muted-foreground">（空）</div>;

  return (
    <div className="divide-y">
      {data.rows.map((r, i) => (
        <div key={i} className="flex flex-wrap items-baseline gap-2 px-4 py-2.5 text-sm">
          {FIELDS[table].map((f) => {
            const v = r[f];
            if (v === undefined || v === null || v === "") return null;
            const isTs = f === "ts" || f === "due_at";
            if (isTs)
              return (
                <span key={f} className="ml-auto font-mono text-xs text-muted-foreground">
                  {fmtDate(Number(v))}
                </span>
              );
            const primary = f === "summary" || f === "topic" || f === "what" || f === "intent";
            return primary ? (
              <span key={f} className="min-w-0 flex-1 break-all">{String(v)}</span>
            ) : (
              <Badge key={f} variant="outline" className="text-[11px]">{String(v)}</Badge>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/** 图片行：描述 + 徽标 + 时间 + 「>」展开看原图（内容寻址，token 走查询参数） */
function ImageRow({ r }: { r: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const hash = String(r.hash ?? "");
  const desc = String(r.description ?? "");
  const hit = Number(r.hit_count ?? 0);
  const size = Number(r.size ?? 0);
  const lastSeen = Number(r.last_seen ?? 0);
  const firstSeen = Number(r.first_seen ?? 0);

  return (
    <div className="px-4 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 break-all">{desc || "（未描述）"}</span>
        {hit > 0 && <Badge variant="outline" className="text-[11px]">看过{hit}次</Badge>}
        <Badge variant="outline" className="text-[11px]">{fmtSize(size)}</Badge>
        <span className="font-mono text-xs text-muted-foreground">{fmtDate(lastSeen)}</span>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label={open ? "收起图片" : "查看图片"}
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronRight
            className={`size-4 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
          />
        </Button>
      </div>
      {open && hash && (
        <div className="mt-2 space-y-1">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={authedUrl(`/api/memory/image/${hash}`)}
            alt={desc || hash}
            loading="lazy"
            className="max-h-96 rounded-md border"
          />
          <div className="font-mono text-[11px] text-muted-foreground">
            {hash.slice(0, 12)}… · 首次 {fmtDate(firstSeen)}
          </div>
        </div>
      )}
    </div>
  );
}

function ImageRows() {
  const { data, isLoading } = useQuery({
    queryKey: ["memory", "images"],
    queryFn: () => apiGet<MemoryResp>(`/api/memory/images?limit=50`),
    refetchInterval: 3_000,
  });

  if (isLoading)
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-3/4" />
      </div>
    );
  if (!data?.rows?.length)
    return <div className="py-12 text-center text-sm text-muted-foreground">（还没有看过图）</div>;

  return (
    <div className="divide-y">
      {data.rows.map((r, i) => (
        <ImageRow key={String(r.hash ?? i)} r={r} />
      ))}
    </div>
  );
}

export default function MemoryPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">记忆</h1>
      <Card>
        <CardContent className="p-0">
          <Tabs defaultValue="episodes">
            <div className="border-b px-2">
              <TabsList className="h-10 bg-transparent">
                {TABS.map((t) => (
                  <TabsTrigger key={t.key} value={t.key}>{t.label}</TabsTrigger>
                ))}
              </TabsList>
            </div>
            {TABS.map((t) => (
              <TabsContent key={t.key} value={t.key} className="m-0">
                <ScrollArea className="h-[calc(100vh-16rem)]">
                  {t.key === "images" ? <ImageRows /> : <Rows table={t.key} />}
                </ScrollArea>
              </TabsContent>
            ))}
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
