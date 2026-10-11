"use client";

import { BarChart3, Gauge, RefreshCw, Target, TrendingUp, Wallet } from "lucide-react";
import { useState } from "react";

import { BarChart, Donut, DOWN, UP } from "@/components/charts";
import { Button, Card, Empty, ErrorBox, KV, Notice, PageTitle, pnlClass, Progress, Segmented, Skeleton, StatCard, Table } from "@/components/ui";
import { inr, pct } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Report {
  days: number;
  mode: string;
  min_sample: number;
  signals: {
    total: number;
    qualified: number;
    rejected: number;
    expired: number;
    executed: number;
    closed: number;
    wins: number;
    net: number;
    win_rate: number | null;
    enough_data: boolean;
    by_strategy: Record<string, { decisions: number; qualified: number; executed: number; closed: number; wins: number; net: number; win_rate: number | null }>;
    top_rejections: [string, number][];
  };
  trades: {
    closed: number;
    wins: number;
    total_pnl: number;
    avg_win: number | null;
    avg_loss: number | null;
    profit_factor: number | null;
    max_drawdown: number;
    avg_return: number | null;
    return_std: number | null;
  };
  calibration: { low: number; high: number; trades: number; wins: number }[];
  sources: string[];
}

const RANGES = [
  { value: 7, label: "7D" }, { value: 30, label: "30D" }, { value: 90, label: "90D" }, { value: 365, label: "1Y" },
] as const;

export default function ReportPage() {
  const [days, setDays] = useState<number>(90);
  const { data: r, error, loading, reload } = useApi<Report>(`/api/dashboard/report?days=${days}`);

  return (
    <div className="space-y-3 sm:space-y-4">
      <PageTitle title="Track record" icon={BarChart3} subtitle="Forward results only: every Adaptive AI signal the engines decided, and what its trade then made after charges"
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload}>Refresh</Button>} />
      <Segmented value={days} onChange={setDays} options={RANGES} />
      <ErrorBox error={error} />
      {loading && !r ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : r ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard title="AI signals" icon={Target} tone="info" value={r.signals.total} detail={`${r.signals.qualified} qualified · ${r.signals.rejected} rejected`} />
            <StatCard title="Signal win rate" icon={Gauge} value={pct(r.signals.win_rate)} detail={`of ${r.signals.closed} closed · ${r.signals.executed} executed`}
              tone={r.signals.enough_data ? "ok" : "warning"} />
            <StatCard title="Signals net P&L" icon={TrendingUp} value={<span className={pnlClass(r.signals.net)}>{inr(r.signals.net)}</span>} detail="After charges, closed signals" />
            <StatCard title="Realized P&L" icon={Wallet} tone={r.trades.total_pnl >= 0 ? "ok" : "error"}
              value={<span className={pnlClass(r.trades.total_pnl)}>{inr(r.trades.total_pnl)}</span>}
              detail={`${r.trades.closed} closed · ${r.trades.closed ? pct(r.trades.wins / r.trades.closed, 0) : "—"} won`} />
          </div>
          {!r.signals.enough_data && (
            <Notice tone="warning">Only {r.signals.closed} closed signals. Below {r.min_sample}, win rates are mostly noise.</Notice>
          )}

          <div className="grid gap-3 lg:grid-cols-2">
            <Card title="Closed trades">
              <KV cols={2} items={[
                ["Avg win", <span key="w" className="text-emerald-300">{inr(r.trades.avg_win)}</span>],
                ["Avg loss", <span key="l" className="text-rose-300">{inr(r.trades.avg_loss)}</span>],
                ["Profit factor", r.trades.profit_factor == null ? "—" : r.trades.profit_factor.toFixed(2)],
                ["Max drawdown", inr(r.trades.max_drawdown)],
                ["Avg return / trade", pct(r.trades.avg_return, 2)],
                ["Return std", pct(r.trades.return_std, 2)],
              ]} />
            </Card>

            <Card title="Confidence calibration" subtitle="Do higher-confidence trades win more often?">
              <div className="space-y-3">
                {r.calibration.map((b) => {
                  const rate = b.trades ? b.wins / b.trades : null;
                  return (
                    <div key={b.low} className="flex items-center gap-3 text-sm">
                      <span className="num w-16 text-xs text-gray-400">{b.low}–{Math.min(b.high, 100)}%</span>
                      <div className="flex-1"><Progress value={(rate ?? 0) * 100} /></div>
                      <span className="num w-24 text-right text-xs text-gray-400">{rate == null ? "—" : pct(rate, 0)} of {b.trades}</span>
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card title="Win rate by confidence" subtitle="Bars: share of trades won in each confidence band">
              <BarChart height={150} zeroLine={false} format={(v) => `${v.toFixed(0)}%`}
                bars={r.calibration.map((b) => ({ label: `${b.low}%`, v: b.trades ? (b.wins / b.trades) * 100 : 0, tone: "accent",
                  hint: `${b.low}–${Math.min(b.high, 100)}% confidence · ${b.wins}/${b.trades} won` }))} />
            </Card>
            <Card title="Closed trades">
              <Donut slices={[{ label: "Won", v: r.trades.wins, color: UP }, { label: "Lost", v: r.trades.closed - r.trades.wins, color: DOWN }]}
                center={<div><div className="num text-lg font-bold text-white">{r.trades.closed ? Math.round((r.trades.wins / r.trades.closed) * 100) : 0}%</div><div className="text-[10px] text-gray-500">won</div></div>} />
            </Card>
          </div>
          {Object.keys(r.signals.by_strategy).length > 0 && (
            <Card title="Net P&L by strategy" subtitle="Closed signals, after charges">
              <BarChart height={140} format={(v) => inr(v, 0)}
                bars={Object.entries(r.signals.by_strategy).sort().map(([k, s]) => ({ label: k.replace(/^(scalp|swing|fno)\./, "").slice(0, 10), v: s.net, hint: `${k}: ${s.closed} closed` }))} />
            </Card>
          )}

          <div className="grid gap-3 lg:grid-cols-2">
            <Card title="Signals by strategy">
              {Object.keys(r.signals.by_strategy).length === 0 ? <Empty>No signals in this period yet.</Empty> : (
                <Table head={["Strategy", "Decided", "Qualified", "Executed", "Win rate", "Net"]}>
                  {Object.entries(r.signals.by_strategy).sort().map(([name, s]) => (
                    <tr key={name} className="hover:bg-white/[0.02]">
                      <td className="font-medium text-white">{name}</td><td>{s.decisions}</td><td>{s.qualified}</td><td>{s.executed}</td>
                      <td>{pct(s.win_rate)}</td>
                      <td className={pnlClass(s.net)}>{inr(s.net, 0)}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Why signals were rejected" subtitle="Most common gate reasons">
              {r.signals.top_rejections.length === 0 ? <Empty>No rejections in this period.</Empty> : (
                <Table head={["Reason", "Count"]}>
                  {r.signals.top_rejections.map(([why, count]) => (
                    <tr key={why}><td className="text-gray-300">{why}</td><td className="num">{count}</td></tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
          <p className="text-xs text-gray-600">Mode {r.mode} · sources: {r.sources.join(", ") || "none"}</p>
        </>
      ) : null}
    </div>
  );
}
