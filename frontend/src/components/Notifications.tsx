"use client";

import { Bell, BellRing, CheckCheck, Settings2 } from "lucide-react";
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { FiredAlert } from "@/lib/live";

import { useToast } from "./Toast";
import { Button, cx, Empty, Sheet } from "./ui";

export interface FeedItem { id: string; kind: string; title: string; body: string; at: string; symbol: string; read: boolean }

interface Ctx {
  unread: number;
  open: () => void;
  onAlerts: (a: FiredAlert[]) => void;
}
const NotifCtx = createContext<Ctx>({ unread: 0, open: () => {}, onAlerts: () => {} });
export const useNotifications = () => useContext(NotifCtx);

const OP_TEXT: Record<string, string> = { above: "crossed above", below: "fell below", pct_up: "is up", pct_down: "is down" };

/** The bell: the server's notification feed (fired price alerts …), polled every minute and
 * refreshed at once when the live stream fires an alert (which also shows a toast). */
export function NotificationsProvider({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const [show, setShow] = useState(false);
  const toast = useToast();
  const feed = useApi<{ items: FeedItem[]; unread: number }>(enabled ? "/api/dashboard/notifications?limit=50" : null, 60000);
  const { reload } = feed;

  const onAlerts = useCallback((fired: FiredAlert[]) => {
    fired.forEach((a) => toast({
      tone: "alert", title: `🔔 ${a.symbol} ${OP_TEXT[a.op] || ""} ${a.op.startsWith("pct") ? `${a.value}%` : a.value}`,
      body: `Now ₹${a.triggered_price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${a.note ? ` · ${a.note}` : ""}`,
    }));
    reload();
  }, [toast, reload]);

  const ctx = useMemo(() => ({ unread: feed.data?.unread || 0, open: () => setShow(true), onAlerts }),
    [feed.data?.unread, onAlerts]);

  const markAll = async () => {
    try {
      await api("/api/dashboard/notifications/read", { method: "POST", json: {} });
    } finally {
      reload();
    }
  };
  useEffect(() => {
    if (show) reload();
  }, [show, reload]);
  const items = feed.data?.items || [];

  return (
    <NotifCtx.Provider value={ctx}>
      {children}
      <Sheet open={show} onClose={() => setShow(false)} title={<span className="flex items-center gap-2"><Bell className="h-5 w-5 text-brand-400" /> Notifications</span>}
        footer={<div className="flex items-center justify-between gap-2">
          <Link href="/alerts" onClick={() => setShow(false)} className="link inline-flex items-center gap-1.5 text-sm font-medium"><Settings2 className="h-4 w-4" /> Manage price alerts</Link>
          <Button size="sm" variant="ghost" icon={CheckCheck} onClick={markAll} disabled={!ctx.unread}>Mark all read</Button>
        </div>}>
        {feed.error && <p className="py-4 text-sm text-rose-300">{feed.error}</p>}
        {!items.length ? (
          <Empty icon={BellRing} title="No notifications yet">Fired price alerts land here and on Telegram.</Empty>
        ) : (
          <ul className="space-y-1">
            {items.map((n) => (
              <li key={n.id}>
                <Link href={n.symbol ? `/market?symbol=${encodeURIComponent(n.symbol)}` : "/alerts"} onClick={() => setShow(false)}
                  className={cx("flex gap-3 rounded-2xl p-3 transition hover:bg-white/[0.04]", !n.read && "bg-brand-500/[0.06]")}>
                  <span className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.read ? "bg-transparent" : "bg-brand-500")} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-white">{n.title}</span>
                      <span className="shrink-0 text-[11px] text-gray-500">{when(n.at)}</span>
                    </div>
                    <p className="mt-0.5 text-xs text-gray-400">{n.body}</p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Sheet>
    </NotifCtx.Provider>
  );
}
