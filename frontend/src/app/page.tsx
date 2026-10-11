"use client";

import {
  Activity, ArrowRight, BellPlus, Brain, CalendarClock, Cpu, Database, Grid3x3, OctagonX, PlugZap, Radar,
  RefreshCw, ShoppingCart, Zap,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { EquityChart } from "@/components/charts";
import { IdeasStrip } from "@/components/IdeaCards";
import { IndexCards, Movers, StockRow, Watchlist } from "@/components/MarketWidgets";
import ModeSwitch, { useModeChanged } from "@/components/ModeSwitch";
import { useOrderTicket } from "@/components/OrderTicket";
import RiskCapacity from "@/components/RiskCapacity";
import type { SwingStatus } from "@/components/SwingModel";
import { Badge, Button, Card, Change, Empty, ErrorBox, Notice, pnlClass, Segmented, Skeleton } from "@/components/ui";
import { inr } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useQuotes } from "@/lib/live";
import { useMarketOpen } from "@/lib/market";
import { usePrefs } from "@/lib/prefs";
import { planLine, rcLabel, type SchedulerStatus } from "@/lib/scheduler";
import type { Overview, PnlHistory } from "@/lib/types";

interface SystemStatus {
  version: string;
  mode: string;
  broker: { name: string; token_valid: boolean; token_remaining: string | null; token_warning: string | null };
  services: { supabase: boolean; redis: boolean };
}

function StatusRow({ icon: Icon, label, ok, detail }: { icon: typeof Cpu; label: string; ok: boolean | null; detail?: string }) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white/[0.05]"><Icon className="h-4 w-4 text-gray-400" /></div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-200">{label}</div>
        <div className="truncate text-xs text-gray-500">{detail}</div>
      </div>
      <Badge tone={ok === null ? "neutral" : ok ? "ok" : "error"} dot>{ok === null ? "Optional" : ok ? "OK" : "Check"}</Badge>
    </div>
  );
}

const PERIODS = [{ value: 30, label: "1M" }, { value: 90, label: "3M" }, { value: 365, label: "1Y" }, { value: 1825, label: "All" }] as const;

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", hourCycle: "h23" }).format(new Date()));
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function Hero({ o, pnl, days, setDays }: { o: Overview | null; pnl: PnlHistory | null; days: number; setDays: (d: number) => void }) {
  const { quotes } = useQuotes(o?.positions.map((p) => p.symbol) || []);
  // Unrealized P&L at live prices (the overview is refreshed less often)
  const live = o ? o.positions.reduce((s, p) => {
    const ltp = quotes[p.symbol]?.ltp ?? p.ltp;
    return s + (ltp != null ? ltp * p.quantity - p.cost : 0);
  }, 0) : null;
  const unreal = o && o.positions.length ? live : o?.unrealized_pnl ?? 0;
  const total = o ? o.realized_pnl + (unreal || 0) : null;
  const capital = o?.mode === "paper" ? o.initial_paper_capital : 0;
  const value = capital ? capital + (total || 0) : null;
  const pts = pnl?.points || [];
  const short = (d: string) => new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  return (
    <section className="surface overflow-hidden">
      <div className="bg-gradient-to-br from-brand-500/[0.12] via-transparent to-emerald-500/[0.06] p-4 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-gray-500">
              {value != null ? "Paper account value" : "Total P&L"} {o && (o.mode === "live" ? <Badge tone="error" dot>Live</Badge> : <Badge tone="warning" dot>Paper</Badge>)}
            </div>
            {o ? (
              <div className="num mt-1 text-3xl font-bold tracking-tight text-white sm:text-4xl">{value != null ? inr(value, 0) : <span className={pnlClass(total)}>{inr(total)}</span>}</div>
            ) : <Skeleton className="mt-2 h-10 w-56" />}
            {o && (
              <div className="num mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="text-gray-500">Total P&amp;L <b className={pnlClass(total)}>{inr(total)}</b></span>
                {capital > 0 && total != null && <Change value={total / capital} size="xs" />}
                <span className="text-gray-500">Unrealised <b className={pnlClass(unreal)}>{inr(unreal)}</b></span>
                <span className="text-gray-500">Realised <b className={pnlClass(o.realized_pnl)}>{inr(o.realized_pnl)}</b></span>
              </div>
            )}
          </div>
          <Segmented size="sm" value={days} onChange={setDays} options={PERIODS} />
        </div>
        <div className="mt-4">
          {pts.length >= 2 ? (
            <EquityChart points={pts.map((p) => ({ label: short(p.date), v: p.cumulative }))} baseline={0} base={capital} height={200} format={(v) => inr(v, 0)} drawdown={false} />
          ) : (
            <div className="rounded-2xl border border-dashed border-white/[0.1] py-8 text-center text-sm text-gray-500">
              {pnl ? (pts.length === 1 ? `One trading day so far: ${inr(pnl.total)}.` : "Your P&L curve appears after two days with closed trades.") : "The P&L curve needs the database."}
            </div>
          )}
        </div>
      </div>
      {o && (
        <div className="grid grid-cols-3 divide-x divide-white/[0.06] border-t border-white/[0.06] text-center">
          {[["Positions", o.positions.length], ["Trades today", o.today_trades], ["Invested", inr(o.invested, 0)]].map(([k, v]) => (
            <div key={k as string} className="px-2 py-3"><div className="num text-sm font-bold text-white">{v}</div><div className="text-[11px] text-gray-500">{k}</div></div>
          ))}
        </div>
      )}
    </section>
  );
}

function QuickActions() {
  const { isAdmin } = useAuth();
  const ticket = useOrderTicket();
  const items = [
    ...(isAdmin ? [{ label: "New order", icon: ShoppingCart, onClick: () => ticket(), tone: "bg-brand-500/10 text-brand-400" }] : []),
    { label: "Swing scan", icon: Radar, href: "/scanner", tone: "bg-emerald-500/10 text-emerald-400" },
    { label: "Heatmap", icon: Grid3x3, href: "/market/heatmap", tone: "bg-amber-500/10 text-amber-400" },
    { label: "Price alert", icon: BellPlus, href: "/alerts", tone: "bg-rose-500/10 text-rose-400" },
    { label: "AI insights", icon: Brain, href: "/ai", tone: "bg-violet-500/10 text-violet-400" },
  ];
  return (
    <div className="no-scrollbar -mx-3 flex gap-3 overflow-x-auto px-3 sm:mx-0 sm:grid sm:grid-cols-5 sm:px-0">
      {items.map((a) => {
        const inner = (
          <>
            <span className={`grid h-11 w-11 place-items-center rounded-2xl ${a.tone}`}><a.icon className="h-5 w-5" /></span>
            <span className="text-xs font-semibold text-gray-300">{a.label}</span>
          </>
        );
        const cls = "surface flex min-w-[96px] shrink-0 flex-col items-center gap-2 p-3 transition hover:-translate-y-0.5 hover:shadow-pop";
        return "href" in a && a.href ? <Link key={a.label} href={a.href} className={cls}>{inner}</Link>
          : <button key={a.label} type="button" onClick={(a as any).onClick} className={cls}>{inner}</button>;
      })}
    </div>
  );
}

export default function Home() {
  const router = useRouter();
  const { user } = useAuth();
  const [prefs] = usePrefs();
  const show = (k: string) => prefs.homeCards.includes(k);
  const status = useApi<SystemStatus>("/api/status", 60000);
  const open = useMarketOpen();
  const [days, setDays] = useState<number>(90);
  const ov = useApi<Overview>("/api/dashboard/overview", open ? 30000 : 120000);
  const pnl = useApi<PnlHistory>(`/api/dashboard/pnl-history?days=${days}`, 300000);
  const sched = useApi<SchedulerStatus>(show("session") ? "/api/dashboard/scheduler" : null, 120000);
  const swing = useApi<SwingStatus>(show("system") ? "/api/dashboard/swing" : null, 300000);
  useModeChanged(ov.reload);
  const go = (s: string) => router.push(`/market?symbol=${encodeURIComponent(s)}`);

  // The landing page from Settings (once per visit)
  useEffect(() => {
    if (prefs.landing && prefs.landing !== "/" && !sessionStorage.getItem("aiind:landed")) {
      sessionStorage.setItem("aiind:landed", "1");
      router.replace(prefs.landing);
    } else sessionStorage.setItem("aiind:landed", "1");
  }, [prefs.landing, router]);

  const s = status.data;
  const o = ov.data;
  const sw = swing.data;
  const todayKey = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const todayDaemon = sched.data?.days.find((d) => d.date === todayKey)?.jobs.daemon;
  const { quotes } = useQuotes(o?.positions.map((p) => p.symbol) || []);
  const first = (user?.name || user?.email || "").split(/[@\s.]/)[0];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-gray-500">{greeting()}{first ? `, ${first}` : ""}</p>
          <h1 className="text-2xl font-bold tracking-tight text-white">Your dashboard</h1>
        </div>
        <Button variant="ghost" size="sm" icon={RefreshCw} onClick={() => { ov.reload(); pnl.reload(); status.reload(); }} loading={ov.loading && !!o}>Refresh</Button>
      </div>

      {o?.kill_switch.halted && (
        <Notice tone="error" icon={OctagonX} title="Trading halted" action={<Link href="/control" className="text-sm font-semibold underline">Manage</Link>}>{o.kill_switch.text}</Notice>
      )}
      {s && !s.broker.token_valid && (
        <Notice tone="warning" icon={PlugZap} title="No valid INDstocks token" action={<Link href="/broker" className="text-sm font-semibold underline">Set token</Link>}>
          Live prices fall back to delayed quotes, and today&apos;s auto-trading session cannot start.
        </Notice>
      )}
      <ErrorBox error={ov.error} />

      {show("portfolio") && <Hero o={o} pnl={pnl.data} days={days} setDays={setDays} />}
      <QuickActions />
      {show("indices") && <IndexCards />}
      {show("ideas") && <IdeasStrip />}

      <div className="grid gap-4 lg:grid-cols-2">
        {show("watchlist") && <Watchlist go={go} compact />}
        {show("movers") && <Movers go={go} />}
      </div>

      {show("positions") && (
        <Card title="Open positions" icon={Activity} padded={false}
          right={<Link href="/portfolio" className="link inline-flex items-center gap-1 text-sm font-semibold">Portfolio <ArrowRight className="h-4 w-4" /></Link>}>
          {o && o.positions.length === 0 ? <Empty icon={Activity} title="No open positions">The scheduler opens trades on NSE trading days from 09:15 IST.</Empty>
            : o ? (
              <ul className="divide-y divide-white/[0.05]">
                {o.positions.map((p) => {
                  const ltp = quotes[p.symbol]?.ltp ?? p.ltp ?? null;
                  const pl = ltp != null ? ltp * p.quantity - p.cost : null;
                  return (
                    <li key={p.symbol}>
                      <StockRow symbol={p.symbol} sub={`${p.quantity} × ${inr(p.entry_price)} · SL ${inr(p.stop_loss)}`} quote={quotes[p.symbol] || { ltp: p.ltp ?? null, change_pct: p.day_change_pct }}
                        onClick={() => go(p.symbol)} right={<span className={`num w-24 text-right text-sm font-bold ${pnlClass(pl)}`}>{inr(pl)}</span>} />
                    </li>
                  );
                })}
              </ul>
            ) : <div className="space-y-2 p-4"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
        </Card>
      )}

      <RiskCapacity portfolioValue={o?.mode === "paper" ? o.initial_paper_capital + (o.realized_pnl || 0) : null} />

      <div className="grid gap-4 lg:grid-cols-2">
        {show("session") && (
          <Link href="/scheduler" className="surface group block p-4 transition hover:shadow-pop sm:p-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5 text-[15px] font-semibold text-white">
                <span className="grid h-8 w-8 place-items-center rounded-xl bg-brand-500/10"><CalendarClock className="h-4 w-4 text-brand-400" /></span> Auto-trading
              </div>
              <ArrowRight className="h-4 w-4 text-gray-500 transition group-hover:translate-x-0.5 group-hover:text-brand-400" />
            </div>
            {sched.data?.ok ? (
              <div className="mt-4 grid grid-cols-3 gap-3 text-xs">
                <div><div className="text-gray-500">Today</div><div className="mt-1">{todayDaemon ? <Badge tone={rcLabel(todayDaemon).tone}>{rcLabel(todayDaemon).text}</Badge> : <span className="text-gray-200">{planLine(sched.data.lines, "Today") || "—"}</span>}</div></div>
                <div><div className="text-gray-500">Next</div><div className="mt-1 text-gray-200">{planLine(sched.data.lines, "Next session") || "—"}</div></div>
                <div><div className="text-gray-500">Mode</div><div className="mt-1"><Badge tone={!sched.data.enabled ? "neutral" : sched.data.mode === "live" ? "error" : "warning"} dot>{sched.data.enabled ? (sched.data.mode || "").toUpperCase() : "OFF"}</Badge></div></div>
              </div>
            ) : <p className="mt-3 text-sm text-gray-500">{sched.data?.error || sched.error || "…"}</p>}
          </Link>
        )}
        {show("system") && (
          <Card title="System" icon={Cpu} subtitle={s ? `Backend v${s.version}` : undefined}>
            <ErrorBox error={status.error} />
            {s ? (
              <div className="-my-2 divide-y divide-white/[0.05]">
                <StatusRow icon={PlugZap} label={`Broker · ${s.broker.name}`} ok={s.broker.token_valid} detail={s.broker.token_valid ? `Token expires in ${s.broker.token_remaining}` : "No valid token"} />
                <StatusRow icon={Database} label="Supabase" ok={o?.database === "ok" ? true : s.services.supabase ? null : false} detail={o?.database === "ok" ? "Connected" : s.services.supabase ? "Configured" : "Not configured"} />
                <StatusRow icon={Zap} label="Upstash Redis" ok={s.services.redis ? true : null} detail={s.services.redis ? "Connected" : "Not configured (optional)"} />
                <StatusRow icon={Brain} label="Adaptive AI · swing model" ok={sw ? (sw.active ? true : sw.rules_only ? null : false) : null}
                  detail={!sw ? (swing.error ? "Unavailable" : "…") : sw.active ? `${sw.active} (${sw.active === sw.champion ? "champion" : "shadow"})` : sw.rules_only ? "No model · rules only (paper)" : "No model trained yet"} />
              </div>
            ) : !status.error ? <Skeleton className="h-32" /> : null}
          </Card>
        )}
      </div>

      <ModeSwitch compact />
    </div>
  );
}
