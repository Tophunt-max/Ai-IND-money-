"use client";

import { ChevronDown, RefreshCw, ScrollText, Search } from "lucide-react";
import { useMemo, useState } from "react";

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

  return (
    <div className="space-y-6">
      <PageTitle title="Trades" icon={ScrollText} subtitle="Every order the system recorded"
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload}>Refresh</Button>} />

      <div className="grid grid-cols-3 gap-3">
        <StatCard title="Trades" value={trades.length} tone="info" />
        <StatCard title="Realized P&L" value={<span className={pnlClass(total)}>{inr(total)}</span>} tone={total >= 0 ? "ok" : "error"} />
        <StatCard title="Win rate" value={closed.length ? `${Math.round((wins / closed.length) * 100)}%` : "—"} detail={`${wins} of ${closed.length} closed`} />
      </div>

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
          <div className="space-y-3 p-5"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : trades.length === 0 ? (
          <Empty icon={ScrollText} title="No trades yet" />
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {trades.map((t, i) => {
              const key = t.id || String(i);
              const isOpen = open === key;
              return (
                <div key={key}>
                  <button className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition hover:bg-white/[0.02]"
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
                    <div className="px-5 pb-4">
                      <p className="whitespace-pre-wrap rounded-xl bg-white/[0.02] p-3 text-xs leading-relaxed text-gray-400 ring-1 ring-white/[0.05]">
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
