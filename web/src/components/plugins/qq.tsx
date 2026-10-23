"use client";
/** QQ 插件面板：连接状态 + 会话清单（未读） + 聊天记录查看（不算心智已读） */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";

interface QQStatus {
  ok: boolean;
  connected: boolean;
  connected_since: number;
  bot_uin: number;
  listen_groups: unknown;
  unread: string;
}

interface QQChats {
  ok: boolean;
  chats: { chat_id: string; last_ts: number; unread: number }[];
}

interface QQLog {
  ok: boolean;
  count: number;
  rows: { ts: number; sender_id: number; sender_name: string; text: string }[];
}

function chatLabel(id: string): string {
  return id.replace("group_", "群 ").replace("private_", "私聊 ");
}

export function QQPanel() {
  const [chat, setChat] = useState("");

  const status = useQuery({
    queryKey: ["qq", "status"],
    queryFn: () => apiGet<QQStatus>("/api/plugins/qq/status"),
    refetchInterval: 3_000,
  });
  const chats = useQuery({
    queryKey: ["qq", "chats"],
    queryFn: () => apiGet<QQChats>("/api/plugins/qq/chats"),
    refetchInterval: 3_000,
  });
  const log = useQuery({
    queryKey: ["qq", "chatlog", chat],
    queryFn: () => apiGet<QQLog>(`/api/plugins/qq/chatlog?chat=${encodeURIComponent(chat)}&n=100`),
    enabled: !!chat,
  });

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">QQ</h1>

      {/* 连接状态 */}
      <Card className="pt-0 pb-0">
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 p-5 text-sm">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block size-2 rounded-full ${
                status.data?.connected ? "bg-emerald-500" : "bg-muted-foreground/40"
              }`}
            />
            {status.data?.connected ? "已连接" : "未连接"}
          </div>
          {status.data?.connected_since ? (
            <span className="text-muted-foreground">
              自 {fmtTime(status.data.connected_since)}
            </span>
          ) : null}
          <span className="font-mono text-xs text-muted-foreground">
            bot {status.data?.bot_uin || "未配置"}
          </span>
          {status.data?.unread && (
            <span className="text-muted-foreground">{status.data.unread}</span>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[11rem_1fr]">
        {/* 会话清单 */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">会话（24h 活跃）</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <ScrollArea className="h-[calc(100vh-20rem)]">
              {chats.isLoading && <div className="p-4"><Skeleton className="h-5 w-3/4" /></div>}
              {(chats.data?.chats ?? []).map((c) => (
                <button
                  key={c.chat_id}
                  onClick={() => setChat(c.chat_id)}
                  className={`block w-full border-b px-2.5 py-2 text-left text-sm hover:bg-accent/40 ${
                    chat === c.chat_id ? "bg-accent" : ""
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate">{chatLabel(c.chat_id)}</span>
                    {c.unread > 0 && (
                      <Badge className="h-5 shrink-0 px-1.5 text-[10px]">{c.unread}</Badge>
                    )}
                  </div>
                  <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                    {fmtTime(c.last_ts)}
                  </div>
                </button>
              ))}
              {chats.data && chats.data.chats.length === 0 && (
                <div className="py-10 text-center text-xs text-muted-foreground">
                  24h 内无活跃会话
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        {/* 聊天记录 */}
        <Card>
          <CardHeader className="flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm">
              {chat ? chatLabel(chat) : "选择左侧会话查看记录"}
            </CardTitle>
            <Button
              variant="ghost" size="sm"
              onClick={() => log.refetch()}
              disabled={!chat}
            >
              刷新
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <ScrollArea className="h-[calc(100vh-20rem)]">
              <div className="space-y-2 p-4 text-sm">
                {log.data?.rows.map((r, i) => {
                  const self = !!status.data?.bot_uin && r.sender_id === status.data.bot_uin;
                  const prev = log.data!.rows[i - 1];
                  // 换人或自己发言才显示名字行；连续同人不再重复标
                  const showName = self || !prev || prev.sender_id !== r.sender_id;
                  const name = self ? "我" : r.sender_name || r.sender_id;
                  return (
                    <div
                      key={i}
                      className={`group/msg relative flex flex-col ${self ? "items-end" : "items-start"}`}
                    >
                      {showName ? (
                        /* 名字行：grid 同格叠放（宽度取两者较宽者），hover 交叉淡切为时间 */
                        <div className="mb-0.5 grid text-[11px] font-medium text-muted-foreground">
                          <span className="[grid-area:1/1] transition-opacity duration-200 group-hover/msg:opacity-0">{name}</span>
                          <span className="[grid-area:1/1] font-mono opacity-0 transition-opacity duration-200 group-hover/msg:opacity-100">{fmtTime(r.ts)}</span>
                        </div>
                      ) : (
                        /* 连续消息无名字行：时间 hover 时浮在气泡侧边（不挤布局） */
                        <div
                          className={`absolute top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted-foreground opacity-0 transition-opacity duration-200 group-hover/msg:opacity-100 ${
                            self ? "right-full mr-2" : "left-full ml-2"
                          }`}
                        >
                          {fmtTime(r.ts)}
                        </div>
                      )}
                      <div
                        className={`max-w-[75%] rounded-xl px-5 py-1.5 text-sm leading-relaxed ${
                          self ? "bg-primary text-primary-foreground" : "bg-muted"
                        }`}
                      >
                        <div className="whitespace-pre-wrap wrap-break-word">{r.text}</div>
                      </div>
                    </div>
                  );
                })}
                {log.data && log.data.count === 0 && (
                  <div className="py-10 text-center text-xs text-muted-foreground">
                    无记录
                  </div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
