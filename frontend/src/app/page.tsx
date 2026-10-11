"use client";

import {
  Activity, ArrowRight, Brain, CalendarClock, Cpu, Database, LineChart,
  FlaskConical, OctagonX, PlugZap, Radar, RefreshCw, TrendingUp, Wallet, Zap,
} from "lucide-react";
import Link from "next/link";

import { BarChart, EquityChart } from "@/components/charts";
import ModeSwitch, { useModeChanged } from "@/components/ModeSwitch";
import RiskCapacity from "@/components/RiskCapacity";
import type { SwingStatus } from "@/components/SwingModel";
import {
  Badge, Button, Card, Change, Empty, ErrorBox, Notice, pnlClass, Segmented, Skeleton, StatCard,
} from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useMarketOpen } from "@/lib/market";
import { useState } from "react";
import { planLine, rcLabel, type SchedulerStatus } from "@/lib/scheduler";

interface SystemStatus {
  version: string;
  mode: string;
  broker: { name: string; token_valid: boolean; token_remaining: string | null; token_warning: string | null };
  services: { supabase: boolean; redis: boolean };
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
    <div className="flex items-center gap-2.5 py-2">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white/[0.04] ring-1 ring-white/[0.06]">
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

const PERIODS = [{ value: 30, label: "1M" }, { value: 90, label: "3M" }, { value: 365, label: "1Y" }, { value: 1825, label: "5Y" }] as const;

export default function Home() {
  const status = useApi<SystemStatus>("/api/status", 60000);
  const open = useMarketOpen();
  const [days, setDays] = useState<number>(90);
  const ov = useApi<Overview>("/api/dashboard/overview", open ? 15000 : 60000);
  const pnl = useApi<PnlHistory>(`/api/dashboard/pnl-history?days=${days}`, 120000);
  const sched = useApi<SchedulerStatus>("/api/dashboard/scheduler", 60000);
  const swing = useApi<SwingStatus>("/api/dashboard/swing", 300000);

  const s = status.data;
  const o = ov.data;
  const sw = swing.data;
  const todayKey = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const todayDaemon = sched.data?.days.find((d) => d.date === todayKey)?.jobs.daemon;
  const total = o ? o.realized_pnl + (o.unrealized_pnl || 0) : null;
  const capital = o?.mode === "paper" ? o.initial_paper_capital : 0;
  const pts = pnl.data?.points || [];
  const wins = pts.filter((p) => p.pnl > 0).length;
  const short = (d: string) => new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

  const refresh = () => {
    status.reload();
    ov.reload();
    pnl.reload();
    sched.reload();
  };
  useModeChanged(ov.reload);

  return (
    <div className="space-y-3 sm:space-y-4">
      {o?.kill_switch.halted && (
        <Notice tone="error" icon={OctagonX} title="Trading halted"
          action={<Link href="/control" className="text-sm font-medium underline">Manage</Link>}>
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

      {/* Account summary + equity */}
      <div className="grid gap-3 lg:grid-cols-3">
        <section className="surface p-3 sm:p-4 lg:col-span-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
                Total P&amp;L {o && (o.mode === "live" ? <Badge tone="error" dot>LIVE</Badge> : <Badge tone="warning" dot>PAPER</Badge>)}
              </div>
              {o ? (
                <div className={`num mt-1 text-3xl font-bold sm:text-4xl ${pnlClass(total)}`}>{inr(total)}</div>
              ) : <Skeleton className="mt-2 h-9 w-48" />}
              <div className="num mt-1 flex flex-wrap gap-x-3 text-xs text-gray-500">
                {o && <>
                  <span>Realized <b className={pnlClass(o.realized_pnl)}>{inr(o.realized_pnl)}</b></span>
                  <span>Unrealized <b className={pnlClass(o.unrealized_pnl)}>{inr(o.unrealized_pnl)}</b></span>
                  {capital > 0 && total != null && <Change value={total / capital} size="xs" />}
                </>}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Segmented size="sm" value={days} onChange={setDays} options={PERIODS} />
              <Button variant="ghost" size="sm" icon={RefreshCw} onClick={refresh} loading={ov.loading && !!o} title="Refresh" />
            </div>
          </div>
          <div className="mt-3">
            <ErrorBox error={pnl.error && pnl.error !== ov.error && o?.database === "ok" ? pnl.error : null} />
            {pts.length >= 2 ? (
              <EquityChart title="Cumulative realized P&L" base={capital}
                points={pts.map((p) => ({ label: short(p.date), v: p.cumulative }))}
                baseline={0} height={230} format={(v) => inr(v, 0)} />
            ) : pnl.data ? (
              <div className="rounded-lg border border-dashed border-white/[0.08] py-8 text-center text-sm text-gray-500">
                {pts.length === 1 ? `One trading day so far: ${inr(pnl.data.total)}.` : "The equity curve appears after two days with closed trades."}
              </div>
            ) : !pnl.error ? <Skeleton className="h-56 w-full" /> : pnl.error === ov.error || o?.database !== "ok" ? (
              <div className="rounded-lg border border-dashed border-white/[0.08] py-8 text-center text-sm text-gray-500">The equity curve needs the database.</div>
            ) : null}
          </div>
        </section>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-1 lg:content-start">
          {o ? (
            <>
              <StatCard title="Open positions" icon={Activity} tone="info" value={o.positions.length} detail={`Invested ${inr(o.invested, 0)}`} />
              <StatCard title="Trades today" icon={Zap} value={o.today_trades} detail={`${o.closed_trades} closed in total`} />
              <StatCard title="Market value" icon={Wallet} value={o.market_value != null ? inr(o.market_value, 0) : "—"} detail={o.price_source || "open positions"} />
              <StatCard title="Winning days" icon={TrendingUp} tone={pts.length ? (wins / pts.length >= 0.5 ? "ok" : "error") : "neutral"}
                value={pts.length ? `${Math.round((wins / pts.length) * 100)}%` : "—"} detail={`${wins} of ${pts.length} days`} />
            </>
          ) : [0, 1, 2, 3].map((i) => <div key={i} className="surface p-3"><Skeleton className="h-3 w-20" /><Skeleton className="mt-2 h-6 w-24" /></div>)}
        </div>
      </div>

      {/* Daily P&L */}
      {pts.length > 0 && (
        <Card title="Daily P&L" subtitle={`Realized per trading day · last ${Math.min(pts.length, 60)} days`}>
          <BarChart bars={pts.slice(-60).map((p) => ({ label: short(p.date), v: p.pnl, hint: p.date }))} height={140} format={(v) => inr(v, 0)} />
        </Card>
      )}

      {/* Paper: the paper capital; live: the broker's equity is not in the overview, so the
          card shows the loss without its rupee limit (the % stop still applies) */}
      <RiskCapacity portfolioValue={o?.mode === "paper" ? o.initial_paper_capital + (o.realized_pnl || 0) : null} />

      <div className="grid gap-3 lg:grid-cols-3">
        {/* Positions */}
        <Card title="Open positions" icon={Activity} className="lg:col-span-2" padded
          right={o?.price_source && <Badge tone={o.price_source.startsWith("INDstocks") ? "ok" : "warning"} dot>{o.price_source}</Badge>}>
          {o && o.positions.length === 0 ? (
            <Empty icon={Activity} title="No open positions">The scheduler opens trades on NSE trading days from 09:15 IST.</Empty>
          ) : o ? (
            <div className="-mx-3 divide-y divide-white/[0.04] sm:-mx-4">
              {o.positions.map((p, i) => (
                <Link key={i} href={`/market?symbol=${encodeURIComponent(p.symbol)}`}
                  className="flex items-center gap-3 px-3 py-2.5 transition hover:bg-white/[0.03] sm:px-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-white">{p.symbol}</span>
                      <span className="num text-[11px] text-gray-500">{p.quantity} qty</span>
                    </div>
                    <div className="num truncate text-[11px] text-gray-500">
                      Avg {inr(p.entry_price)} · SL {p.stop_loss ? inr(p.stop_loss) : "—"} · TG {p.target ? inr(p.target) : "—"}
                    </div>
                  </div>
                  <div className="num text-right">
                    <div className={`text-sm font-semibold ${pnlClass(p.unrealized_pnl)}`}>{inr(p.unrealized_pnl)}</div>
                    <div className="flex items-center justify-end gap-1.5 text-[11px] text-gray-400">
                      LTP {inr(p.ltp)} {p.unrealized_pct != null && <Change value={p.unrealized_pct} size="xs" pill={false} />}
                    </div>
                    <div className="text-[10px] text-gray-600">{when(p.opened_at)}</div>
                  </div>
                </Link>
              ))}
            </div>
          ) : <div className="space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
        </Card>

        <div className="space-y-3">
          <Link href="/scheduler" className="surface group block p-3 transition hover:border-brand-500/30 sm:p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-gray-200">
                <CalendarClock className="h-4 w-4 text-brand-300" /> Auto-trading
              </div>
              <ArrowRight className="h-4 w-4 text-gray-600 transition group-hover:translate-x-0.5 group-hover:text-brand-300" />
            </div>
            {sched.data?.ok ? (
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <div><div className="text-[10px] uppercase text-gray-500">Today</div>
                  <div className="mt-1">{todayDaemon ? <Badge tone={rcLabel(todayDaemon).tone}>{rcLabel(todayDaemon).text}</Badge>
                    : <span className="text-gray-200">{planLine(sched.data.lines, "Today") || "—"}</span>}</div></div>
                <div><div className="text-[10px] uppercase text-gray-500">Next</div>
                  <div className="mt-1 text-gray-200">{planLine(sched.data.lines, "Next session") || "—"}</div></div>
                <div><div className="text-[10px] uppercase text-gray-500">Mode</div>
                  <div className="mt-1"><Badge tone={!sched.data.enabled ? "neutral" : sched.data.mode === "live" ? "error" : "warning"} dot>
                    {sched.data.enabled ? (sched.data.mode || "").toUpperCase() : "OFF"}</Badge></div></div>
              </div>
            ) : sched.data ? <p className="mt-3 text-sm text-rose-300">{sched.data.error}</p>
              : sched.error ? <p className="mt-3 text-sm text-rose-300">{sched.error}</p>
              : <div className="mt-3 space-y-2"><Skeleton /><Skeleton className="h-4 w-2/3" /></div>}
          </Link>

          <Card title="System" icon={Cpu} subtitle={s ? `Backend v${s.version}` : undefined}>
            <ErrorBox error={status.error} />
            {s ? (
              <div className="-my-2 divide-y divide-white/[0.04]">
                <StatusRow icon={PlugZap} label={`Broker · ${s.broker.name}`} value="" ok={s.broker.token_valid}
                  detail={s.broker.token_valid ? `Token expires in ${s.broker.token_remaining}` : "No valid token"} />
                <StatusRow icon={Database} label="Supabase" value="" ok={o?.database === "ok" ? true : s.services.supabase ? null : false}
                  detail={o?.database === "ok" ? "Connected" : s.services.supabase ? "Configured" : "Not configured"} />
                <StatusRow icon={Zap} label="Upstash Redis" value="" ok={s.services.redis ? true : null}
                  detail={s.services.redis ? "Connected" : "Not configured (optional)"} />
                <StatusRow icon={Brain} label="Adaptive AI · swing model" value="" ok={sw ? (sw.active ? true : sw.rules_only ? null : false) : null}
                  detail={!sw ? (swing.error ? "Unavailable" : "…") : sw.active ? `${sw.active} (${sw.active === sw.champion ? "champion" : "shadow"})` : sw.rules_only ? "No model · rules only (paper)" : "No model trained yet"} />
              </div>
            ) : !status.error ? <div className="space-y-2"><Skeleton className="h-9" /><Skeleton className="h-9" /></div> : null}
          </Card>
        </div>
      </div>

      <ModeSwitch compact />

      {/* Quick actions */}
      <div className="grid grid-cols-4 gap-2">
        {[
          { href: "/market", label: "Charts", icon: LineChart },
          { href: "/scanner", label: "Swing scan", icon: Radar },
          { href: "/backtest", label: "Backtest", icon: FlaskConical },
          { href: "/learning", label: "AI learning", icon: Brain },
        ].map((a) => (
          <Link key={a.href} href={a.href}
            className="surface flex flex-col items-center gap-1.5 p-3 text-center text-[11px] font-semibold text-gray-300 transition hover:border-brand-500/30 hover:text-white sm:flex-row sm:justify-center sm:text-[13px]">
            <a.icon className="h-5 w-5 text-brand-300" />
            {a.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
