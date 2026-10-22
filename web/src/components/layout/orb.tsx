"use client";
/** 生命标识：呼吸光环——思考时呼吸快、休息时缓慢（暖色唯一强调） */
import { useEffect, useState } from "react";
import { useSse } from "@/hooks/use-sse";

export function Orb({ size = 44 }: { size?: number }) {
  const { events } = useSse();
  const [sleeping, setSleeping] = useState(false);

  useEffect(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const t = events[i].type;
      if (t === "rest") return setSleeping(true);
      if (t === "wake") return setSleeping(false);
    }
  }, [events]);

  return (
    <div
      className="relative grid shrink-0 place-items-center rounded-full"
      style={{ width: size, height: size }}
    >
      <div
        className="absolute inset-0 rounded-full lm-breathe"
        style={{
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--color-primary) 38%, transparent), transparent 72%)",
          filter: "blur(4px)",
          animationDuration: sleeping ? "6s" : "2.4s",
        }}
      />
      <div
        className="absolute inset-0 rounded-full border"
        style={{
          borderColor:
            "color-mix(in oklab, var(--color-primary) 45%, transparent)",
        }}
      />
      <span style={{ fontSize: size * 0.44 }}>{sleeping ? "🌙" : "🌅"}</span>
    </div>
  );
}
