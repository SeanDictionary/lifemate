"use client";
/** 侧栏 + 主体布局；插件区动态显隐（/api/plugins 声明） */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { PluginsResp, StatusResp } from "@/lib/types";
import { fmtUptime } from "@/lib/format";
import { ConnDot, ThemeToggle } from "./theme-toggle";
import { Orb } from "./orb";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "概览", icon: "🏠" },
  { href: "/events", label: "事件流", icon: "🌅" },
  { href: "/history", label: "历史回放", icon: "📂" },
  { href: "/chat", label: "对话", icon: "💬" },
  { href: "/memory", label: "记忆", icon: "🧠" },
  { href: "/usage", label: "用量", icon: "📊" },
  { href: "/settings", label: "设置", icon: "⚙️" },
];

function NavLink({ href, label, icon }: { href: string; label: string; icon: string }) {
  const pathname = usePathname();
  const active = pathname === href;
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
          : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
      )}
    >
      <span className="text-base leading-none">{icon}</span>
      {label}
    </Link>
  );
}

function Vitals() {
  const { data: st } = useQuery({
    queryKey: ["status"],
    queryFn: () => apiGet<StatusResp>("/api/status"),
    refetchInterval: 10_000,
  });
  return (
    <div className="flex items-center gap-2 px-3 pb-3 text-xs text-muted-foreground">
      <ConnDot />
      <span>{st ? `存活 ${fmtUptime(st.uptime_s)}` : "连接中…"}</span>
      {st && <span className="ml-auto font-mono">v{st.version}</span>}
    </div>
  );
}

function PluginNav() {
  const { data } = useQuery({
    queryKey: ["plugins"],
    queryFn: () => apiGet<PluginsResp>("/api/plugins"),
    refetchInterval: 30_000,
  });
  const withPanel = (data?.plugins ?? []).filter((p) => p.panel);
  if (!withPanel.length) return null;
  return (
    <>
      <div className="px-3 pt-3 pb-1 text-[11px] uppercase tracking-wider text-muted-foreground/60">
        插件
      </div>
      {withPanel.map((p) => (
        <NavLink
          key={p.name}
          href={`/plugin?name=${p.name}`}
          label={p.panel!.title}
          icon={p.panel!.icon}
        />
      ))}
    </>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r bg-sidebar">
        <div className="flex items-center gap-3 px-4 py-4">
          <Orb size={38} />
          <div>
            <div className="text-sm font-semibold leading-tight">lifemate</div>
            <div className="text-[11px] text-muted-foreground">数字生命观测台</div>
          </div>
        </div>
        <Separator />
        <nav className="flex-1 space-y-0.5 overflow-y-auto py-2">
          {NAV.map((n) => (
            <NavLink key={n.href} {...n} />
          ))}
          <PluginNav />
        </nav>
        <Separator />
        <div className="flex items-center justify-between pr-2 pl-3 pt-2">
          <Vitals />
        </div>
        <div className="flex justify-end pb-2 pr-2">
          <ThemeToggle />
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-5xl px-6 py-6">{children}</div>
      </main>
    </div>
  );
}
