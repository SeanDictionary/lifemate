"use client";
/** 插件面板：?name= 路由（静态导出用查询参数；已知插件有专属组件，其余回退提示） */
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { QQPanel } from "@/components/plugins/qq";

function Panel() {
  const params = useSearchParams();
  const name = params.get("name") ?? "";

  if (name === "qq") return <QQPanel />;
  return (
    <div className="py-16 text-center text-sm text-muted-foreground">
      {name ? `插件「${name}」声明了面板，但前端尚未内置其页面` : "缺少 ?name= 参数"}
    </div>
  );
}

export default function PluginPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <Panel />
    </Suspense>
  );
}
