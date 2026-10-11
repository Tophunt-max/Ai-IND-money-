"use client";

import { Bell, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { cx } from "./ui";

export interface ToastItem {
  id: number;
  tone: "ok" | "error" | "info" | "alert";
  title: string;
  body?: string;
}

const ToastCtx = createContext<(t: Omit<ToastItem, "id">) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

let seq = 0;
const ICON = { ok: CheckCircle2, error: XCircle, info: Info, alert: Bell };
const TONE = {
  ok: "text-emerald-400", error: "text-rose-400", info: "text-brand-400", alert: "text-amber-400",
};

/** Small notifications in a corner (top on a phone); each fades after a few seconds. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const dismiss = useCallback((id: number) => setItems((x) => x.filter((t) => t.id !== id)), []);
  const push = useCallback((t: Omit<ToastItem, "id">) => {
    const id = ++seq;
    setItems((x) => [...x.slice(-3), { ...t, id }]);
    setTimeout(() => dismiss(id), t.tone === "alert" ? 9000 : 5000);
  }, [dismiss]);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-[calc(env(safe-area-inset-top)+0.75rem)] z-[60] flex flex-col items-center gap-2 px-3 sm:inset-x-auto sm:right-4 sm:top-auto sm:bottom-4 sm:items-end"
        aria-live="polite">
        {items.map((t) => {
          const Icon = ICON[t.tone];
          return (
            <div key={t.id} className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-2xl border border-white/[0.08] bg-ink-900 p-3.5 shadow-pop animate-scale-in">
              <Icon className={cx("mt-0.5 h-5 w-5 shrink-0", TONE[t.tone])} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-white">{t.title}</div>
                {t.body && <div className="mt-0.5 break-words text-xs text-gray-400">{t.body}</div>}
              </div>
              <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss" className="rounded p-0.5 text-gray-500 hover:text-white">
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}
