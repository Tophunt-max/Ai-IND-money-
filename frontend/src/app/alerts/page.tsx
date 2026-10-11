"use client";

import { Bell, BellOff, BellPlus, BellRing, RotateCcw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import AlertSheet, { alertDistance as distance, type PriceAlert } from "@/components/AlertSheet";
import { useAuth } from "@/components/AuthGate";
import type { FeedItem } from "@/components/Notifications";
import { useToast } from "@/components/Toast";
import { Badge, Button, Card, Empty, ErrorBox, Flash, PageTitle, Skeleton, SymbolAvatar, Tabs } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useQuotes } from "@/lib/live";

export default function AlertsPage() {
  const { isAdmin } = useAuth();
  const toast = useToast();
  const alerts = useApi<{ alerts: PriceAlert[] }>("/api/dashboard/alerts", 30000);
  const feed = useApi<{ items: FeedItem[] }>("/api/dashboard/notifications?limit=50", 60000);
  const [tab, setTab] = useState<"active" | "fired" | "feed">("active");
  const [open, setOpen] = useState(false);
  const list = alerts.data?.alerts || [];
  const active = list.filter((a) => a.active);
  const fired = list.filter((a) => !a.active);
  const { quotes } = useQuotes(active.map((a) => a.symbol));

  const act = async (path: string, method: "POST" | "DELETE", done: string) => {
    try {
      await api(path, { method, json: method === "POST" ? {} : undefined });
      toast({ tone: "ok", title: done });
      alerts.reload();
    } catch (e: any) {
      toast({ tone: "error", title: "Failed", body: e instanceof ApiError ? e.message : String(e) });
    }
  };

  return (
    <div className="space-y-4">
      <PageTitle title="Price alerts" icon={Bell} subtitle="Fire once when a price or day change crosses your level: a toast here, the bell, and Telegram"
        right={isAdmin && <Button icon={BellPlus} onClick={() => setOpen(true)}>New alert</Button>} />
      <ErrorBox error={alerts.error} />
      <section className="surface p-4 sm:p-5">
        <Tabs value={tab} onChange={setTab} className="mb-2" options={[
          { value: "active", label: "Active", count: active.length }, { value: "fired", label: "Fired", count: fired.length },
          { value: "feed", label: "Notifications" },
        ]} />
        {alerts.loading && !alerts.data ? <div className="space-y-2 py-3"><Skeleton className="h-14" /><Skeleton className="h-14" /></div> : null}
        {tab === "active" && alerts.data && (active.length === 0 ? (
          <Empty icon={BellPlus} title="No active alerts">Set one from a stock page (the bell next to the price) or with New alert.</Empty>
        ) : (
          <ul className="-mx-4 divide-y divide-white/[0.05] sm:-mx-5">
            {active.map((a) => {
              const q = quotes[a.symbol];
              const d = distance(a, q?.ltp);
              return (
                <li key={a.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                  <Link href={`/market?symbol=${encodeURIComponent(a.symbol)}`}><SymbolAvatar symbol={a.symbol} size={40} /></Link>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-white">{a.text}</div>
                    <div className="num text-xs text-gray-500">Now <Flash value={q?.ltp}>{inr(q?.ltp)}</Flash>{d != null && <> · {Math.abs(d).toFixed(2)}% {d > 0 ? "to go up" : "to go down"}</>}{a.note && ` · ${a.note}`}</div>
                  </div>
                  {isAdmin && <Button size="sm" variant="subtle" icon={Trash2} onClick={() => act(`/api/dashboard/alerts/${a.id}`, "DELETE", "Alert deleted")} title="Delete" />}
                </li>
              );
            })}
          </ul>
        ))}
        {tab === "fired" && alerts.data && (fired.length === 0 ? <Empty icon={BellOff}>No alert has fired yet.</Empty> : (
          <ul className="-mx-4 divide-y divide-white/[0.05] sm:-mx-5">
            {fired.map((a) => (
              <li key={a.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                <SymbolAvatar symbol={a.symbol} size={40} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-white">{a.text}</div>
                  <div className="num text-xs text-gray-500">Fired {when(a.triggered_at)} at {inr(a.triggered_price)}</div>
                </div>
                <Badge tone="ok">fired</Badge>
                {isAdmin && <>
                  <Button size="sm" variant="subtle" icon={RotateCcw} onClick={() => act(`/api/dashboard/alerts/${a.id}/rearm`, "POST", "Alert re-armed")} title="Re-arm" />
                  <Button size="sm" variant="subtle" icon={Trash2} onClick={() => act(`/api/dashboard/alerts/${a.id}`, "DELETE", "Alert deleted")} title="Delete" />
                </>}
              </li>
            ))}
          </ul>
        ))}
        {tab === "feed" && ((feed.data?.items || []).length === 0 ? <Empty icon={BellRing}>No notifications yet.</Empty> : (
          <ul className="-mx-4 divide-y divide-white/[0.05] sm:-mx-5">
            {(feed.data?.items || []).map((n) => (
              <li key={n.id} className="px-4 py-3 sm:px-5">
                <div className="flex justify-between gap-2"><span className="text-sm font-semibold text-white">{n.title}</span><span className="text-[11px] text-gray-500">{when(n.at)}</span></div>
                <p className="mt-0.5 text-xs text-gray-400">{n.body}</p>
              </li>
            ))}
          </ul>
        ))}
      </section>
      <Card title="How alerts work">
        <ul className="list-disc space-y-1 pl-5 text-[13px] text-gray-400">
          <li>Checked on every live price the dashboard streams (every 2 s while NSE is open), and every minute by the Telegram bot.</li>
          <li>An alert fires once, then moves to Fired; re-arm it to watch again.</li>
          <li>Alerts are stored on the server (shared by every device and the bot). They never place orders.</li>
        </ul>
      </Card>
      <AlertSheet open={open} onClose={() => setOpen(false)} onSaved={alerts.reload} />
    </div>
  );
}
