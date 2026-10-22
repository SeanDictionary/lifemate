"use client";
/** 设置：config 只读展示 + 分区表单编辑 + 保存/重载（密钥掩码，env 锁定标注） */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";
import type { ApplyResult, ConfigResp, Receipt } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

/** 分区（键名即文档；密钥/布尔/数字的输入形态按默认值类型判断） */
const GROUPS: { title: string; keys: string[] }[] = [
  { title: "模型", keys: ["llm_base_url", "llm_model", "llm_api_key", "llm_max_retries", "llm_retry_backoff_s", "llm_fail_alert_streak"] },
  { title: "内核", keys: ["max_turns", "turns_rest_nudge", "tool_timeout_s", "compact_threshold_tokens", "keep_recent_tokens"] },
  { title: "网络服务", keys: ["server_host", "server_port", "server_token", "server_static_dir"] },
  { title: "观测与档案", keys: ["observe_retention_days", "observe_max_field_chars", "session_keep_rotated"] },
  { title: "系统日志", keys: ["log_level", "log_max_bytes", "log_backup_count"] },
  { title: "守护", keys: ["watchdog_interval_s", "max_run_s", "reminder_poll_s", "queue_max_steer", "backup_interval_s", "backup_keep", "config_poll_s"] },
];

/** 密钥字段（镜像后端 _SECRET_KEYS——精确集合，不用子串启发：
 *  compact_threshold_tokens 这类"token 数量"键不是凭证） */
const SECRET_KEYS = new Set(["llm_api_key", "server_token"]);

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["config"],
    queryFn: () => apiGet<ConfigResp>("/api/config"),
  });
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (data) {
      const d: Record<string, string> = {};
      for (const [k, v] of Object.entries(data.config)) {
        if (k === "plugins" || k.startsWith("soul_") || k.startsWith("skills_")) continue;
        d[k] = v === null || v === undefined ? "" : String(v);
      }
      setDraft(d);
    }
  }, [data]);

  const secretOf = (k: string) => SECRET_KEYS.has(k);
  const envLocked = (k: string) =>
    (data?.config?.env_locked as string[] | undefined)?.includes?.(k) ?? false;

  const save = async () => {
    if (!data) return;
    // 只提交有变化的键；掩码形态原样回传（后端跳过 = 未修改）
    const updates: Record<string, string> = {};
    for (const [k, v] of Object.entries(draft)) {
      if (String(data.config[k] ?? "") !== v) updates[k] = v;
    }
    if (!Object.keys(updates).length) return toast.info("没有改动");
    try {
      const r = await apiPost<ApplyResult>("/api/config", updates);
      if (r.skipped_masked?.length)
        toast.info(`密钥未修改（掩码回传跳过）：${r.skipped_masked.join(", ")}`);
      if (r.unknown?.length)
        toast.warning(`未知键被拒绝：${r.unknown.join(", ")}`);
      toast.success(`已保存 ${r.applied.length} 项${r.env_locked?.length ? "（注意：env 锁定键重启后被环境变量覆盖）" : ""}`);
      qc.invalidateQueries({ queryKey: ["config"] });
    } catch (e) {
      toast.error(`保存失败：${(e as Error).message}`);
    }
  };

  const reload = async () => {
    try {
      const r = await apiPost<Receipt & { changed: string[] }>("/api/config/reload");
      toast.success(r.changed?.length ? `重载生效：${r.changed.join(", ")}` : "重载完成（无变化）");
      qc.invalidateQueries({ queryKey: ["config"] });
    } catch (e) {
      toast.error(`重载失败：${(e as Error).message}`);
    }
  };

  if (isLoading)
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full" />
      </div>
    );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">设置</h1>
        <span className="text-xs text-muted-foreground">保存即热生效并写入 config.json</span>
        <div className="ml-auto flex gap-2">
          <Button variant="outline" size="sm" onClick={reload}>从文件重载</Button>
          <Button size="sm" onClick={save}>保存</Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {GROUPS.map((g) => (
          <Card key={g.title}>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">{g.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {g.keys
                .filter((k) => k in draft)
                .map((k) => (
                  <div key={k} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Label htmlFor={k} className="font-mono text-xs">{k}</Label>
                      {secretOf(k) && <Badge variant="outline" className="text-[10px]">密钥</Badge>}
                      {envLocked(k) && <Badge variant="outline" className="text-[10px]">env 锁定</Badge>}
                    </div>
                    <Input
                      id={k}
                      className="h-8 font-mono text-xs"
                      value={draft[k] ?? ""}
                      onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                    />
                  </div>
                ))}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
