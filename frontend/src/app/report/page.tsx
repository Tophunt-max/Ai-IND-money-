"use client";

import { BarChart3, Gauge, RefreshCw, Target, TrendingUp, Wallet } from "lucide-react";
import { useState } from "react";

import { Button, Card, Empty, ErrorBox, KV, Notice, PageTitle, pnlClass, Progress, Segmented, Skeleton, StatCard, Table } from "@/components/ui";
import { inr, pct } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Report {
  days: number;
  mode: string;
  min_sample: number;
  calls: {
    total: number;
    pending: number;
    settled: number;
    hit_rate: number | null;
    directional: number;
    long_alpha: number | null;
    by_rating: Record<string, { calls: number; settled: number; avg_return: number | null; avg_alpha: number | null }>;
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
    <div className="space-y-6">
      <PageTitle title="Track record" icon={BarChart3} subtitle="Forward results only: every AI call scored against what the market then did"
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload}>Refresh</Button>} />
      <Segmented value={days} onChange={setDays} options={RANGES} />
      <ErrorBox error={error} />
      {loading && !r ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : r ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard title="AI calls" icon={Target} tone="info" value={r.calls.total} detail={`${r.calls.settled} settled · ${r.calls.pending} pending`} />
            <StatCard title="Hit rate" icon={Gauge} value={pct(r.calls.hit_rate)} detail={`of ${r.calls.directional} directional calls`}
              tone={r.calls.directional < r.min_sample ? "warning" : "ok"} />
            <StatCard title="Buy calls vs NIFTY" icon={TrendingUp} value={<span className={pnlClass(r.calls.long_alpha)}>{pct(r.calls.long_alpha, 2)}</span>} detail="Average excess return" />
            <StatCard title="Realized P&L" icon={Wallet} tone={r.trades.total_pnl >= 0 ? "ok" : "error"}
              value={<span className={pnlClass(r.trades.total_pnl)}>{inr(r.trades.total_pnl)}</span>}
              detail={`${r.trades.closed} closed · ${r.trades.closed ? pct(r.trades.wins / r.trades.closed, 0) : "—"} won`} />
          </div>
          {r.calls.directional < r.min_sample && (
            <Notice tone="warning">Only {r.calls.directional} settled directional calls. Below {r.min_sample}, hit rates are mostly noise.</Notice>
          )}

          <div className="grid gap-5 lg:grid-cols-2">
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

          <Card title="Calls by rating">
            {Object.keys(r.calls.by_rating).length === 0 ? <Empty>No AI calls in this period yet.</Empty> : (
              <Table head={["Rating", "Calls", "Settled", "Avg return", "Vs NIFTY"]}>
                {Object.entries(r.calls.by_rating).sort().map(([rating, s]) => (
                  <tr key={rating} className="hover:bg-white/[0.02]">
                    <td className="font-medium text-white">{rating}</td><td>{s.calls}</td><td>{s.settled}</td>
                    <td className={pnlClass(s.avg_return)}>{pct(s.avg_return, 2)}</td>
                    <td className={pnlClass(s.avg_alpha)}>{pct(s.avg_alpha, 2)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
          <p className="text-xs text-gray-600">Mode {r.mode} · sources: {r.sources.join(", ") || "none"}</p>
        </>
      ) : null}
    </div>
  );
}
