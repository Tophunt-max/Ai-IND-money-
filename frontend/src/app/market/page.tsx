"use client";

import { ArrowLeft, Bell, BrainCircuit, Grid3x3, Radar, Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import AlertSheet, { type PriceAlert } from "@/components/AlertSheet";
import AnalysisPanel from "@/components/AnalysisPanel";
import { useAuth } from "@/components/AuthGate";
import type { Level } from "@/components/CandleChart";
import { usePalette } from "@/components/CommandPalette";
import { heatColor, IndexCards, Movers, sectorName, SectorBars, StarButton, useHeatmap, Watchlist } from "@/components/MarketWidgets";
import { useOrderTicket } from "@/components/OrderTicket";
import PriceChart from "@/components/PriceChart";
import { Badge, Button, Card, Change, cx, Empty, ErrorBox, Flash, Gauge, IconButton, KV, Skeleton, SymbolAvatar, Tabs } from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useQuote } from "@/lib/live";
import { SETUP_LABEL, type Overview, type SwingIdeaAnswer } from "@/lib/types";

const fmt = (v: number | null | undefined, idx: boolean) => (v == null ? "—" : idx ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : inr(v));

function AIView({ symbol }: { symbol: string }) {
  const r = useApi<SwingIdeaAnswer>(`/api/dashboard/swing/idea?symbol=${encodeURIComponent(symbol)}`, 600000);
  const ticket = useOrderTicket();
  const { isAdmin } = useAuth();
  const d = r.data;
  const idea = d?.idea;
  return (
    <Card title="Adaptive AI view" icon={BrainCircuit} subtitle="The swing setup on the last closed daily bar, scored by the swing model. Evidence, not advice."
      right={d && (d.model ? <Badge tone={d.model_state === "champion" ? "ok" : "info"}>{d.model_state}</Badge> : <Badge tone="warning">no model</Badge>)}>
      <ErrorBox error={r.error} />
      {r.loading && !d ? <Skeleton className="h-28" /> : d && !idea ? (
        <Empty icon={BrainCircuit} title="No setup today">None of the three setups fired on {symbol}&apos;s last daily bar.</Empty>
      ) : d && idea ? (
        <div className="flex flex-col items-center gap-5 sm:flex-row sm:items-start">
          <div className="text-center">
            <Gauge value={idea.prob} threshold={d.threshold} label={d.threshold != null ? `P(win) · needs ${Math.round(d.threshold * 100)}%` : "P(win)"} />
            <div className="mt-1">{d.qualifies ? <Badge tone="ok" dot>Model takes it</Badge> : d.model ? <Badge tone="warning">Below threshold</Badge> : <Badge>Rules only</Badge>}</div>
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex flex-wrap items-center gap-2"><Badge tone="info">{SETUP_LABEL[idea.setup] || idea.setup}</Badge><span className="text-xs text-gray-500">{idea.reason}</span></div>
            <div className="grid grid-cols-3 gap-2 rounded-2xl bg-ink-850 p-3 text-center">
              <div><div className="text-[11px] text-gray-500">Entry</div><div className="num text-sm font-bold text-white">{inr(idea.entry)}</div></div>
              <div><div className="text-[11px] text-gray-500">Stop</div><div className="num text-sm font-bold text-rose-400">{inr(idea.stop)}</div></div>
              <div><div className="text-[11px] text-gray-500">Target</div><div className="num text-sm font-bold text-emerald-400">{inr(idea.target)}</div></div>
            </div>
            {isAdmin && (
              <Button variant="soft" size="sm" onClick={() => ticket({ symbol, side: "BUY", stop: idea.stop, target: idea.target })}>
                Order with these levels
              </Button>
            )}
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function Detail({ symbol, back }: { symbol: string; back: () => void }) {
  const { isAdmin } = useAuth();
  const ticket = useOrderTicket();
  const q = useQuote(symbol);
  const isIndex = symbol.startsWith("^");
  const [tab, setTab] = useState<"overview" | "technicals" | "ai">("overview");
  const [alertOpen, setAlertOpen] = useState(false);
  const ov = useApi<Overview>(!isIndex ? "/api/dashboard/overview" : null, 60000);
  const alerts = useApi<{ alerts: PriceAlert[] }>("/api/dashboard/alerts", 60000);
  const pos = ov.data?.positions.find((p) => p.symbol === symbol);
  const myAlerts = (alerts.data?.alerts || []).filter((a) => a.symbol === symbol && a.active);
  const levels: Level[] = [
    ...(pos ? [{ label: "Avg", price: pos.entry_price, tone: "avg" as const },
      ...(pos.stop_loss ? [{ label: "Your stop", price: pos.stop_loss, tone: "stop" as const }] : []),
      ...(pos.target ? [{ label: "Your target", price: pos.target, tone: "target" as const }] : [])] : []),
    ...myAlerts.filter((a) => a.op === "above" || a.op === "below").map((a) => ({ label: "Alert", price: a.value, tone: "alert" as const })),
  ];
  const range = q && q.high != null && q.low != null && q.high > q.low && q.ltp != null ? ((q.ltp - q.low) / (q.high - q.low)) * 100 : null;
  const up = (q?.change_pct ?? 0) >= 0;

  return (
    <div className="space-y-4 pb-20 lg:pb-0">
      <section className="surface p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <button type="button" onClick={back} aria-label="Back" className="-ml-1 mt-1 rounded-lg p-1 text-gray-400 hover:bg-white/[0.06] hover:text-white lg:hidden">
            <ArrowLeft className="h-5 w-5" />
          </button>
          <SymbolAvatar symbol={symbol} size={48} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-bold text-white">{symbol.replace("^", "")}</h1>
              <Badge>{isIndex ? "Index" : "NSE"}</Badge>
              {q?.source === "indstocks" ? <Badge tone="ok" dot>Live</Badge> : q ? <Badge tone="warning" dot>Delayed</Badge> : null}
            </div>
            {q ? (
              <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className={cx("num text-3xl font-bold", up ? "text-emerald-400" : "text-rose-400")}><Flash value={q.ltp}>{fmt(q.ltp, isIndex)}</Flash></span>
                <Change value={q.change_pct} abs={q.change} size="md" />
              </div>
            ) : <Skeleton className="mt-2 h-9 w-48" />}
            {q?.as_of && <div className="mt-1 text-[11px] text-gray-500">as of {when(q.as_of)}</div>}
          </div>
          <div className="flex shrink-0 items-center">
            {!isIndex && <StarButton symbol={symbol} />}
            <IconButton icon={Bell} label="Price alert" onClick={() => setAlertOpen(true)} badge={myAlerts.length} />
          </div>
        </div>
        {range != null && q && (
          <div className="mt-4">
            <div className="num flex justify-between text-[11px] text-gray-500"><span>Low {fmt(q.low, isIndex)}</span><span>Today&apos;s range</span><span>High {fmt(q.high, isIndex)}</span></div>
            <div className="relative mt-1.5 h-1.5 rounded-full bg-gradient-to-r from-rose-500/50 via-white/10 to-emerald-500/50">
              <span className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink-900 bg-white shadow" style={{ left: `${range}%` }} />
            </div>
          </div>
        )}
      </section>

      <section className="surface min-w-0 overflow-hidden p-3 sm:p-5"><PriceChart symbol={symbol} extraLevels={levels} /></section>

      {pos && (
        <Card title="Your position" subtitle={`Opened ${when(pos.opened_at)}`}>
          <KV cols={3} items={[
            ["Quantity", pos.quantity], ["Avg price", inr(pos.entry_price)], ["Invested", inr(pos.cost, 0)],
            ["P&L", <span key="p" className={(pos.unrealized_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}>{inr(pos.unrealized_pnl)}</span>],
            ["Stop", inr(pos.stop_loss)], ["Target", inr(pos.target)],
          ]} />
        </Card>
      )}

      {!isIndex && (
        <>
          <Tabs value={tab} onChange={setTab} options={[{ value: "overview", label: "Overview" }, { value: "technicals", label: "Technicals" }, { value: "ai", label: "AI view" }]} />
          {tab === "overview" && q && (
            <Card title="Today">
              <KV cols={3} items={[
                ["Open", fmt(q.open, false)], ["Prev. close", fmt(q.prev_close, false)], ["Volume", q.volume ? q.volume.toLocaleString("en-IN") : "—"],
                ["Day high", fmt(q.high, false)], ["Day low", fmt(q.low, false)], ["Value traded", q.volume && q.ltp ? inr(q.volume * q.ltp, 0) : "—"],
              ]} />
              {myAlerts.length > 0 && (
                <div className="mt-4 border-t border-white/[0.06] pt-3">
                  <div className="mb-2 text-xs font-medium text-gray-500">Your alerts</div>
                  <div className="flex flex-wrap gap-2">{myAlerts.map((a) => <span key={a.id} className="chip"><Bell className="h-3.5 w-3.5 text-amber-400" />{a.text.replace(`${symbol} `, "")}</span>)}</div>
                </div>
              )}
            </Card>
          )}
          {tab === "technicals" && <AnalysisPanel symbol={symbol} />}
          {tab === "ai" && <AIView symbol={symbol} />}
        </>
      )}

      {isAdmin && !isIndex && (
        <div className="fixed inset-x-0 bottom-[calc(4.4rem+env(safe-area-inset-bottom))] z-20 grid grid-cols-2 gap-3 border-t border-white/[0.06] bg-ink-900/95 p-3 backdrop-blur-xl lg:static lg:border-0 lg:bg-transparent lg:p-0">
          <Button variant="success" size="lg" onClick={() => ticket({ symbol, side: "BUY" })}>Buy</Button>
          <Button variant="danger" size="lg" onClick={() => ticket({ symbol, side: "SELL" })}>Sell</Button>
        </div>
      )}
      <AlertSheet open={alertOpen} onClose={() => setAlertOpen(false)} symbol={symbol} ltp={q?.ltp} onSaved={alerts.reload} />
    </div>
  );
}

function HeatPreview() {
  const heat = useHeatmap();
  const stocks = heat.data?.stocks || [];
  return (
    <Card title="Heatmap" icon={Grid3x3} subtitle="NIFTY 50 by day change" right={<Link href="/market/heatmap" className="link text-sm font-semibold">Open</Link>}>
      {!heat.data ? <Skeleton className="h-32" /> : (
        <>
          <div className="grid grid-cols-10 gap-1">
            {stocks.map((s) => (
              <Link key={s.symbol} href={`/market?symbol=${encodeURIComponent(s.symbol)}`} title={`${s.symbol} ${s.change_pct != null ? (s.change_pct * 100).toFixed(2) + "%" : ""}`}
                className="aspect-square rounded-md transition hover:scale-110" style={{ background: heatColor(s.change_pct) }} />
            ))}
          </div>
          {heat.data.sectors.length > 0 && <div className="mt-4"><SectorBars sectors={heat.data.sectors.slice(0, 6)} /></div>}
        </>
      )}
    </Card>
  );
}

function Market() {
  const params = useSearchParams();
  const router = useRouter();
  const palette = usePalette();
  const symbol = (params.get("symbol") || "").toUpperCase();
  const go = (s: string) => router.push(`/market?symbol=${encodeURIComponent(s)}`);

  return (
    <div className="space-y-4">
      {!symbol && (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">Market</h1>
            <p className="text-[13px] text-gray-500">Live NSE prices, your watchlists, movers and sectors</p>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" icon={Search} onClick={palette.open}>Search</Button>
            <Link href="/scanner"><Button variant="soft" icon={Radar}>Swing scan</Button></Link>
          </div>
        </div>
      )}
      <div className={cx(symbol && "hidden lg:block")}><IndexCards active={symbol} /></div>

      <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
        <div className={cx("min-w-0 space-y-4", symbol && "hidden lg:block")}>
          <Watchlist go={go} compact={!!symbol} />
        </div>
        {symbol ? <div className="min-w-0"><Detail symbol={symbol} back={() => router.push("/market")} /></div> : (
          <div className="min-w-0 space-y-4">
            <Movers go={go} />
            <HeatPreview />
          </div>
        )}
      </div>
      {!symbol && <ExploreSectorNames />}
    </div>
  );
}

function ExploreSectorNames() {
  const heat = useHeatmap();
  if (!heat.data?.sectors.length) return null;
  return (
    <div className="no-scrollbar flex gap-2 overflow-x-auto">
      {heat.data.sectors.map((s) => (
        <Link key={s.sector} href={`/market/heatmap#${s.sector}`} className="chip shrink-0">
          {sectorName(s.sector)} <span className={s.change_pct >= 0 ? "text-emerald-400" : "text-rose-400"}>{(s.change_pct * 100).toFixed(2)}%</span>
        </Link>
      ))}
    </div>
  );
}

export default function MarketPage() {
  return (
    <Suspense>
      <Market />
    </Suspense>
  );
}
