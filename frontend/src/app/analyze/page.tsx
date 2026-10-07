"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";

import { actionTone, Badge, Button, Card, Collapsible, ErrorBox, PageTitle, StatCard } from "@/components/ui";
import { inr } from "@/lib/api";
import { useJob } from "@/lib/hooks";

const REPORT_NAMES: Record<string, string> = {
  market_report: "📈 Market analyst",
  sentiment_report: "🗣️ Social analyst",
  news_report: "📰 News analyst",
  fundamentals_report: "🏦 Fundamentals analyst",
  "investment_debate_state.judge_decision": "⚖️ Research manager (bull vs bear)",
  investment_plan: "📝 Investment plan",
  trader_investment_plan: "💼 Trader",
  "risk_debate_state.judge_decision": "🛡️ Portfolio manager (risk debate)",
  final_trade_decision: "✅ Final decision",
};

function Analyze() {
  const params = useSearchParams();
  const [symbol, setSymbol] = useState(params.get("symbol") || "");
  const { job, error, running, start } = useJob();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!running || !job) return;
    const t = setInterval(() => setElapsed(Math.round(Date.now() / 1000 - job.created_at)), 1000);
    return () => clearInterval(t);
  }, [running, job]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (symbol.trim()) start({ kind: "analyze", symbol: symbol.trim().toUpperCase() });
  };

  const r = job?.status === "done" || job?.status === "failed" ? job.result : null;
  const reports = r?.reports ? Object.entries(r.reports as Record<string, string>) : [];
  reports.sort(([a], [b]) => {
    const order = Object.keys(REPORT_NAMES);
    return (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99);
  });

  return (
    <div className="space-y-5">
      <PageTitle title="Analyze a stock" />
      <Card>
        <p className="text-sm text-gray-400 mb-3">
          Runs the full multi-agent analysis (analysts → bull/bear debate → trader → risk). Takes
          2–5 minutes. No order is placed.
        </p>
        <form onSubmit={submit} className="flex gap-2">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="RELIANCE, TCS, INFY..."
            className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm uppercase"
          />
          <Button type="submit" disabled={running || !symbol.trim()}>
            {running ? "Running..." : "Analyze"}
          </Button>
        </form>
      </Card>

      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />
      {running && (
        <div className="animate-pulse text-gray-400 text-sm">
          Agents are working on {job?.symbol}... {elapsed}s (keep this page open)
        </div>
      )}

      {r?.signal && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard
              title="Decision"
              value={<Badge tone={actionTone(r.signal.action)}>{r.signal.action}</Badge>}
              detail={`${r.symbol} · ${r.trade_date}`}
              tone={actionTone(r.signal.action)}
            />
            <StatCard title="Confidence" value={`${r.signal.confidence}%`} />
            <StatCard
              title="Entry / Stop / Target"
              value={inr(r.signal.entry_price)}
              detail={`Stop ${inr(r.signal.stop_loss)} · Target ${inr(r.signal.target)}`}
            />
            <StatCard title="Time taken" value={`${r.duration_seconds}s`} />
          </div>
          {r.signal.reasoning && (
            <Card title="Reasoning">
              <p className="text-sm text-gray-300 whitespace-pre-wrap">{r.signal.reasoning}</p>
            </Card>
          )}
        </>
      )}

      {reports.length > 0 && (
        <Card title="Agent reports">
          <div className="space-y-2">
            {reports.map(([key, text]) => (
              <Collapsible key={key} title={REPORT_NAMES[key] || key} text={text} />
            ))}
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
