"use client";
/** token 门：401 时全屏输入（无账号体系；token 来自 config.json 的 server_token） */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { setToken } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function TokenGate() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const qc = useQueryClient();

  useEffect(() => {
    const onUnauthorized = () => setOpen(true);
    window.addEventListener("lm:unauthorized", onUnauthorized);
    return () => window.removeEventListener("lm:unauthorized", onUnauthorized);
  }, []);

  if (!open) return null;

  const save = () => {
    if (!value.trim()) return;
    setToken(value);
    window.dispatchEvent(new CustomEvent("lm:token-changed"));
    setOpen(false);
    setValue("");
    qc.invalidateQueries();
    toast.success("token 已保存，重连中");
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-background/80 backdrop-blur-sm">
      <div className="w-[24rem] rounded-xl border bg-card p-6 shadow-lg">
        <h2 className="text-lg font-semibold">需要访问令牌</h2>
        <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
          令牌在服务端的 <code className="font-mono text-xs">config.json</code> 的{" "}
          <code className="font-mono text-xs">server_token</code> 字段。
          清空后服务端会自动换新值。
        </p>
        <div className="mt-4 space-y-2">
          <Label htmlFor="token">Token</Label>
          <Input
            id="token"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
            placeholder="粘贴 server_token"
            autoFocus
          />
        </div>
        <Button className="mt-4 w-full" onClick={save}>
          连接
        </Button>
      </div>
    </div>
  );
}
