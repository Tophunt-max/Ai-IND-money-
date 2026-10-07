"use client";

import Link from "next/link";

import { Badge, Button, Card, Empty, ErrorBox, Loading, PageTitle, StatCard } from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface SystemStatus {
  version: string;
  mode: string;
  broker: { name: string; token_valid: boolean; token_remaining: string | null; token_warning: string | null };
  services: { supabase: boolean; redis: boolean; llms: Record<string, boolean> };
}

interface Overview {
  mode: string;
  kill_switch: { halted: boolean; text: string };
  initial_paper_capital: number;
  positions: {
    symbol: string;
    quantity: number;
    entry_price: number;
    cost: number;
    opened_at: string | null;
    stop_loss: number | null;
    target: number | null;
  }[];
  realized_pnl: number;
  closed_trades: number;
  today_trades: number;
  invested: number;
  database: string;
}

export default function Home() {
  const status = useApi<SystemStatus>("/api/status", 60000);
  const ov = useApi<Overview>("/api/dashboard/overview", 30000);

  const s = status.data;
  const o = ov.data;
  const llms = s ? Object.entries(s.services.llms).filter(([, v]) => v).map(([k]) => k) : [];

  return (
    <div className="space-y-6">
      <PageTitle
        title="Dashboard"
        right={
          <div className="flex items-center gap-2">
            {o && <Badge tone={o.mode === "live" ? "error" : "warning"}>{o.mode.toUpperCase()}</Badge>}
            <Button variant="ghost" onClick={() => { status.reload(); ov.reload(); }}>
              Refresh
            </Button>
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
            title="Realized P&L"
            value={<span className={o.realized_pnl >= 0 ? "text-green-400" : "text-red-400"}>{inr(o.realized_pnl)}</span>}
            detail={`${o.closed_trades} closed trades`}
            tone={o.realized_pnl > 0 ? "ok" : o.realized_pnl < 0 ? "error" : "neutral"}
          />
          <StatCard title="Open positions" value={o.positions.length} detail={`Invested ${inr(o.invested, 0)}`} />
          <StatCard title="Trades today" value={o.today_trades} />
          <StatCard
            title="Paper capital"
            value={inr(o.initial_paper_capital, 0)}
            detail={o.mode === "paper" ? "Starting capital" : "Live mode"}
          />
        </div>
      ) : null}

      {/* Positions */}
      <Card title="Open positions">
        {o && o.positions.length === 0 ? (
          <Empty>No open positions. The scheduler opens paper trades on trading days from 09:15 IST.</Empty>
        ) : o ? (
          <div className="overflow-x-auto -mx-4 px-4">
            <table className="w-full text-sm min-w-[520px]">
              <thead className="text-gray-500 text-xs text-left">
                <tr>
                  <th className="py-2">Symbol</th><th>Qty</th><th>Entry</th><th>Cost</th>
                  <th>Stop / Target</th><th>Opened</th>
                </tr>
              </thead>
              <tbody>
                {o.positions.map((p, i) => (
                  <tr key={i} className="border-t border-gray-800">
                    <td className="py-2 font-medium">{p.symbol}</td>
                    <td>{p.quantity}</td>
                    <td>{inr(p.entry_price)}</td>
                    <td>{inr(p.cost, 0)}</td>
                    <td className="text-gray-400">
                      {p.stop_loss ? inr(p.stop_loss) : "—"} / {p.target ? inr(p.target) : "—"}
                    </td>
                    <td className="text-gray-400">{when(p.opened_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Loading />
        )}
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
          ["/analyze", "🧠 Analyze a stock"],
          ["/scanner", "🔎 Scan market"],
          ["/chat", "💬 Ask the AI"],
          ["/report", "📊 Track record"],
        ].map(([href, label]) => (
          <Link key={href} href={href} className="border border-gray-800 rounded-lg p-3 text-sm hover:bg-gray-900 text-center">
            {label}
          </Link>
        ))}
      </div>
    </div>
  );
}
