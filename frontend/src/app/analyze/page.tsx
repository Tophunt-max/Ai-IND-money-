"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";

import { actionTone, Badge, Button, Card, Collapsible, ErrorBox, PageTitle, StatCard } from "@/components/ui";
import { inr } from "@/lib/api";
import PriceChart from "@/components/PriceChart";
import { useAuth } from "@/components/AuthGate";
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

  return (
    <div className="space-y-5">
      <PageTitle title={trade ? "Analyze + paper trade" : "Analyze a stock"} />
      <Card>
        <p className="text-sm text-gray-400 mb-3">
          Runs the full multi-agent analysis (analysts → bull/bear debate → trader → risk). Takes
          2–5 minutes.{" "}
          {trade
            ? "Then places a PAPER order (simulated, fills at the current price) if the decision passes the safety checks."
            : "No order is placed."}
        </p>
        <div className="flex gap-2 mb-3">
          {[
            [false, "🧠 Analyze only"],
            [true, "📝 Analyze + paper trade"],
          ].map(([v, label]) => (
            <button
              key={String(v)}
              type="button"
              onClick={() => setTrade(v as boolean)}
              className={`px-3 py-1 rounded-full text-xs border ${
                trade === v ? "border-blue-500 text-blue-300" : "border-gray-700 text-gray-400"
              }`}
            >
              {label as string}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="flex gap-2">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="RELIANCE, TCS, INFY..."
            className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm uppercase"
          />
          <Button type="submit" disabled={!isAdmin || running || !symbol.trim()}>
            {running ? "Running..." : trade ? "Analyze + trade" : "Analyze"}
          </Button>
        </form>
      </Card>

      {!isAdmin && (
        <div className="border border-gray-700 bg-gray-900/50 rounded-lg p-3 text-sm text-gray-400">
          👀 View-only account: running this needs an admin.
        </div>
      )}
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
          {job?.kind === "trade" && (
            <Card title="📝 Paper order">
              {!r.execution ? (
                <p className="text-sm text-gray-400">
                  No order: the decision was {r.signal.action}, so nothing was traded.
                </p>
              ) : r.execution.success ? (
                <div className="text-sm space-y-1">
                  <div className="text-green-400 font-semibold">✅ Paper order filled</div>
                  <div className="text-gray-300">
                    {r.signal.action} {r.execution.quantity ?? "—"} × {r.symbol} @ {inr(r.execution.fill_price)}
                  </div>
                  <div className="text-xs text-gray-500">
                    Brokerage {inr(r.execution.brokerage)} · order {r.execution.order_id || "—"} · see Trades
                  </div>
                </div>
              ) : (
                <div className="text-sm space-y-1">
                  <div className="text-yellow-400 font-semibold">
                    {r.execution.safety_passed ? "Order not filled" : "🛡️ Blocked by the safety checks"}
                  </div>
                  <div className="text-gray-300">{r.execution.rejection_reason || "No reason given"}</div>
                </div>
              )}
            </Card>
          )}
          {r.signal.reasoning && (
            <Card title="Reasoning">
              <p className="text-sm text-gray-300 whitespace-pre-wrap">{r.signal.reasoning}</p>
            </Card>
          )}
          <Card title={`${r.symbol} price`}>
            <PriceChart symbol={r.symbol} initial="1mo" />
          </Card>
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
