"use client";

import Link from "next/link";

import Chart from "@/components/Chart";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageTitle, StatCard } from "@/components/ui";
import { inr, pct, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { planLine, rcLabel, type SchedulerStatus } from "@/lib/scheduler";

interface SystemStatus {
  version: string;
  mode: string;
  broker: { name: string; token_valid: boolean; token_remaining: string | null; token_warning: string | null };
  services: { supabase: boolean; redis: boolean; llms: Record<string, boolean> };
}

interface Position {
  symbol: string;
  quantity: number;
  entry_price: number;
  cost: number;
  opened_at: string | null;
  stop_loss: number | null;
  target: number | null;
  ltp?: number | null;
  market_value?: number | null;
  unrealized_pnl?: number | null;
  unrealized_pct?: number | null;
  day_change_pct?: number | null;
}

interface Overview {
  mode: string;
  kill_switch: { halted: boolean; text: string };
  initial_paper_capital: number;
  positions: Position[];
  realized_pnl: number;
  unrealized_pnl: number | null;
  market_value: number | null;
  closed_trades: number;
  today_trades: number;
  invested: number;
  database: string;
  price_source?: string;
}

interface PnlHistory {
  points: { date: string; pnl: number; cumulative: number }[];
  total: number;
}

const color = (v: number | null | undefined) =>
  v == null ? "" : v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "";

export default function Home() {
  const status = useApi<SystemStatus>("/api/status", 60000);
  const ov = useApi<Overview>("/api/dashboard/overview", 30000);
  const pnl = useApi<PnlHistory>("/api/dashboard/pnl-history?days=90", 120000);
  const sched = useApi<SchedulerStatus>("/api/dashboard/scheduler", 60000);

  const s = status.data;
  const o = ov.data;
  const llms = s ? Object.entries(s.services.llms).filter(([, v]) => v).map(([k]) => k) : [];
  const today = sched.data?.days.find((d) => d.date === new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }));
  const todayDaemon = today?.jobs.daemon;

  const refresh = () => {
    status.reload();
    ov.reload();
    pnl.reload();
    sched.reload();
  };

  return (
    <div className="space-y-6">
      <PageTitle
        title="Dashboard"
        right={
          <div className="flex items-center gap-2">
            {o && <Badge tone={o.mode === "live" ? "error" : "warning"}>{o.mode.toUpperCase()}</Badge>}
            <Button variant="ghost" onClick={refresh}>Refresh</Button>
          </div>
        }
      />

      {o?.kill_switch.halted && (
        <div className="border border-red-700 bg-red-900/30 rounded-lg p-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="font-semibold text-red-300">🛑 Trading HALTED</div>
            <div className="text-sm text-red-200/80">{o.kill_switch.text}</div>
          </div>
          <Link href="/settings" className="text-sm underline text-red-200">Manage</Link>
        </div>
      )}

      <ErrorBox error={ov.error} />
      {o && o.database !== "ok" && <ErrorBox error={`Database: ${o.database}`} />}

      {/* P&L summary */}
      {ov.loading && !o ? (
        <Loading />
      ) : o ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            title="Unrealized P&L"
            value={<span className={color(o.unrealized_pnl)}>{inr(o.unrealized_pnl)}</span>}
            detail={o.market_value != null ? `Value ${inr(o.market_value, 0)}` : "Open positions at market"}
            tone={o.unrealized_pnl == null ? "neutral" : o.unrealized_pnl >= 0 ? "ok" : "error"}
          />
          <StatCard
            title="Realized P&L"
            value={<span className={color(o.realized_pnl)}>{inr(o.realized_pnl)}</span>}
            detail={`${o.closed_trades} closed trades`}
            tone={o.realized_pnl > 0 ? "ok" : o.realized_pnl < 0 ? "error" : "neutral"}
          />
          <StatCard title="Open positions" value={o.positions.length} detail={`Invested ${inr(o.invested, 0)}`} />
          <StatCard
            title="Trades today"
            value={o.today_trades}
            detail={o.mode === "paper" ? `Paper capital ${inr(o.initial_paper_capital, 0)}` : "Live mode"}
          />
        </div>
      ) : null}

      {/* Scheduler */}
      <Link href="/scheduler" className="block">
        <Card
          title="⏰ Auto-trading session"
          right={<span className="text-xs text-blue-400">Details →</span>}
          className="hover:bg-gray-900/70"
        >
          {sched.data?.ok ? (
            <div className="text-sm space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-gray-500 w-14">Today</span>
                {todayDaemon ? (
                  <Badge tone={rcLabel(todayDaemon).tone}>{rcLabel(todayDaemon).text}</Badge>
                ) : (
                  <span className="text-gray-300">{planLine(sched.data.lines, "Today")}</span>
                )}
              </div>
              <div className="flex gap-2">
                <span className="text-gray-500 w-14 shrink-0">Next</span>
                <span className="text-gray-300">{planLine(sched.data.lines, "Next session")}</span>
              </div>
            </div>
          ) : sched.data ? (
            <div className="text-sm text-red-300">{sched.data.error}</div>
          ) : sched.error ? (
            <div className="text-sm text-red-300">{sched.error}</div>
          ) : (
            <Loading />
          )}
        </Card>
      </Link>

      {/* Positions */}
      <Card title="Open positions" right={o?.price_source && <span className="text-[10px] text-gray-500">{o.price_source}</span>}>
        {o && o.positions.length === 0 ? (
          <Empty>No open positions. The scheduler opens paper trades on trading days from 09:15 IST.</Empty>
        ) : o ? (
          <div className="divide-y divide-gray-800">
            {o.positions.map((p, i) => (
              <Link key={i} href={`/market?symbol=${encodeURIComponent(p.symbol)}`} className="block py-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium">{p.symbol}</div>
                    <div className="text-xs text-gray-500">
                      {p.quantity} @ {inr(p.entry_price)} · {when(p.opened_at)}
                    </div>
                    <div className="text-xs text-gray-500">
                      Stop {p.stop_loss ? inr(p.stop_loss) : "—"} · Target {p.target ? inr(p.target) : "—"}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm">{inr(p.ltp)}</div>
                    <div className={`text-sm font-semibold ${color(p.unrealized_pnl)}`}>
                      {inr(p.unrealized_pnl)}{" "}
                      <span className="text-xs">{p.unrealized_pct != null && `(${pct(p.unrealized_pct, 1)})`}</span>
                    </div>
                    {p.day_change_pct != null && (
                      <div className={`text-[11px] ${color(p.day_change_pct)}`}>today {pct(p.day_change_pct, 2)}</div>
                    )}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <Loading />
        )}
      </Card>

      {/* P&L chart */}
      <Card title="Realized P&L · last 90 days">
        <ErrorBox error={pnl.error} />
        {pnl.data && pnl.data.points.length >= 2 ? (
          <Chart
            points={pnl.data.points.map((p) => ({ t: Date.parse(p.date) / 1000, v: p.cumulative, label: p.date }))}
            baseline={0}
            height={180}
            format={(v) => inr(v)}
          />
        ) : pnl.data ? (
          <Empty>
            {pnl.data.points.length === 1
              ? `One trading day so far: ${inr(pnl.data.total)}.`
              : "No closed trades yet. The chart appears after two days with closed trades."}
          </Empty>
        ) : !pnl.error ? (
          <Loading />
        ) : null}
      </Card>

      {/* System status */}
      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-3">System</h3>
        <ErrorBox error={status.error} />
        {s ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard
              title="Broker"
              value={s.broker.name}
              tone={s.broker.token_valid ? "ok" : "error"}
              detail={
                s.broker.token_valid
                  ? `Token expires in ${s.broker.token_remaining}`
                  : "No token (needed for live trading and scanner quotes)"
              }
            />
            <StatCard
              title="Database"
              value="Supabase"
              tone={o?.database === "ok" ? "ok" : s.services.supabase ? "warning" : "error"}
              detail={o?.database === "ok" ? "Connected" : s.services.supabase ? "Configured" : "Not configured"}
            />
            <StatCard
              title="Cache"
              value="Upstash Redis"
              tone={s.services.redis ? "ok" : "neutral"}
              detail={s.services.redis ? "Connected" : "Not configured (optional)"}
            />
            <StatCard
              title="LLM models"
              value={`${llms.length} active`}
              tone={llms.length ? "ok" : "error"}
              detail={llms.join(", ") || "No LLM keys configured"}
            />
          </div>
        ) : !status.error ? (
          <Loading />
        ) : null}
        {s && <p className="text-xs text-gray-600 mt-3">Backend v{s.version}</p>}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          ["/market", "📈 Prices & charts"],
          ["/scanner", "🔎 Scan market"],
          ["/chat", "💬 Ask the AI"],
          ["/scheduler", "⏰ Sessions & logs"],
        ].map(([href, label]) => (
          <Link key={href} href={href} className="border border-gray-800 rounded-lg p-3 text-sm hover:bg-gray-900 text-center">
            {label}
          </Link>
        ))}
      </div>
    </div>
  );
}
