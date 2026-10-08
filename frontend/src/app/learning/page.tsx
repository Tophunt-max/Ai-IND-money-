"use client";

import { BookOpen, Database, Search, Sparkles, Target } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, PageTitle, pnlClass, Progress, Skeleton, StatCard, Table } from "@/components/ui";
import { api, inr } from "@/lib/api";
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

interface Memory {
  memories: Record<string, { recommendation: string; score: number }[]>;
  reflections: { symbol: string; reflection: string; pnl: number | null; pnl_pct: number | null; trade_date: string }[];
  errors: Record<string, string>;
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

function MemorySearch() {
  const [q, setQ] = useState("");
  const [data, setData] = useState<Memory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setData(await api<Memory>(`/api/dashboard/memory?q=${encodeURIComponent(q.trim())}`));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const memories = data ? Object.entries(data.memories).flatMap(([role, items]) => items.map((m) => ({ role, ...m }))) : [];

  return (
    <Card title="Agent memory" icon={BookOpen} subtitle="Lessons the agents learned from past decisions and trades">
      <form onSubmit={go} className="relative">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. banking stocks after a rate hike" className="field pl-10 pr-24" />
        <Button type="submit" size="sm" loading={busy} disabled={q.trim().length < 2} className="absolute right-1.5 top-1/2 -translate-y-1/2">Search</Button>
      </form>
      <div className="mt-4 space-y-3">
        <ErrorBox error={error} />
        {data && Object.entries(data.errors).map(([k, v]) => <Notice key={k} tone="warning">{k}: {v}</Notice>)}
        {data && memories.length === 0 && data.reflections.length === 0 && <Empty>No matching memories.</Empty>}
        {memories.map((m, i) => (
          <div key={i} className="rounded-xl bg-white/[0.02] p-4 ring-1 ring-white/[0.05]">
            <div className="mb-2 flex items-center justify-between gap-2">
              <Badge tone="info">{m.role.replace(/_/g, " ")}</Badge>
              <span className="num text-[11px] text-gray-500">match {m.score.toFixed(2)}</span>
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-300">{m.recommendation}</p>
          </div>
        ))}
        {data && data.reflections.map((r, i) => (
          <div key={`r${i}`} className="rounded-xl bg-white/[0.02] p-4 ring-1 ring-white/[0.05]">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="font-medium text-white">{r.symbol} <span className="text-xs text-gray-500">{r.trade_date}</span></span>
              <span className={`num text-sm font-semibold ${pnlClass(r.pnl)}`}>{inr(r.pnl)}</span>
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-300">{r.reflection}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function LearningPage() {
  const { data, error, loading } = useApi<Learning>("/api/dashboard/learning");
  const sl = data?.stop_loss;

  return (
    <div className="space-y-6">
      <PageTitle title="AI learning" icon={Sparkles} subtitle="What the system learns from its own signals and trades" />
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
          <div className="grid gap-5 lg:grid-cols-2">
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
          <div className="grid gap-5 lg:grid-cols-2">
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

      <div className="grid gap-5 lg:grid-cols-2">
        <SymbolStats />
        <MemorySearch />
      </div>
    </div>
  );
}
