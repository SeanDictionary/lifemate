"use client";
/** 设置：schema 驱动渲染（分组/类型/说明/密钥/重启/env 锁定全部来自
 * /api/config/schema，零硬编码——后端加配置键前端自动出现）。
 * 插件设置按 plugins.<name>.<key> 点路径读写（后端按插件声明校验）。 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";
import type { ApplyResult, ConfigResp, ConfigSchemaResp, Receipt, SchemaField } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

/** 草稿值：主键 + 插件点路径（plugins.<name>.<key>）统一平铺 */
type Draft = Record<string, string | boolean>;

function toDraftValue(v: unknown): string | boolean {
  if (typeof v === "boolean") return v;
  if (Array.isArray(v)) return v.join(","); // 数组 → 逗号分隔编辑
  return v === null || v === undefined ? "" : String(v);
}

function fromDraftValue(v: string | boolean, f: SchemaField): unknown {
  if (f.type === "bool") return Boolean(v);
  if (f.type === "number") return Number(v);
  if (f.type === "array")
    return String(v).split(",").map((s) => s.trim()).filter(Boolean);
  return String(v);
}

/** 单个字段渲染：按 schema 类型选控件，元数据出角标（id 用全路径防跨插件重名） */
function FieldRow({
  id, label, f, value, envLocked, onChange,
}: {
  id: string; label: string; f: SchemaField; value: string | boolean | undefined;
  envLocked: boolean; onChange: (v: string | boolean) => void;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Label htmlFor={id} className="font-mono text-xs">{label}</Label>
        {f.secret && <Badge variant="outline" className="text-[10px]">密钥</Badge>}
        {f.restart && <Badge variant="outline" className="text-[10px]">需重启</Badge>}
        {envLocked && <Badge variant="outline" className="text-[10px]">env 锁定</Badge>}
      </div>
      {f.description && (
        <p className="text-[11px] leading-tight text-muted-foreground">{f.description}</p>
      )}
      {f.type === "bool" ? (
        <div className="flex items-center gap-2 pt-1">
          <Switch id={id} checked={Boolean(value)} onCheckedChange={onChange} />
          <span className="text-xs text-muted-foreground">{value ? "开" : "关"}</span>
        </div>
      ) : (
        <Input
          id={id}
          type={f.secret ? "password" : f.type === "number" ? "number" : "text"}
          step={f.type === "number" ? "any" : undefined}
          className="h-8 font-mono text-xs"
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["config"],
    queryFn: () => apiGet<ConfigResp>("/api/config"),
  });
  const { data: schema } = useQuery({
    queryKey: ["configSchema"],
    queryFn: () => apiGet<ConfigSchemaResp>("/api/config/schema"),
  });
  const [draft, setDraft] = useState<Draft>({});

  useEffect(() => {
    if (!data || !schema) return;
    const d: Draft = {};
    for (const [k, v] of Object.entries(data.config)) {
      if (k === "plugins") continue;
      d[k] = toDraftValue(v);
    }
    // 插件段 → 点路径平铺（值经后端掩码，密钥显示 *** 尾）
    const plug = (data.config.plugins ?? {}) as Record<string, Record<string, unknown>>;
    for (const [name, section] of Object.entries(plug)) {
      if (!section || typeof section !== "object") continue;
      for (const [fk, v] of Object.entries(section)) {
        d[`plugins.${name}.${fk}`] = toDraftValue(v);
      }
    }
    setDraft(d);
  }, [data, schema]);

  /** 主配置按声明分组（保持声明顺序）；未分组键归"其他" */
  const groups = useMemo(() => {
    if (!schema) return [];
    const acc: { title: string; items: [string, SchemaField][] }[] = [];
    const idx = new Map<string, number>();
    for (const [k, f] of Object.entries(schema.fields)) {
      if (k === "plugins") continue;
      const title = f.group || "其他";
      if (!idx.has(title)) {
        idx.set(title, acc.length);
        acc.push({ title, items: [] });
      }
      acc[idx.get(title)!].items.push([k, f]);
    }
    return acc;
  }, [schema]);

  const envLocked = (k: string) =>
    schema?.env_set?.includes(k) ?? false;

  const save = async () => {
    if (!data) return;
    const updates: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(draft)) {
      const orig = (() => {
        if (k.startsWith("plugins.")) {
          const [, name, fk] = k.split(".");
          const plug = (data.config.plugins ?? {}) as Record<string, Record<string, unknown>>;
          return toDraftValue(plug?.[name]?.[fk]);
        }
        return toDraftValue(data.config[k]);
      })();
      if (orig !== v) {
        const f = k.startsWith("plugins.")
          ? schema?.plugins?.[k.split(".")[1]]?.[k.split(".")[2]]
          : schema?.fields?.[k];
        if (f) updates[k] = fromDraftValue(v, f);
      }
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
        <span className="text-xs text-muted-foreground">保存即热生效并写入 config.json（标&quot;需重启&quot;的除外）</span>
        <div className="ml-auto flex gap-2">
          <Button variant="outline" size="sm" onClick={reload}>从文件重载</Button>
          <Button size="sm" onClick={save}>保存</Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {groups.map((g) => (
          <Card key={g.title}>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">{g.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {g.items.map(([k, f]) => (
                <FieldRow
                  key={k} id={k} label={k} f={f} envLocked={envLocked(k)}
                  value={draft[k]}
                  onChange={(v) => setDraft((d) => ({ ...d, [k]: v }))}
                />
              ))}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* 插件设置：按已声明插件分组（含未启用的——值来自 config.json 预设段） */}
      {schema && Object.keys(schema.plugins).length > 0 && (
        <>
          <h2 className="pt-2 text-sm font-semibold">插件</h2>
          <div className="grid gap-4 md:grid-cols-2">
            {Object.entries(schema.plugins).map(([name, fields]) => (
              <Card key={name}>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm font-mono">{name}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {Object.entries(fields).map(([fk, f]) => {
                    const k = `plugins.${name}.${fk}`;
                    return (
                      <FieldRow
                        key={k} id={k} label={fk} f={f} envLocked={false}
                        value={draft[k]}
                        onChange={(v) => setDraft((d) => ({ ...d, [k]: v }))}
                      />
                    );
                  })}
                </CardContent>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
