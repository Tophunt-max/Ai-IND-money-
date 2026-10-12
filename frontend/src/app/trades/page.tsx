"use client";

import { ChevronDown, RefreshCw, ScrollText, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { BarChart, Donut, DOWN, EquityChart, UP } from "@/components/charts";
import { Badge, Button, Card, cx, Empty, ErrorBox, PageTitle, pnlClass, Segmented, Skeleton, StatCard } from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Trade {
  id: string | null;
  symbol: string;
  side: string;
  quantity: number;
  price: number | null;
  status: string;
  is_paper: boolean;
  pnl: number | null;
  confidence: number | null;
  reason: string;
  closed_at: string | null;
  created_at: string | null;
}

const MODES = [
  { value: "current", label: "Current mode" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
  { value: "all", label: "All" },
] as const;

export default function TradesPage() {
  const [mode, setMode] = useState<(typeof MODES)[number]["value"]>("current");
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const { data, error, loading, reload } = useApi<{ trades: Trade[] }>(`/api/dashboard/trades?limit=200&mode=${mode}`);
  const trades = useMemo(
    () => (data?.trades || []).filter((t) => !q || t.symbol.toLowerCase().includes(q.toLowerCase())),
    [data, q],
  );
  const closed = trades.filter((t) => t.pnl != null);
  const total = closed.reduce((s, t) => s + (t.pnl || 0), 0);
  const wins = closed.filter((t) => (t.pnl || 0) > 0).length;
  const losses = closed.filter((t) => (t.pnl || 0) < 0).length;
  // Oldest first, by when the P&L was booked
  const booked = useMemo(() => closed.slice().sort((a, b) =>
    (a.closed_at || a.created_at || "").localeCompare(b.closed_at || b.created_at || "")), [closed]);
  const curve = useMemo(() => {
    let run = 0;
    return booked.map((t) => ({ label: when(t.closed_at || t.created_at), v: (run += t.pnl || 0) }));
  }, [booked]);
  const bySymbol = useMemo(() => {
    const m = new Map<string, number>();
    closed.forEach((t) => m.set(t.symbol, (m.get(t.symbol) || 0) + (t.pnl || 0)));
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]).slice(0, 20);
  }, [closed]);
  const avgWin = wins ? closed.filter((t) => (t.pnl || 0) > 0).reduce((s, t) => s + (t.pnl || 0), 0) / wins : null;
  const avgLoss = losses ? closed.filter((t) => (t.pnl || 0) < 0).reduce((s, t) => s + (t.pnl || 0), 0) / losses : null;

  return (
    <div className="space-y-3 sm:space-y-4">
      <PageTitle title="Trades" icon={ScrollText} subtitle="Every order the system recorded"
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload}>Refresh</Button>} />

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
        <StatCard title="Trades" value={trades.length} tone="info" detail={`${closed.length} with P&L`} />
        <StatCard title="Realized P&L" value={<span className={pnlClass(total)}>{inr(total)}</span>} tone={total >= 0 ? "ok" : "error"} />
        <StatCard title="Win rate" value={closed.length ? `${Math.round((wins / closed.length) * 100)}%` : "—"} detail={`${wins} of ${closed.length} closed`} />
        <StatCard title="Avg win / loss" value={<span><span className="text-emerald-400">{inr(avgWin, 0)}</span> / <span className="text-rose-400">{inr(avgLoss, 0)}</span></span>}
          detail={avgWin != null && avgLoss ? `payoff ${(avgWin / Math.abs(avgLoss)).toFixed(2)}×` : undefined} />
      </div>

      {closed.length >= 2 && (
        <div className="grid gap-3 lg:grid-cols-3">
          <Card title="Cumulative P&L" subtitle="Closed trades in booking order, with drawdown" className="lg:col-span-2">
            <EquityChart points={curve} baseline={0} height={210} format={(v) => inr(v, 0)} />
          </Card>
          <Card title="Wins vs losses">
            <Donut slices={[{ label: "Wins", v: wins, color: UP }, { label: "Losses", v: losses, color: DOWN },
              { label: "Flat", v: closed.length - wins - losses, color: "#6b7280" }]}
              center={<div><div className="num text-lg font-bold text-white">{Math.round((wins / closed.length) * 100)}%</div><div className="text-[10px] text-gray-500">win rate</div></div>} />
          </Card>
          <Card title="P&L by symbol" subtitle="Top 20" className="lg:col-span-3">
            <BarChart bars={bySymbol.map(([s, v]) => ({ label: s.slice(0, 6), v, hint: s }))} height={150} format={(v) => inr(v, 0)} labelEvery={bySymbol.length > 10 ? 2 : 1} />
          </Card>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented value={mode} onChange={setMode} options={MODES} />
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter symbol" className="field pl-9" />
        </div>
      </div>

      <ErrorBox error={error} />
      <Card padded={false}>
        {loading && !data ? (
          <div className="space-y-3 p-3"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : trades.length === 0 ? (
          <Empty icon={ScrollText} title="No trades yet" />
        ) : (
          <div className="divide-y divide-white/4">
            {trades.map((t, i) => {
              const key = t.id || String(i);
              const isOpen = open === key;
              return (
                <div key={key}>
                  <button className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition hover:bg-white/2 sm:px-4"
                    onClick={() => setOpen(isOpen ? null : key)}>
                    <Badge tone={t.side === "BUY" ? "ok" : "error"}>{t.side}</Badge>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-white">{t.symbol}</span>
                        {!t.is_paper && <Badge tone="error">LIVE</Badge>}
                      </div>
                      <div className="num truncate text-xs text-gray-500">
                        {t.quantity} @ {inr(t.price)} · {t.status}
                        {t.confidence != null && ` · ${t.confidence}% conf.`}
                        {t.side === "BUY" && (t.closed_at ? ` · closed ${when(t.closed_at)}` : " · open")}
                      </div>
                    </div>
                    <div className="num text-right">
                      {t.pnl !== null && <div className={`text-sm font-semibold ${pnlClass(t.pnl)}`}>{inr(t.pnl)}</div>}
                      <div className="text-[11px] text-gray-500">{when(t.created_at)}</div>
                    </div>
                    <ChevronDown className={cx("h-4 w-4 shrink-0 text-gray-600 transition", isOpen && "rotate-180")} />
                  </button>
                  {isOpen && (
                    <div className="px-3 pb-3 sm:px-4">
                      <p className="whitespace-pre-wrap rounded-xl bg-white/2 p-3 text-xs leading-relaxed text-gray-400 ring-1 ring-white/5">
                        {t.reason || "No reason recorded."}
                      </p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
