"use client";

import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useToastStore } from "@/lib/store";
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from "lucide-react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  const { toasts, dismiss } = useToastStore();

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none">
        {toasts.map((t) => {
          const icon =
            t.variant === "success" ? (
              <CheckCircle2 className="w-4 h-4 text-green-600 shrink-0 mt-0.5" />
            ) : t.variant === "error" ? (
              <XCircle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
            ) : t.variant === "warning" ? (
              <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
            ) : (
              <Info className="w-4 h-4 text-green-600 shrink-0 mt-0.5" />
            );
          return (
            <div
              key={t.id}
              className="pointer-events-auto flex items-start gap-3 rounded-xl border border-slate-200 bg-white/95 backdrop-blur px-4 py-3 shadow-xl"
            >
              {icon}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-900">{t.title}</p>
                {t.description && (
                  <p className="text-xs text-slate-500 mt-0.5 break-words">{t.description}</p>
                )}
              </div>
              <button
                onClick={() => dismiss(t.id)}
                className="text-slate-400 hover:text-slate-600 transition"
                aria-label="Dismiss notification"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          );
        })}
      </div>
    </QueryClientProvider>
  );
}
