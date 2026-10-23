"use client";
/** 技能：知情层清单（skills/ + 插件 SKILL.md）· 点击卡片弹窗看全文 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { SkillDetailResp, SkillsResp } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faBookOpen, faXmark } from "@fortawesome/free-solid-svg-icons";

/** 来源配色：内置灰 / 插件蓝（skills/ 第三方不标记） */
const SRC_STYLE: Record<string, string> = {
  内置: "border-slate-500/30 bg-slate-500/10 text-slate-400",
};

/** 全文弹窗（TokenGate 同款遮罩模式：无 radix-dialog 依赖） */
function SkillModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["skill", name],
    queryFn: () => apiGet<SkillDetailResp>(`/api/skills/${encodeURIComponent(name)}`),
  });
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-[52rem] max-w-full flex-col rounded-xl border bg-card shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b px-5 py-3">
          <FontAwesomeIcon icon={faBookOpen} className="text-muted-foreground" />
          <span className="font-mono text-sm font-semibold">{name}</span>
          {data?.source ? (
            <Badge
              variant="outline"
              className={`text-[10px] ${SRC_STYLE[data.source] ?? "border-blue-500/30 bg-blue-500/10 text-blue-400"}`}
            >
              {data.source}
            </Badge>
          ) : null}
          <span className="text-xs text-muted-foreground">
            {data ? `${data.content.length} 字` : ""}
          </span>
          <Button variant="ghost" size="sm" className="ml-auto h-7 px-2" onClick={onClose}>
            <FontAwesomeIcon icon={faXmark} />
          </Button>
        </div>
        <ScrollArea className="flex-1">
          <div className="px-5 py-4">
            {isLoading || !data ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
              </div>
            ) : (
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                {data.content}
              </pre>
            )}
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const [open, setOpen] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["skills"],
    queryFn: () => apiGet<SkillsResp>("/api/skills"),
  });

  const skills = data?.skills ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">技能</h1>
        <span className="text-xs text-muted-foreground">
          {data ? `共 ${skills.length} 份 · 点击查看全文` : "加载中…"}
        </span>
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-5/6" />
        </div>
      )}

      <div className="grid gap-2 md:grid-cols-2">
        {skills.map((s) => (
          <Card
            key={s.name}
            className="cursor-pointer transition-colors hover:border-primary/40"
            onClick={() => setOpen(s.name)}
          >
            <CardContent className="p-3">
              <div className="flex items-center gap-2">
                <FontAwesomeIcon
                  icon={faBookOpen}
                  className="text-xs text-muted-foreground"
                />
                <span className="font-mono text-sm font-medium">{s.name}</span>
                {s.source ? (
                  <Badge
                    variant="outline"
                    className={`text-[10px] ${SRC_STYLE[s.source] ?? "border-blue-500/30 bg-blue-500/10 text-blue-400"}`}
                  >
                    {s.source}
                  </Badge>
                ) : null}
              </div>
              {s.description && (
                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                  {s.description}
                </p>
              )}
            </CardContent>
          </Card>
        ))}
        {data && skills.length === 0 && (
          <div className="py-16 text-center text-sm text-muted-foreground md:col-span-2">
            暂无技能（skills/ 目录与插件 SKILL.md）
          </div>
        )}
      </div>

      {open && <SkillModal name={open} onClose={() => setOpen("")} />}
    </div>
  );
}
