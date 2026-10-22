"use client";
/** 根 Providers：主题（深/浅切换）+ react-query + SSE + Toaster + token 门 */
import { useState, type ReactNode } from "react";
import { ThemeProvider } from "next-themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/sonner";
import { SseProvider } from "@/hooks/use-sse";
import { RecordsProvider } from "@/hooks/use-records";
import { TokenGate } from "@/components/token-gate";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
        },
      }),
  );
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <QueryClientProvider client={queryClient}>
        <SseProvider>
          <RecordsProvider>
            {children}
            <TokenGate />
            <Toaster position="top-center" richColors />
          </RecordsProvider>
        </SseProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
