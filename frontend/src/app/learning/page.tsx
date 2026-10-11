"use client";

import { Database, Sparkles, Target } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, Card, Empty, ErrorBox, KV, Notice, PageTitle, pnlClass, Progress, Skeleton, StatCard, Table } from "@/components/ui";
import { api, inr } from "@/lib/api";
import AdaptiveAI from "@/components/AdaptiveAI";
import RLAgents from "@/components/RLAgents";
import SwingModel from "@/components/SwingModel";
import StrategyBuilder from "@/components/StrategyBuilder";
import ModelRegistry from "@/components/ModelRegistry";
import RegimeDrift from "@/components/RegimeDrift";
import { useApi } from "@/lib/hooks";

type Row = Record<string, any>;

interface Learning {
  available: boolean;
  error?: string;
  insights?: string;
  calibration?: Row[];
  sectors?: Row[];
  regimes?: Row[];
  timing?: Row[];
  stop_loss?: Row;
}

const p1 = (v: any) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);

function SymbolStats() {
  const [symbol, setSymbol] = useState("");
  const [data, setData] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setData(await api(`/api/dashboard/learning/symbol?symbol=${encodeURIComponent(symbol.trim())}`));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Symbol track record" icon={Target} subtitle="How the AI has done on one stock">
      <form onSubmit={go} className="flex gap-2">
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="RELIANCE" className="field uppercase" />
        <Button type="submit" loading={busy} disabled={!symbol.trim()}>Look up</Button>
      </form>
      <div className="mt-4">
        <ErrorBox error={error} />
        {data && (data.total_trades ? (
          <KV cols={2} items={[
            ["Trades", data.total_trades],
            ["Win rate", p1(data.win_rate)],
            ["Avg P&L", <span key="p" className={pnlClass(data.avg_pnl)}>{inr(data.avg_pnl)}</span>],
            ["Avg P&L %", p1(data.avg_pnl_pct)],
            ["Avg confidence", p1(data.avg_confidence)],
            ["Avg holding", data.avg_holding_days != null ? `${Number(data.avg_holding_days).toFixed(1)} days` : "—"],
          ]} />
        ) : <Empty>No recorded trades for {data.symbol}.</Empty>)}
      </div>
    </Card>
  );
}

export default function LearningPage() {
  const { data, error, loading } = useApi<Learning>("/api/dashboard/learning");
  const sl = data?.stop_loss;

  return (
    <div className="space-y-3 sm:space-y-4">
      <PageTitle title="AI learning" icon={Sparkles} subtitle="The Adaptive AI: models trained on our own market data, signals and trades (no LLM)" />
      <SwingModel />
      <ModelRegistry />
      <StrategyBuilder />
      <AdaptiveAI />
      <RLAgents />
      <RegimeDrift />
      <ErrorBox error={error} />

      {loading && !data ? <Skeleton className="h-32" /> : data && !data.available ? (
        <Notice tone="neutral" icon={Database} title="Signal tracker not connected">{data.error}</Notice>
      ) : data && (
        <>
          {data.insights && (
            <Card title="Insights" icon={Sparkles}>
              <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-gray-300">{data.insights}</pre>
            </Card>
          )}
          {sl && (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard title="Trades tracked" value={sl.total_trades ?? 0} tone="info" />
              <StatCard title="Stops hit" value={sl.stops_hit ?? 0} detail={p1(sl.stop_hit_rate)} tone="warning" />
              <StatCard title="Avg stop loss" value={p1(sl.avg_stop_loss)} tone="error" />
              <StatCard title="Avg win (no stop)" value={p1(sl.avg_win_when_not_stopped)} tone="ok" />
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-2">
            <Card title="Confidence calibration" subtitle="Stated confidence vs actual win rate">
              {(data.calibration || []).length === 0 ? <Empty>No data yet.</Empty> : (
                <div className="space-y-3">
                  {data.calibration!.map((c, i) => (
                    <div key={i} className="text-sm">
                      <div className="mb-1 flex justify-between text-xs text-gray-400">
                        <span>{c.confidence_range}</span>
                        <span className="num">{p1(c.actual_win_rate)} actual · {p1(c.stated_confidence)} stated · {c.total} trades</span>
                      </div>
                      <Progress value={Number(c.actual_win_rate) || 0} tone={Math.abs(Number(c.calibration_gap) || 0) > 15 ? "warning" : "info"} />
                    </div>
                  ))}
                </div>
              )}
            </Card>
            <Card title="Best hours" subtitle="Win rate by hour of entry (IST)">
              {(data.timing || []).length === 0 ? <Empty>No data yet.</Empty> : (
                <div className="flex h-40 items-end gap-1.5">
                  {data.timing!.map((t, i) => (
                    <div key={i} className="flex flex-1 flex-col items-center gap-1" title={`${t.trades} trades · ${p1(t.win_rate)}`}>
                      <div className="w-full rounded-t-md bg-gradient-to-t from-brand-600 to-cyan-400" style={{ height: `${Math.max(4, Number(t.win_rate) || 0)}%` }} />
                      <span className="num text-[10px] text-gray-500">{t.hour}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <Card title="By sector">
              {(data.sectors || []).length === 0 ? <Empty>No data yet.</Empty> : (
                <Table head={["Sector", "Trades", "Win rate", "P&L"]}>
                  {data.sectors!.map((s, i) => (
                    <tr key={i}><td className="text-white">{s.sector}</td><td>{s.trades}</td><td>{p1(s.win_rate)}</td><td className={pnlClass(s.total_pnl)}>{inr(s.total_pnl)}</td></tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="By market regime">
              {(data.regimes || []).length === 0 ? <Empty>No data yet.</Empty> : (
                <Table head={["Regime", "Trades", "Win rate", "P&L"]}>
                  {data.regimes!.map((s, i) => (
                    <tr key={i}><td className="text-white">{s.regime}</td><td>{s.trades}</td><td>{p1(s.win_rate)}</td><td className={pnlClass(s.total_pnl)}>{inr(s.total_pnl)}</td></tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
        </>
      )}

      <SymbolStats />
    </div>
  );
}
