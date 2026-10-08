"use client";

import { Dices, FlaskConical, Play } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/AuthGate";
import Chart from "@/components/Chart";
import {
  Badge, Button, Card, Empty, ErrorBox, Field, Notice, PageTitle, pnlClass, Progress, Segmented, Select, StatCard, Table,
} from "@/components/ui";
import { inr } from "@/lib/api";
import { useJob } from "@/lib/hooks";

const n = (v: number | null | undefined, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
const p = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${Number(v).toFixed(d)}%`);

function BacktestResult({ r }: { r: any }) {
  const m = r.metrics;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Total return" tone={m.total_return_pct >= 0 ? "ok" : "error"}
          value={<span className={pnlClass(m.total_return_pct)}>{p(m.total_return_pct)}</span>} detail={`Annual ${p(m.annual_return_pct)}`} />
        <StatCard title="Sharpe" tone="info" value={n(m.sharpe_ratio)} detail={`Sortino ${n(m.sortino_ratio)} · Calmar ${n(m.calmar_ratio)}`} />
        <StatCard title="Max drawdown" tone="error" value={p(m.max_drawdown_pct)} detail={`${m.max_drawdown_duration_days ?? "—"} days`} />
        <StatCard title="Win rate" value={p(m.win_rate_pct, 1)} detail={`${m.winning_trades}/${m.total_trades} trades · PF ${n(m.profit_factor)}`} />
      </div>
      <Card title="Equity curve" subtitle={`${r.symbol} · ${r.start_date} → ${r.end_date} · ${r.strategy}`}>
        {r.equity_curve.length >= 2 ? (
          <Chart points={r.equity_curve.map((pt: any, i: number) => ({ t: i, v: pt.value, label: pt.date }))} height={220} format={(v) => inr(v, 0)} />
        ) : <Empty>No equity curve.</Empty>}
      </Card>
      <Card title={`Trades (${r.trades.length})`} subtitle={`Stop ${r.stop_loss_pct}% · target ${r.target_pct}%`}>
        {r.trades.length === 0 ? <Empty>The strategy made no trades in this period.</Empty> : (
          <Table head={["Entry", "Exit", "Qty", "P&L", "%", "Why"]}>
            {r.trades.slice().reverse().map((t: any, i: number) => (
              <tr key={i} className="hover:bg-white/[0.02]">
                <td><div className="text-white">{String(t.entry_date).slice(0, 10)}</div><div className="text-[11px] text-gray-500">{inr(t.entry_price)}</div></td>
                <td><div>{String(t.exit_date).slice(0, 10)}</div><div className="text-[11px] text-gray-500">{inr(t.exit_price)}</div></td>
                <td>{t.quantity}</td>
                <td className={pnlClass(t.pnl)}>{inr(t.pnl, 0)}</td>
                <td className={pnlClass(t.pnl_pct)}>{p(t.pnl_pct)}</td>
                <td><Badge tone={t.exit_reason === "target" ? "ok" : t.exit_reason === "stop_loss" ? "error" : "neutral"}>{String(t.exit_reason).replace(/_/g, " ")}</Badge></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function MonteCarloResult({ r }: { r: any }) {
  const m = r.result;
  const max = Math.max(1, ...r.histogram.map((h: any) => h.count));
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Median return" tone={m.median_return_pct >= 0 ? "ok" : "error"} value={<span className={pnlClass(m.median_return_pct)}>{p(m.median_return_pct)}</span>}
          detail={`5%–95%: ${p(m.p5_return_pct)} … ${p(m.p95_return_pct)}`} />
        <StatCard title="Probability of loss" tone={m.probability_of_loss_pct > 50 ? "error" : "warning"} value={p(m.probability_of_loss_pct, 1)} />
        <StatCard title="Worst drawdown" tone="error" value={p(m.worst_max_dd_pct)} detail={`Median ${p(m.median_max_dd_pct)}`} />
        <StatCard title="Probability of ruin" value={p(m.probability_of_ruin_pct, 1)} detail={`${m.n_simulations} runs · ${m.n_trades} trades`} />
      </div>
      <Card title="Distribution of final returns" subtitle="Each bar: how many shuffled runs ended in that range">
        <div className="flex h-48 items-end gap-1">
          {r.histogram.map((h: any, i: number) => (
            <div key={i} className="group flex flex-1 flex-col items-center justify-end" title={`${p(h.from)} … ${p(h.to)}: ${h.count}`}>
              <div className={`w-full rounded-t-md ${h.to < 0 ? "bg-gradient-to-t from-rose-600/70 to-rose-400" : "bg-gradient-to-t from-emerald-600/70 to-emerald-400"}`}
                style={{ height: `${(h.count / max) * 100}%`, minHeight: h.count ? 3 : 0 }} />
            </div>
          ))}
        </div>
        <div className="num mt-2 flex justify-between text-[11px] text-gray-500">
          <span>{p(r.histogram[0]?.from)}</span><span>{p(r.histogram[r.histogram.length - 1]?.to)}</span>
        </div>
        {r.histogram.length === 1 && <p className="mt-3 text-xs text-gray-500">Every ordering ends at the same return: drawdowns differ, the total does not.</p>}
      </Card>
    </div>
  );
}

export default function BacktestPage() {
  const { isAdmin } = useAuth();
  const [kind, setKind] = useState<"backtest" | "montecarlo">("backtest");
  const [symbol, setSymbol] = useState("RELIANCE");
  const [days, setDays] = useState(365);
  const [sl, setSl] = useState(3);
  const [tp, setTp] = useState(6);
  const [sims, setSims] = useState(1000);
  const { job, error, running, start } = useJob();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    start({ kind, symbol: symbol.trim().toUpperCase(), days, stop_loss_pct: sl, target_pct: tp, simulations: sims });
  };
  const done = job?.status === "done" && (job.kind === "backtest" || job.kind === "montecarlo");

  return (
    <div className="space-y-6">
      <PageTitle title="Backtest" icon={FlaskConical} subtitle="Test the RSI mean-reversion strategy on history. Pure maths, no AI calls, no orders." />

      <Card>
        <form onSubmit={submit} className="space-y-5">
          <Segmented value={kind} onChange={setKind} options={[
            { value: "backtest", label: <span className="flex items-center gap-2"><FlaskConical className="h-4 w-4" /> Backtest</span> },
            { value: "montecarlo", label: <span className="flex items-center gap-2"><Dices className="h-4 w-4" /> Monte Carlo</span> },
          ]} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Field label="Symbol" className="col-span-2 md:col-span-1">
              <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} className="field uppercase" />
            </Field>
            <Field label="History">
              <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                {[180, 365, 730, 1095, 1825].map((d) => <option key={d} value={d}>{d >= 365 ? `${d / 365} year${d > 365 ? "s" : ""}` : `${d} days`}</option>)}
              </Select>
            </Field>
            {kind === "backtest" ? (
              <>
                <Field label="Stop loss %"><input type="number" step="0.5" min="0.5" max="50" value={sl} onChange={(e) => setSl(Number(e.target.value))} className="field" /></Field>
                <Field label="Target %"><input type="number" step="0.5" min="0.5" max="100" value={tp} onChange={(e) => setTp(Number(e.target.value))} className="field" /></Field>
              </>
            ) : (
              <Field label="Simulations">
                <Select value={sims} onChange={(e) => setSims(Number(e.target.value))}>
                  {[500, 1000, 2000, 5000].map((s) => <option key={s} value={s}>{s}</option>)}
                </Select>
              </Field>
            )}
            <div className="flex items-end">
              <Button type="submit" icon={Play} loading={running} disabled={!isAdmin || !symbol.trim()} className="w-full">Run</Button>
            </div>
          </div>
        </form>
        {running && <div className="mt-5"><Progress value={50} /><p className="mt-2 text-xs text-gray-500">Fetching history and simulating...</p></div>}
        {!isAdmin && <p className="mt-3 text-xs text-gray-500">View-only account: running a backtest needs an admin.</p>}
      </Card>

      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />
      {done && job.kind === "backtest" && <BacktestResult r={job.result} />}
      {done && job.kind === "montecarlo" && <MonteCarloResult r={job.result} />}
      {!job && (
        <Notice tone="neutral" icon={FlaskConical}>
          Past results do not guarantee future returns. Monte Carlo shuffles the backtest&apos;s trades to show how much of the result is luck of the order.
        </Notice>
      )}
    </div>
  );
}
