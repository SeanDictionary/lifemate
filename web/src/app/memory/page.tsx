"use client";
/** 记忆浏览器：记忆 / 好奇 / 需求 / 提醒（只读） */
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { MemoryResp } from "@/lib/types";
import { fmtDate } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const TABS = [
  { key: "episodes", label: "记忆" },
  { key: "curiosity", label: "好奇" },
  { key: "needs", label: "需求" },
  { key: "reminders", label: "提醒" },
] as const;

const FIELDS: Record<string, string[]> = {
  episodes: ["summary", "source", "ts"],
  curiosity: ["topic", "status", "result", "ts"],
  needs: ["what", "why", "status", "ts"],
  reminders: ["intent", "due_at", "repeat", "ts"],
};

function Rows({ table }: { table: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["memory", table],
    queryFn: () => apiGet<MemoryResp>(`/api/memory/${table}?limit=50`),
    refetchInterval: 30_000,
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
                  <Rows table={t.key} />
                </ScrollArea>
              </TabsContent>
            ))}
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
