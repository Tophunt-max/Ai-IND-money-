"use client";

import { useState } from "react";

import { Button, Card, Empty, ErrorBox, Loading, PageTitle, StatCard } from "@/components/ui";
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

const RANGES = [7, 30, 90, 365];

export default function ReportPage() {
  const [days, setDays] = useState(90);
  const { data: r, error, loading, reload } = useApi<Report>(`/api/dashboard/report?days=${days}`);

  return (
    <div className="space-y-5">
      <PageTitle title="Track record" right={<Button variant="ghost" onClick={reload}>Refresh</Button>} />
      <div className="flex gap-2">
        {RANGES.map((d) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={`px-3 py-1 rounded-full text-xs border ${
              days === d ? "border-blue-500 text-blue-300" : "border-gray-700 text-gray-400"
            }`}
          >
            {d} days
          </button>
        ))}
      </div>
      <ErrorBox error={error} />
      {loading && !r ? (
        <Loading text="Building report..." />
      ) : r ? (
        <>
          <p className="text-xs text-gray-500">
            Forward results only ({r.mode}): every AI call scored against what the market then did.
          </p>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard
              title="AI calls"
              value={r.calls.total}
              detail={`${r.calls.settled} settled · ${r.calls.pending} pending`}
            />
            <StatCard
              title="Hit rate"
              value={pct(r.calls.hit_rate)}
              detail={`of ${r.calls.directional} directional calls`}
              tone={r.calls.directional < r.min_sample ? "warning" : "neutral"}
            />
            <StatCard title="Buy calls vs NIFTY" value={pct(r.calls.long_alpha, 2)} detail="avg excess return" />
            <StatCard
              title="Realized P&L"
              value={<span className={r.trades.total_pnl >= 0 ? "text-green-400" : "text-red-400"}>{inr(r.trades.total_pnl)}</span>}
              detail={`${r.trades.closed} closed · ${r.trades.closed ? pct(r.trades.wins / r.trades.closed, 0) : "—"} won`}
            />
          </div>
          {r.calls.directional < r.min_sample && (
            <div className="text-xs text-yellow-400/80">
              ⚠ Only {r.calls.directional} settled directional calls. Below {r.min_sample}, hit rates are mostly noise.
            </div>
          )}

          <Card title="Closed trades">
            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-y-3 text-sm">
              {[
                ["Avg win", inr(r.trades.avg_win)],
                ["Avg loss", inr(r.trades.avg_loss)],
                ["Profit factor", r.trades.profit_factor == null ? "—" : r.trades.profit_factor.toFixed(2)],
                ["Max drawdown", inr(r.trades.max_drawdown)],
                ["Avg return / trade", pct(r.trades.avg_return, 2)],
                ["Return std", pct(r.trades.return_std, 2)],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-gray-500">{k}</dt>
                  <dd className="font-medium">{v}</dd>
                </div>
              ))}
            </dl>
          </Card>

          <Card title="Calls by rating">
            {Object.keys(r.calls.by_rating).length === 0 ? (
              <Empty>No AI calls in this period yet.</Empty>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-xs text-gray-500 text-left">
                  <tr><th className="py-1">Rating</th><th>Calls</th><th>Settled</th><th>Avg return</th><th>Vs NIFTY</th></tr>
                </thead>
                <tbody>
                  {Object.entries(r.calls.by_rating).sort().map(([rating, s]) => (
                    <tr key={rating} className="border-t border-gray-800">
                      <td className="py-2">{rating}</td><td>{s.calls}</td><td>{s.settled}</td>
                      <td>{pct(s.avg_return, 2)}</td><td>{pct(s.avg_alpha, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Confidence calibration">
            <p className="text-xs text-gray-500 mb-3">Do higher-confidence trades win more often?</p>
            <div className="space-y-2">
              {r.calibration.map((b) => {
                const rate = b.trades ? b.wins / b.trades : null;
                return (
                  <div key={b.low} className="flex items-center gap-3 text-sm">
                    <span className="w-16 text-gray-400">{b.low}–{Math.min(b.high, 100)}%</span>
                    <div className="flex-1 h-2 bg-gray-800 rounded">
                      <div className="h-2 bg-blue-500 rounded" style={{ width: `${(rate ?? 0) * 100}%` }} />
                    </div>
                    <span className="w-24 text-right text-gray-400">
                      {rate == null ? "—" : pct(rate, 0)} of {b.trades}
                    </span>
                  </div>
                );
              })}
            </div>
          </Card>
          <p className="text-xs text-gray-600">Sources: {r.sources.join(", ") || "none"}</p>
        </>
      ) : null}
    </div>
  );
}
