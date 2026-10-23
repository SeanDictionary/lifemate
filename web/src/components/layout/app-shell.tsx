"use client";
/** 侧栏 + 主体布局；插件区动态显隐（/api/plugins 声明）。
 * 菜单图标：Font Awesome 本地打包（tree-shaking，只含用到的图标，零 CDN）。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faGaugeHigh,
  faClockRotateLeft,
  faComments,
  faBrain,
  faChartColumn,
  faGear,
  faGears,
  faBookOpen,
  faXmark,
  faCircleDot,
  faPuzzlePiece,
  type IconDefinition,
} from "@fortawesome/free-solid-svg-icons";
import { apiGet } from "@/lib/api";
import type { PluginsResp, StatusResp } from "@/lib/types";
import { fmtUptime } from "@/lib/format";
import { ConnDot, FloatingThemeToggle } from "./theme-toggle";
import { Orb } from "./orb";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

/** 图标注册表（Font Awesome 名 → 组件；插件面板 icon 字段同用此表） */
export const FA_ICONS: Record<string, IconDefinition> = {
  gauge: faGaugeHigh,
  history: faClockRotateLeft,
  comments: faComments,
  brain: faBrain,
  chart: faChartColumn,
  gear: faGear,
  circle: faCircleDot,
  puzzle: faPuzzlePiece,
};

interface NavItem {
  href: string;
  label: string;
  icon: IconDefinition;
}

const NAV: NavItem[] = [
  { href: "/", label: "概览", icon: faGaugeHigh },
  { href: "/history", label: "历史", icon: faClockRotateLeft },
  { href: "/memory", label: "记忆", icon: faBrain },
  { href: "/tools", label: "工具", icon: faGears },
  { href: "/skills", label: "技能", icon: faBookOpen },
  { href: "/chat", label: "对话", icon: faComments },
  { href: "/usage", label: "用量", icon: faChartColumn },
  { href: "/settings", label: "设置", icon: faGear },
];

function NavLink({ href, label, icon }: NavItem) {
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
      <span className="grid w-4 shrink-0 place-items-center text-muted-foreground">
        <FontAwesomeIcon icon={icon} />
      </span>
      {label}
    </Link>
  );
}

function Brand() {
  const { data: st } = useQuery({
    queryKey: ["status"],
    queryFn: () => apiGet<StatusResp>("/api/status"),
    refetchInterval: 3_000,
    select: (d) => d.version,
  });
  return (
    <div className="flex items-center gap-3 px-4 py-4">
      <Orb size={38} />
      <div className="min-w-0">
        <div className="text-sm font-semibold leading-tight">lifemate</div>
        <div className="text-[11px] text-muted-foreground">
          控制面板{st ? ` v${st}` : ""}
        </div>
      </div>
    </div>
  );
}

function Vitals() {
  const { data: st } = useQuery({
    queryKey: ["status"],
    queryFn: () => apiGet<StatusResp>("/api/status"),
    refetchInterval: 3_000,
    select: (d) => d.uptime_s,
  });
  return (
    <div className="flex items-center gap-2 px-3 pb-3 text-xs text-muted-foreground">
      <ConnDot />
      <span>{st !== undefined ? `存活 ${fmtUptime(st)}` : "连接中…"}</span>
    </div>
  );
}

function PluginNav() {
  const { data } = useQuery({
    queryKey: ["plugins"],
    queryFn: () => apiGet<PluginsResp>("/api/plugins"),
    refetchInterval: 3_000,
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
          icon={FA_ICONS[p.panel!.icon] ?? faPuzzlePiece}
        />
      ))}
    </>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <FloatingThemeToggle />
      <div className="flex min-h-screen">
        <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r bg-sidebar">
          <Brand />
          <Separator />
          <nav className="flex-1 space-y-0.5 overflow-y-auto py-2">
            {NAV.map((n) => (
              <NavLink key={n.href} {...n} />
            ))}
            <PluginNav />
          </nav>
          <Separator />
          <div className="pt-3">
            <Vitals />
          </div>
        </aside>
        <main className="min-w-0 flex-1">
          <div className="mx-auto max-w-5xl px-6 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
