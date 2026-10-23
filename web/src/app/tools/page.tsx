"use client";
/** 工具：全部已装载工具清单（来源筛选 + 搜索；默认按来源→名称排序） */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { ToolsResp, ToolInfo } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

/** 来源配色：内核灰 / 记忆紫 / 宿主青 / 插件蓝 */
const SRC_STYLE: Record<string, string> = {
  内核: "border-slate-500/30 bg-slate-500/10 text-slate-400",
  记忆: "border-violet-500/30 bg-violet-500/10 text-violet-400",
  宿主: "border-teal-500/30 bg-teal-500/10 text-teal-400",
};

/** 默认排序：内置来源在前（内核→记忆→宿主），插件按名，同来源内按工具名 */
const SRC_ORDER = ["内核", "记忆", "宿主"];

function srcRank(s: string): number {
  const i = SRC_ORDER.indexOf(s);
  return i >= 0 ? i : SRC_ORDER.length;
}

function ToolRow({ t }: { t: ToolInfo }) {
  const [open, setOpen] = useState(false);
  const props = (t.inputSchema?.properties ?? {}) as Record<
    string,
    { type?: string; description?: string }
  >;
  const required = new Set(t.inputSchema?.required ?? []);
  const paramCount = Object.keys(props).length;

  return (
    <Card className="pt-1 pb-1">
      <CardContent className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">{t.name}</span>
          <Badge
            variant="outline"
            className={`text-[10px] ${SRC_STYLE[t.source] ?? "border-blue-500/30 bg-blue-500/10 text-blue-400"}`}
          >
            {t.source}
          </Badge>
          {t.interruptible && (
            <Badge variant="outline" className="text-[10px] text-orange-400">
              可打断
            </Badge>
          )}
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            {paramCount} 参数
          </span>
        </div>
        {/* 描述 + 参数详情按钮同行：按钮沉底右浮，与描述末行对齐 */}
        <div className="mt-1 flex items-end gap-2">
          {t.description ? (
            <p className="flex-1 text-xs leading-relaxed text-muted-foreground">
              {t.description}
            </p>
          ) : (
            <span className="flex-1" />
          )}
          <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 shrink-0 px-2 text-[11px] text-muted-foreground"
              >
                {open ? "收起 ▲" : "参数 ▼"}
              </Button>
            </CollapsibleTrigger>
          </Collapsible>
        </div>
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleContent>
            {paramCount === 0 ? (
              <p className="py-2 text-xs text-muted-foreground">无参数</p>
            ) : (
              <div className="mt-1 space-y-1.5">
                {Object.entries(props).map(([pname, p]) => (
                  <div
                    key={pname}
                    className="rounded-md border border-border/60 bg-muted/30 px-2.5 py-1.5"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs">{pname}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {p.type ?? "any"}
                      </span>
                      {required.has(pname) && (
                        <Badge variant="outline" className="h-4 px-1 text-[9px]">
                          必填
                        </Badge>
                      )}
                    </div>
                    {p.description && (
                      <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                        {p.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  );
}

export default function ToolsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["tools"],
    queryFn: () => apiGet<ToolsResp>("/api/tools"),
  });
  const [src, setSrc] = useState("全部");
  const [q, setQ] = useState("");

  const tools = useMemo(() => {
    const list = [...(data?.tools ?? [])];
    list.sort(
      (a, b) =>
        srcRank(a.source) - srcRank(b.source) ||
        a.source.localeCompare(b.source) ||
        a.name.localeCompare(b.name),
    );
    return list;
  }, [data]);

  const sources = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of tools) counts.set(t.source, (counts.get(t.source) ?? 0) + 1);
    return [...counts.entries()].sort(
      (a, b) => srcRank(a[0]) - srcRank(b[0]) || a[0].localeCompare(b[0]),
    );
  }, [tools]);

  const shown = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return tools.filter(
      (t) =>
        (src === "全部" || t.source === src) &&
        (!kw ||
          t.name.toLowerCase().includes(kw) ||
          t.description.toLowerCase().includes(kw)),
    );
  }, [tools, src, q]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">工具</h1>
        <span className="text-xs text-muted-foreground">
          {data ? `共 ${tools.length} 个 · 展示 ${shown.length} 个` : "加载中…"}
        </span>
        <Input
          className="ml-auto h-8 w-52 font-mono text-xs"
          placeholder="搜索名称/描述…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {/* 来源筛选（全部 + 各来源，带计数） */}
      <Tabs value={src} onValueChange={setSrc}>
        <TabsList className="h-8 flex-wrap">
          <TabsTrigger value="全部" className="text-xs">
            全部 {tools.length}
          </TabsTrigger>
          {sources.map(([s, n]) => (
            <TabsTrigger key={s} value={s} className="text-xs">
              {s} {n}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-5/6" />
          <Skeleton className="h-16 w-3/4" />
        </div>
      )}

      <div className="space-y-2">
        {shown.map((t) => (
          <ToolRow key={t.name} t={t} />
        ))}
        {data && shown.length === 0 && (
          <div className="py-16 text-center text-sm text-muted-foreground">
            无匹配工具（换关键词或来源试试）
          </div>
        )}
      </div>
    </div>
  );
}
