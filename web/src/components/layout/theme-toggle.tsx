"use client";
/** 主题切换（悬浮右上角）+ 连接状态点（SSE，供 Vitals 用） */
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { useSse } from "@/hooks/use-sse";
import { Button } from "@/components/ui/button";

export function FloatingThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return (
    <div className="fixed top-4 right-4 z-40">
      <Button
        variant="outline"
        size="icon"
        className="rounded-full shadow-md"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        title="切换深浅主题"
      >
        {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
      </Button>
    </div>
  );
}

export function ConnDot() {
  const { status } = useSse();
  const color =
    status === "open"
      ? "bg-emerald-500"
      : status === "connecting"
        ? "bg-amber-500 animate-pulse"
        : "bg-muted-foreground/40";
  return (
    <span className="relative flex size-2">
      <span className={`absolute inline-flex size-full rounded-full ${color}`} />
    </span>
  );
}
