"use client";

import { Brain, CheckCircle2, Clock, Gauge, NotebookPen, Search, ShieldAlert, Target } from "lucide-react";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";

import { useAuth } from "@/components/AuthGate";
import PriceChart from "@/components/PriceChart";
import {
  actionTone, Badge, Button, Card, Collapsible, ErrorBox, Notice, PageTitle, Progress, Segmented, StatCard,
} from "@/components/ui";
import { inr } from "@/lib/api";
import { useJob } from "@/lib/hooks";

const REPORT_NAMES: Record<string, string> = {
  market_report: "Market analyst",
  sentiment_report: "Social analyst",
  news_report: "News analyst",
  fundamentals_report: "Fundamentals analyst",
  "investment_debate_state.judge_decision": "Research manager (bull vs bear)",
  investment_plan: "Investment plan",
  trader_investment_plan: "Trader",
  "risk_debate_state.judge_decision": "Portfolio manager (risk debate)",
  final_trade_decision: "Final decision",
};

const STAGES = [
  { at: 0, label: "Gathering data" },
  { at: 25, label: "Analysts writing reports" },
  { at: 90, label: "Bull vs bear debate" },
  { at: 150, label: "Trader plan" },
  { at: 190, label: "Risk debate" },
  { at: 240, label: "Final decision" },
];

function Analyze() {
  const params = useSearchParams();
  const { isAdmin } = useAuth();
  const [symbol, setSymbol] = useState(params.get("symbol") || "");
  const [trade, setTrade] = useState(params.get("trade") === "1");
  const { job, error, running, start } = useJob();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!running || !job) return;
    const t = setInterval(() => setElapsed(Math.round(Date.now() / 1000 - job.created_at)), 1000);
    return () => clearInterval(t);
  }, [running, job]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    if (trade && !confirm(`Analyze ${sym} and place a PAPER trade if the AI says BUY or SELL? Safety checks still apply. No real money.`)) return;
    start({ kind: trade ? "trade" : "analyze", symbol: sym });
  };

  const r = job?.status === "done" || job?.status === "failed" ? job.result : null;
  const reports = r?.reports ? Object.entries(r.reports as Record<string, string>) : [];
  reports.sort(([a], [b]) => {
    const order = Object.keys(REPORT_NAMES);
    return (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99);
  });
  const stage = [...STAGES].reverse().find((s) => elapsed >= s.at) || STAGES[0];

  return (
    <div className="space-y-6">
      <PageTitle title="Analyze a stock" icon={Brain}
        subtitle="15 AI agents: analysts → bull/bear debate → trader → risk team. 2–5 minutes." />

      <div className="surface relative overflow-hidden p-6">
        <div className="absolute -left-10 -top-24 h-64 w-64 rounded-full bg-brand-500/15 blur-3xl" />
        <div className="relative space-y-4">
          <Segmented value={trade ? "trade" : "analyze"} onChange={(v) => setTrade(v === "trade")} options={[
            { value: "analyze", label: <span className="flex items-center gap-2"><Brain className="h-4 w-4" /> Analyze only</span> },
            { value: "trade", label: <span className="flex items-center gap-2"><NotebookPen className="h-4 w-4" /> Analyze + paper trade</span> },
          ]} />
          <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
              <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="RELIANCE, TCS, INFY..." className="field h-12 pl-11 uppercase" />
            </div>
            <Button type="submit" size="lg" icon={Brain} loading={running} disabled={!isAdmin || !symbol.trim()}>
              {running ? "Agents working..." : trade ? "Analyze + trade" : "Run analysis"}
            </Button>
          </form>
          <p className="text-xs text-gray-500">
            {trade ? "Places a PAPER order (simulated) if the decision passes the safety checks." : "No order is placed."}
          </p>
        </div>
      </div>

      {!isAdmin && <Notice tone="neutral">View-only account: running an analysis needs an admin.</Notice>}
      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />

      {running && (
        <Card>
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="font-medium text-white">{job?.symbol}: {stage.label}...</span>
            <span className="num text-gray-400">{elapsed}s</span>
          </div>
          <div className="mt-3"><Progress value={Math.min(95, (elapsed / 270) * 100)} /></div>
          <p className="mt-3 text-xs text-gray-500">Keep this page open. You can also follow it on the Jobs page.</p>
        </Card>
      )}

      {r?.signal && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard title="Decision" icon={CheckCircle2} tone={actionTone(r.signal.action)}
              value={<Badge tone={actionTone(r.signal.action)}>{r.signal.action}</Badge>} detail={`${r.symbol} · ${r.trade_date}`} />
            <StatCard title="Confidence" icon={Gauge} tone="info" value={`${r.signal.confidence}%`}
              detail={<Progress value={r.signal.confidence} />} />
            <StatCard title="Entry" icon={Target} value={inr(r.signal.entry_price)}
              detail={`SL ${inr(r.signal.stop_loss)} · TG ${inr(r.signal.target)}`} />
            <StatCard title="Time taken" icon={Clock} value={`${r.duration_seconds}s`} />
          </div>
          {job?.kind === "trade" && (
            <Card title="Paper order" icon={NotebookPen}>
              {!r.execution ? (
                <p className="text-sm text-gray-400">No order: the decision was {r.signal.action}, so nothing was traded.</p>
              ) : r.execution.success ? (
                <Notice tone="ok" title="Paper order filled">
                  {r.signal.action} {r.execution.quantity ?? "—"} × {r.symbol} @ {inr(r.execution.fill_price)} · brokerage{" "}
                  {inr(r.execution.brokerage)} · order {r.execution.order_id || "—"}
                </Notice>
              ) : (
                <Notice tone="warning" icon={ShieldAlert} title={r.execution.safety_passed ? "Order not filled" : "Blocked by the safety checks"}>
                  {r.execution.rejection_reason || "No reason given"}
                </Notice>
              )}
            </Card>
          )}
          {r.signal.reasoning && (
            <Card title="Reasoning">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-300">{r.signal.reasoning}</p>
            </Card>
          )}
          <Card title={`${r.symbol} price`}><PriceChart symbol={r.symbol} initial="1mo" /></Card>
        </>
      )}

      {reports.length > 0 && (
        <Card title="Agent reports" subtitle="What each agent wrote">
          <div className="space-y-2">
            {reports.map(([key, text]) => <Collapsible key={key} title={REPORT_NAMES[key] || key} text={text} />)}
          </div>
        </Card>
      )}
    </div>
  );
}

export default function AnalyzePage() {
  return (
    <Suspense>
      <Analyze />
    </Suspense>
  );
}
