"use client";
/** 记忆浏览器：记忆 / 兴趣 / 需求 / 提醒 / 图片（只读；行摘要 + 点击看完整记录） */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faImage, faXmark } from "@fortawesome/free-solid-svg-icons";
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
  { key: "curiosity", label: "兴趣" },
  { key: "needs", label: "需求" },
  { key: "reminders", label: "提醒" },
  { key: "images", label: "图片" },
] as const;

/** 行摘要：主字段截断上限 */
const BRIEF_MAX = 100;
/** 胶囊字数上限（长文本不配胶囊——进详情弹窗） */
const BADGE_MAX = 16;

/** 每表的主字段（行摘要文本）与胶囊字段（短枚举），其余字段进详情弹窗 */
const PRIMARY: Record<string, string> = {
  episodes: "summary",
  curiosity: "topic",
  needs: "what",
  reminders: "intent",
};
const BADGES: Record<string, string[]> = {
  episodes: ["kind"],
  curiosity: ["status"],
  needs: ["status"],
  reminders: ["status", "repeat"],
};
/** 详情弹窗字段（显示名 → 字段名），按表排序 */
const DETAILS: Record<string, [string, string][]> = {
  episodes: [["类型", "kind"], ["来源", "source"], ["记录于", "ts"]],
  curiosity: [["状态", "status"], ["结果", "result"], ["来源", "source"], ["记录于", "ts"]],
  needs: [["状态", "status"], ["为什么", "why"], ["提出于", "raised_at"], ["记录于", "ts"]],
  reminders: [["状态", "status"], ["重复", "repeat"], ["到点", "due_at"], ["兑现于", "done_at"], ["记录于", "ts"]],
};
const TIME_FIELDS = new Set(["ts", "due_at", "done_at", "raised_at"]);

/** 状态中文化（徽标可读） */
const STATUS_LABEL: Record<string, string> = {
  filed: "待提",
  raised: "已提出",
  pending: "进行中",
  done: "已完成",
};

function brief(v: unknown, max: number = BRIEF_MAX): string {
  const s = String(v ?? "");
  return s.length > max ? s.slice(0, max) + "…" : s;
}

function badgeText(v: unknown): string {
  return brief(STATUS_LABEL[String(v)] ?? String(v), BADGE_MAX);
}

function fmtSize(n: number): string {
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)}MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${n}B`;
}

/** 记录详情弹窗（图片弹窗同款遮罩模式）：完整主文 + 全字段 */
function RecordModal({ table, r, onClose }: { table: string; r: Record<string, unknown>; onClose: () => void }) {
  const primary = String(r[PRIMARY[table]] ?? "") || "（无内容）";
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
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">
            {brief(primary, 60)}
          </span>
          <Button variant="ghost" size="sm" className="h-7 px-2" onClick={onClose}>
            <FontAwesomeIcon icon={faXmark} />
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="space-y-4 px-5 py-4">
            <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">{primary}</div>
            <div className="space-y-2 border-t pt-3">
              {DETAILS[table].map(([label, key]) => {
                const v = r[key];
                if (v === undefined || v === null || v === "" || (key === "repeat" && v === "none")) return null;
                return (
                  <div key={key} className="flex gap-3 text-sm">
                    <span className="shrink-0 text-muted-foreground">{label}</span>
                    <span className="min-w-0 break-all">
                      {TIME_FIELDS.has(key) ? (
                        <span className="font-mono text-xs text-muted-foreground">{fmtDate(Number(v))}</span>
                      ) : key === "status" ? (
                        <Badge variant="outline" className="text-[11px]">{STATUS_LABEL[String(v)] ?? String(v)}</Badge>
                      ) : (
                        String(v)
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 通用记录行：主字段截断 + 短枚举胶囊 + 时间 + 「>」弹窗看全文 */
function Row({ table, r }: { table: string; r: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const primary = String(r[PRIMARY[table]] ?? "") || "（无内容）";
  return (
    <div className="px-4 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 break-all">{brief(primary)}</span>
        {(BADGES[table] ?? []).map((f) => {
          const v = r[f];
          if (v === undefined || v === null || v === "" || (f === "repeat" && v === "none")) return null;
          return (
            <Badge key={f} variant="outline" className="text-[11px]">{badgeText(v)}</Badge>
          );
        })}
        <span className="font-mono text-xs text-muted-foreground">{fmtDate(Number(r.ts ?? 0))}</span>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label="查看完整记录"
          onClick={() => setOpen(true)}
        >
          <ChevronRight className="size-4 text-muted-foreground" />
        </Button>
      </div>
      {open && <RecordModal table={table} r={r} onClose={() => setOpen(false)} />}
    </div>
  );
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
        <Row key={String(r.id ?? r.hash ?? i)} table={table} r={r} />
      ))}
    </div>
  );
}

/** 图片详情弹窗（SkillModal 同款遮罩模式：无 radix-dialog 依赖）：
 *  原图 + 完整描述 + 元信息；点遮罩/× 关闭 */
function ImageModal({ r, onClose }: { r: Record<string, unknown>; onClose: () => void }) {
  const hash = String(r.hash ?? "");
  const desc = String(r.description ?? "");
  const hit = Number(r.hit_count ?? 0);
  const size = Number(r.size ?? 0);
  const firstSeen = Number(r.first_seen ?? 0);
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
          <FontAwesomeIcon icon={faImage} className="text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">
            {desc.length > 40 ? desc.slice(0, 40) + "…" : desc || "（未描述）"}
          </span>
          {hit > 0 && (
            <Badge variant="outline" className="text-[10px]">看过{hit}次</Badge>
          )}
          <Badge variant="outline" className="text-[10px]">{fmtSize(size)}</Badge>
          <Button variant="ghost" size="sm" className="ml-auto h-7 px-2" onClick={onClose}>
            <FontAwesomeIcon icon={faXmark} />
          </Button>
        </div>
        {/* 原生滚动：radix viewport 的百分比高度在 flex 收缩语境下不解析
            （viewport 恒等于内容高→永不滚），改用 overflow-y-auto 一劳永逸 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="space-y-3 px-5 py-4">
            {hash && (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={authedUrl(`/api/memory/image/${hash}`)}
                alt={desc || hash}
                loading="lazy"
                className="mx-auto max-h-[55vh] rounded-md border object-contain"
              />
            )}
            <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
              {desc || "（未描述）"}
            </div>
            <div className="font-mono text-[11px] text-muted-foreground">
              {hash.slice(0, 12)}… · 首次 {fmtDate(firstSeen)}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 图片行：单行描述（100 字截断）+ 徽标 + 时间 + 「>」弹窗看原图与全文 */
function ImageRow({ r }: { r: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const hash = String(r.hash ?? "");
  const desc = String(r.description ?? "");
  const hit = Number(r.hit_count ?? 0);
  const size = Number(r.size ?? 0);
  const lastSeen = Number(r.last_seen ?? 0);
  const briefDesc = desc.length > 100 ? desc.slice(0, 100) + "…" : desc;

  return (
    <div className="px-4 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 break-all">{briefDesc || "（未描述）"}</span>
        {hit > 0 && <Badge variant="outline" className="text-[11px]">看过{hit}次</Badge>}
        <Badge variant="outline" className="text-[11px]">{fmtSize(size)}</Badge>
        <span className="font-mono text-xs text-muted-foreground">{fmtDate(lastSeen)}</span>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label="查看图片与完整描述"
          onClick={() => setOpen(true)}
        >
          <ChevronRight className="size-4 text-muted-foreground" />
        </Button>
      </div>
      {open && <ImageModal r={r} onClose={() => setOpen(false)} />}
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
