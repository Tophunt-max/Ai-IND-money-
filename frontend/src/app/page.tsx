"use client";

import {
  Activity, ArrowRight, Brain, CalendarClock, Cpu, Database, LayoutDashboard, LineChart,
  MessagesSquare, OctagonX, PlugZap, Radar, RefreshCw, TrendingUp, Wallet, Zap,
} from "lucide-react";
import Link from "next/link";

import Chart from "@/components/Chart";
import {
  Badge, Button, Card, Empty, ErrorBox, Notice, PageTitle, pnlClass, Skeleton, StatCard,
} from "@/components/ui";
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

const tone = (v: number | null | undefined) => (v == null || v === 0 ? "neutral" : v > 0 ? "ok" : "error");

function StatusRow({ icon: Icon, label, value, ok, detail }: {
  icon: typeof Cpu; label: string; value: string; ok: boolean | null; detail?: string;
}) {
  return (
    <div className="flex items-center gap-3 py-3">
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white/[0.04] ring-1 ring-white/[0.06]">
        <Icon className="h-4 w-4 text-gray-400" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-200">{label}</div>
        <div className="truncate text-xs text-gray-500">{detail || value}</div>
      </div>
      <Badge tone={ok === null ? "neutral" : ok ? "ok" : "error"} dot>{ok === null ? "Optional" : ok ? "OK" : "Check"}</Badge>
    </div>
  );
}

export default function Home() {
  const status = useApi<SystemStatus>("/api/status", 60000);
  const ov = useApi<Overview>("/api/dashboard/overview", 30000);
  const pnl = useApi<PnlHistory>("/api/dashboard/pnl-history?days=90", 120000);
  const sched = useApi<SchedulerStatus>("/api/dashboard/scheduler", 60000);

  const s = status.data;
  const o = ov.data;
  const llms = s ? Object.entries(s.services.llms).filter(([, v]) => v).map(([k]) => k) : [];
  const todayKey = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const todayDaemon = sched.data?.days.find((d) => d.date === todayKey)?.jobs.daemon;

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
        icon={LayoutDashboard}
        subtitle="Your AI trading desk at a glance"
        right={<Button variant="ghost" icon={RefreshCw} onClick={refresh} loading={ov.loading && !!o}>Refresh</Button>}
      />

      {o?.kill_switch.halted && (
        <Notice tone="error" icon={OctagonX} title="Trading halted"
          action={<Link href="/settings" className="text-sm font-medium underline">Manage</Link>}>
          {o.kill_switch.text}
        </Notice>
      )}
      {s && !s.broker.token_valid && (
        <Notice tone="warning" icon={PlugZap} title="No valid INDstocks token"
          action={<Link href="/broker" className="text-sm font-medium underline">Set token</Link>}>
          Today&apos;s auto-trading session cannot start without it.
        </Notice>
      )}
      <ErrorBox error={ov.error} />
      {o && o.database !== "ok" && <ErrorBox error={`Database: ${o.database}`} />}

      {/* Hero P&L */}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="surface relative overflow-hidden p-6 lg:col-span-2">
          <div className="absolute -right-16 -top-16 h-56 w-56 rounded-full bg-brand-500/20 blur-3xl" />
          <div className="relative flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm text-gray-400">
                Total P&L {o && (o.mode === "live" ? <Badge tone="error" dot>LIVE</Badge> : <Badge tone="warning" dot>PAPER</Badge>)}
              </div>
              {o ? (
                <div className={`num mt-2 text-4xl font-semibold tracking-tight ${pnlClass(o.realized_pnl + (o.unrealized_pnl || 0))}`}>
                  {inr(o.realized_pnl + (o.unrealized_pnl || 0))}
                </div>
              ) : <Skeleton className="mt-3 h-10 w-48" />}
              <div className="mt-2 text-sm text-gray-500">
                {o ? `Realized ${inr(o.realized_pnl)} · Unrealized ${inr(o.unrealized_pnl)}` : " "}
              </div>
            </div>
            {o?.mode === "paper" && (
              <div className="text-right">
                <div className="text-xs text-gray-500">Paper capital</div>
                <div className="num text-lg font-semibold text-gray-200">{inr(o.initial_paper_capital, 0)}</div>
              </div>
            )}
          </div>
          <div className="relative mt-6">
            <ErrorBox error={pnl.error && pnl.error !== ov.error ? pnl.error : null} />
            {pnl.data && pnl.data.points.length >= 2 ? (
              <Chart
                points={pnl.data.points.map((p) => ({ t: Date.parse(p.date) / 1000, v: p.cumulative, label: p.date }))}
                baseline={0}
                height={170}
                format={(v) => inr(v)}
              />
            ) : pnl.data ? (
              <div className="rounded-xl border border-dashed border-white/[0.08] py-8 text-center text-sm text-gray-500">
                {pnl.data.points.length === 1
                  ? `One trading day so far: ${inr(pnl.data.total)}.`
                  : "The 90-day P&L chart appears after two days with closed trades."}
              </div>
            ) : !pnl.error ? <Skeleton className="h-40 w-full" /> : pnl.error === ov.error ? (
              <div className="rounded-xl border border-dashed border-white/[0.08] py-8 text-center text-sm text-gray-500">The P&L chart needs the database.</div>
            ) : null}
          </div>
        </div>

        <Link href="/scheduler" className="surface group flex flex-col p-6 transition hover:border-brand-500/30">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-100">
              <CalendarClock className="h-4 w-4 text-brand-300" /> Auto-trading
            </div>
            <ArrowRight className="h-4 w-4 text-gray-600 transition group-hover:translate-x-0.5 group-hover:text-brand-300" />
          </div>
          {sched.data?.ok ? (
            <div className="mt-5 space-y-4 text-sm">
              <div>
                <div className="text-xs text-gray-500">Today</div>
                <div className="mt-1">
                  {todayDaemon ? <Badge tone={rcLabel(todayDaemon).tone}>{rcLabel(todayDaemon).text}</Badge>
                    : <span className="text-gray-200">{planLine(sched.data.lines, "Today") || "—"}</span>}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Next session</div>
                <div className="mt-1 text-gray-200">{planLine(sched.data.lines, "Next session") || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Mode</div>
                <div className="mt-1">
                  <Badge tone={!sched.data.enabled ? "neutral" : sched.data.mode === "live" ? "error" : "warning"} dot>
                    {sched.data.enabled ? (sched.data.mode || "").toUpperCase() : "DISABLED"}
                  </Badge>
                </div>
              </div>
            </div>
          ) : sched.data ? (
            <p className="mt-4 text-sm text-rose-300">{sched.data.error}</p>
          ) : sched.error ? (
            <p className="mt-4 text-sm text-rose-300">{sched.error}</p>
          ) : (
            <div className="mt-5 space-y-3"><Skeleton /><Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-1/3" /></div>
          )}
        </Link>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {o ? (
          <>
            <StatCard title="Unrealized P&L" icon={TrendingUp} tone={tone(o.unrealized_pnl)}
              value={<span className={pnlClass(o.unrealized_pnl)}>{inr(o.unrealized_pnl)}</span>}
              detail={o.market_value != null ? `Value ${inr(o.market_value, 0)}` : "Open positions at market"} />
            <StatCard title="Realized P&L" icon={Wallet} tone={tone(o.realized_pnl)}
              value={<span className={pnlClass(o.realized_pnl)}>{inr(o.realized_pnl)}</span>}
              detail={`${o.closed_trades} closed trades`} />
            <StatCard title="Open positions" icon={Activity} tone="info" value={o.positions.length} detail={`Invested ${inr(o.invested, 0)}`} />
            <StatCard title="Trades today" icon={Zap} value={o.today_trades} detail={o.mode === "live" ? "Live mode" : "Paper mode"} />
          </>
        ) : [0, 1, 2, 3].map((i) => <div key={i} className="surface p-4"><Skeleton className="h-3 w-20" /><Skeleton className="mt-3 h-6 w-28" /></div>)}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Positions */}
        <Card title="Open positions" icon={Activity} className="lg:col-span-2" padded
          right={o?.price_source && <Badge tone={o.price_source.startsWith("INDstocks") ? "ok" : "warning"} dot>{o.price_source}</Badge>}>
          {o && o.positions.length === 0 ? (
            <Empty icon={Activity} title="No open positions">The scheduler opens trades on NSE trading days from 09:15 IST.</Empty>
          ) : o ? (
            <div className="-mx-2 divide-y divide-white/[0.04]">
              {o.positions.map((p, i) => (
                <Link key={i} href={`/market?symbol=${encodeURIComponent(p.symbol)}`}
                  className="flex items-center gap-3 rounded-xl px-2 py-3 transition hover:bg-white/[0.03]">
                  <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-ink-700 to-ink-800 text-xs font-bold text-gray-200 ring-1 ring-white/[0.06]">
                    {p.symbol.slice(0, 2)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-white">{p.symbol}</div>
                    <div className="num truncate text-xs text-gray-500">
                      {p.quantity} @ {inr(p.entry_price)} · SL {p.stop_loss ? inr(p.stop_loss) : "—"} · TG {p.target ? inr(p.target) : "—"}
                    </div>
                  </div>
                  <div className="num text-right">
                    <div className="text-sm text-gray-200">{inr(p.ltp)}</div>
                    <div className={`text-sm font-semibold ${pnlClass(p.unrealized_pnl)}`}>
                      {inr(p.unrealized_pnl)} {p.unrealized_pct != null && <span className="text-xs">({pct(p.unrealized_pct, 1)})</span>}
                    </div>
                    <div className="text-[11px] text-gray-500">{when(p.opened_at)}</div>
                  </div>
                </Link>
              ))}
            </div>
          ) : <div className="space-y-3"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
        </Card>

        {/* System */}
        <Card title="System health" icon={Cpu} subtitle={s ? `Backend v${s.version}` : undefined}>
          <ErrorBox error={status.error} />
          {s ? (
            <div className="-my-3 divide-y divide-white/[0.04]">
              <StatusRow icon={PlugZap} label={`Broker · ${s.broker.name}`} value="" ok={s.broker.token_valid}
                detail={s.broker.token_valid ? `Token expires in ${s.broker.token_remaining}` : "No valid token"} />
              <StatusRow icon={Database} label="Supabase" value="" ok={o?.database === "ok" ? true : s.services.supabase ? null : false}
                detail={o?.database === "ok" ? "Connected" : s.services.supabase ? "Configured" : "Not configured"} />
              <StatusRow icon={Zap} label="Upstash Redis" value="" ok={s.services.redis ? true : null}
                detail={s.services.redis ? "Connected" : "Not configured (optional)"} />
              <StatusRow icon={Brain} label="LLM models" value="" ok={llms.length > 0}
                detail={llms.join(", ") || "No LLM keys configured"} />
            </div>
          ) : !status.error ? <div className="space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div> : null}
        </Card>
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { href: "/analyze", label: "Analyze a stock", icon: Brain },
          { href: "/market", label: "Prices & charts", icon: LineChart },
          { href: "/scanner", label: "Scan market", icon: Radar },
          { href: "/chat", label: "Ask the AI", icon: MessagesSquare },
        ].map((a) => (
          <Link key={a.href} href={a.href}
            className="surface group flex items-center gap-3 p-4 text-sm font-medium text-gray-200 transition hover:border-brand-500/30 hover:text-white">
            <a.icon className="h-5 w-5 text-brand-300" />
            {a.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
