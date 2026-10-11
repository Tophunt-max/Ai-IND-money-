"use client";

import { Banknote, CalendarDays, Landmark, Layers, LineChart, PieChart, PlugZap, RefreshCw, Target, TrendingDown, TrendingUp, Wallet } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Donut, drawdowns, EquityChart } from "@/components/charts";
import { sectorName, StockRow } from "@/components/MarketWidgets";
import PnlCalendar from "@/components/PnlCalendar";
import {
  Badge, Button, Card, Empty, ErrorBox, Notice, PageTitle, pnlClass, Segmented, Skeleton, StatCard, Tabs,
} from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useQuotes } from "@/lib/live";
import { useMarketOpen } from "@/lib/market";
import { PALETTE } from "@/lib/themeColors";
import type { Overview, PnlHistory } from "@/lib/types";

type Row = Record<string, any>;

interface BrokerBook {
  available: boolean;
  error?: string;
  errors?: Record<string, string>;
  positions?: Row[];
  fno_positions?: Row[];
  holdings?: Row[];
  funds?: Row | null;
  orders?: Row[];
}

interface Portfolio { mode: string; indstocks: BrokerBook; fetched_at: string }

const num = (v: any) => (v == null || v === "" ? null : Number(v));

function order(o: Row) {
  return {
    id: o.order_id ?? o.id ?? "",
    symbol: o.tradingsymbol ?? o.name ?? o.symbol ?? "—",
    side: String(o.transaction_type ?? o.txn_type ?? o.side ?? "").toUpperCase(),
    qty: num(o.quantity ?? o.requested_qty),
    filled: num(o.filled_quantity ?? o.traded_qty),
    price: num(o.average_price ?? o.traded_price ?? o.price),
    status: String(o.status ?? ""),
    time: o.order_timestamp ?? o.created_at ?? o.timestamp ?? null,
  };
}

function statusTone(s: string) {
  const u = s.toUpperCase();
  if (u.includes("COMPLETE") || u.includes("FILLED") || u.includes("SUCCESS")) return "ok" as const;
  if (u.includes("REJECT") || u.includes("CANCEL") || u.includes("FAIL")) return "error" as const;
  return "warning" as const;
}

/** Sector of a symbol from the heatmap's map (loaded once). */
function useSectors() {
  const h = useApi<{ stocks: { symbol: string; sector: string }[] }>("/api/dashboard/market/heatmap", 3600000);
  return useMemo(() => new Map((h.data?.stocks || []).map((s) => [s.symbol, s.sector])), [h.data]);
}

function slicesOf(items: { label: string; v: number }[]) {
  const sorted = items.filter((x) => x.v > 0).sort((a, b) => b.v - a.v);
  const top = sorted.slice(0, 7);
  const rest = sorted.slice(7).reduce((s, x) => s + x.v, 0);
  return [...top, ...(rest > 0 ? [{ label: "Others", v: rest }] : [])].map((x, i) => ({ ...x, v: Math.round(x.v), color: PALETTE[i % PALETTE.length] }));
}

function Holdings({ rows, empty, holdings = false }: { rows: Row[]; empty: string; holdings?: boolean }) {
  const router = useRouter();
  const { quotes } = useQuotes(rows.map((r) => String(r.symbol)));
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <ul className="-mx-4 divide-y divide-white/[0.05] sm:-mx-5">
      {rows.map((p, i) => {
        const avg = num(p.average_price);
        const qty = num(p.quantity) || 0;
        const ltp = quotes[p.symbol]?.ltp ?? num(p.last_price);
        const pnl = ltp != null && avg != null ? (ltp - avg) * qty : num(p.pnl);
        const ret = avg && ltp ? ltp / avg - 1 : null;
        return (
          <li key={i}>
            <StockRow symbol={p.symbol} sub={`${qty} × ${inr(avg)}${!holdings && p.product ? ` · ${p.product}` : ""}`}
              quote={{ ltp, change_pct: quotes[p.symbol]?.change_pct ?? (p.day_change_pct != null ? Number(p.day_change_pct) / 100 : null) }}
              onClick={() => router.push(`/market?symbol=${encodeURIComponent(p.symbol)}`)}
              right={<div className="num w-24 text-right"><div className={`text-sm font-bold ${pnlClass(pnl)}`}>{inr(pnl)}</div><div className={`text-[11px] ${pnlClass(ret)}`}>{ret == null ? "" : `${ret >= 0 ? "+" : ""}${(ret * 100).toFixed(2)}%`}</div></div>} />
          </li>
        );
      })}
    </ul>
  );
}

function Analytics({ book, ov }: { book: BrokerBook | null; ov: Overview | null }) {
  const [days, setDays] = useState(365);
  const pnl = useApi<PnlHistory>(`/api/dashboard/pnl-history?days=${days}`, 300000);
  const sectors = useSectors();
  const [by, setBy] = useState<"stock" | "sector">("stock");
  const pts = pnl.data?.points || [];
  const capital = ov?.mode === "paper" ? ov.initial_paper_capital : 0;
  const dd = drawdowns(pts.map((p) => p.cumulative), capital);
  const wins = pts.filter((p) => p.pnl > 0);
  const losses = pts.filter((p) => p.pnl < 0);
  const best = pts.reduce<{ date: string; pnl: number } | null>((b, p) => (!b || p.pnl > b.pnl ? p : b), null);
  const worst = pts.reduce<{ date: string; pnl: number } | null>((b, p) => (!b || p.pnl < b.pnl ? p : b), null);
  let streak = 0;
  for (let i = pts.length - 1; i >= 0 && pts[i].pnl > 0; i--) streak++;

  const items = [
    ...(book?.holdings || []).map((h) => ({ symbol: String(h.symbol), v: (num(h.last_price) || 0) * (num(h.quantity) || 0) })),
    ...(book?.positions || []).map((h) => ({ symbol: String(h.symbol), v: (num(h.last_price) || 0) * Math.abs(num(h.quantity) || 0) })),
    ...(ov?.positions || []).filter((p) => !book?.available).map((p) => ({ symbol: p.symbol, v: (p.ltp ?? p.entry_price) * p.quantity })),
  ];
  const stockSlices = slicesOf(Object.values(items.reduce<Record<string, { label: string; v: number }>>((m, x) => {
    m[x.symbol] = { label: x.symbol, v: (m[x.symbol]?.v || 0) + x.v };
    return m;
  }, {})));
  const sectorSlices = slicesOf(Object.values(items.reduce<Record<string, { label: string; v: number }>>((m, x) => {
    const s = sectorName(sectors.get(x.symbol) || "OTHER");
    m[s] = { label: s, v: (m[s]?.v || 0) + x.v };
    return m;
  }, {})));
  const slices = by === "stock" ? stockSlices : sectorSlices;
  const totalValue = slices.reduce((s, x) => s + x.v, 0);
  const short = (d: string) => new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Realised P&L" icon={LineChart} value={<span className={pnlClass(pnl.data?.total)}>{inr(pnl.data?.total ?? null, 0)}</span>} detail={`${pts.length} trading days`} tone={(pnl.data?.total ?? 0) >= 0 ? "ok" : "error"} />
        <StatCard title="Winning days" icon={TrendingUp} value={pts.length ? `${Math.round((wins.length / pts.length) * 100)}%` : "—"} detail={`${wins.length} up · ${losses.length} down · streak ${streak}`} tone="info" />
        <StatCard title="Best / worst day" icon={TrendingDown} value={<span className="text-emerald-400">{inr(best?.pnl ?? null, 0)}</span>} detail={<span className="text-rose-400">{inr(worst?.pnl ?? null, 0)}{worst ? ` on ${short(worst.date)}` : ""}</span>} />
        <StatCard title="Max drawdown" icon={Target} value={<span className="text-rose-400">{inr(dd.max, 0)}</span>} detail={capital ? `${(dd.maxPct * 100).toFixed(1)}% of capital` : "from the peak"} tone="error" />
      </div>
      <Card title="Equity curve & drawdown" icon={LineChart} right={<Segmented size="sm" value={days} onChange={setDays} options={[{ value: 90, label: "3M" }, { value: 365, label: "1Y" }, { value: 1825, label: "5Y" }]} />}>
        <ErrorBox error={pnl.error} />
        {pts.length >= 2 ? <EquityChart points={pts.map((p) => ({ label: short(p.date), v: p.cumulative }))} baseline={0} base={capital} height={260} format={(v) => inr(v, 0)} />
          : <Empty icon={LineChart}>The curve appears after two days with closed trades.</Empty>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Daily P&L calendar" icon={CalendarDays} subtitle="Realised P&L per trading day">
          {pnl.data ? <PnlCalendar points={pts} weeks={26} /> : <Skeleton className="h-32" />}
        </Card>
        <Card title="Allocation" icon={PieChart} right={<Segmented size="sm" value={by} onChange={setBy} options={[{ value: "stock", label: "Stocks" }, { value: "sector", label: "Sectors" }]} />}>
          {slices.length === 0 ? <Empty>Nothing held.</Empty> : <Donut slices={slices} size={150}
            center={<div><div className="num text-sm font-bold text-white">{inr(totalValue, 0)}</div><div className="text-[10px] text-gray-500">{slices.length} {by === "stock" ? "stocks" : "sectors"}</div></div>} />}
        </Card>
      </div>
    </div>
  );
}

function Book({ book }: { book: BrokerBook }) {
  const [tab, setTab] = useState<"hold" | "pos" | "fno" | "orders">("hold");
  const f = book.funds;
  const positions = book.positions || [];
  const fno = book.fno_positions || [];
  const holdings = book.holdings || [];
  const orders = (book.orders || []).map(order);
  const { quotes } = useQuotes(holdings.map((h) => String(h.symbol)));
  const holdingValue = holdings.reduce((s, h) => s + (quotes[h.symbol]?.ltp ?? num(h.last_price) ?? 0) * (num(h.quantity) || 0), 0);
  const holdingCost = holdings.reduce((s, h) => s + (num(h.average_price) || 0) * (num(h.quantity) || 0), 0);
  const holdingPnl = holdingValue - holdingCost;
  const dayPnl = holdings.reduce((s, h) => {
    const q = quotes[h.symbol];
    return s + (q?.change != null ? q.change * (num(h.quantity) || 0) : 0);
  }, 0);
  const posPnl = [...positions, ...fno].reduce((s, p) => s + (num(p.pnl) || 0), 0);
  const errors = Object.entries(book.errors || {});

  return (
    <div className="space-y-4">
      {errors.length > 0 && (
        <Notice tone="warning" title="Some data could not be read">{errors.map(([k, v]) => <div key={k}><b className="capitalize">{k}</b>: {v}</div>)}</Notice>
      )}
      <section className="surface overflow-hidden">
        <div className="bg-gradient-to-br from-brand-500/[0.12] via-transparent to-emerald-500/[0.06] p-4 sm:p-6">
          <div className="text-xs font-medium text-gray-500">Holdings value</div>
          <div className="num mt-1 text-3xl font-bold text-white">{inr(holdingValue, 0)}</div>
          <div className="num mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm">
            <span className="text-gray-500">Total returns <b className={pnlClass(holdingPnl)}>{inr(holdingPnl, 0)} {holdingCost ? `(${((holdingPnl / holdingCost) * 100).toFixed(2)}%)` : ""}</b></span>
            <span className="text-gray-500">Today <b className={pnlClass(dayPnl)}>{inr(dayPnl, 0)}</b></span>
            <span className="text-gray-500">Invested <b className="text-gray-200">{inr(holdingCost, 0)}</b></span>
          </div>
        </div>
        <div className="grid grid-cols-3 divide-x divide-white/[0.06] border-t border-white/[0.06] text-center">
          <div className="px-2 py-3"><div className="num text-sm font-bold text-white">{inr(num(f?.available_cash), 0)}</div><div className="text-[11px] text-gray-500"><Banknote className="mr-1 inline h-3 w-3" />Cash</div></div>
          <div className="px-2 py-3"><div className="num text-sm font-bold text-white">{inr(num(f?.option_buy_available), 0)}</div><div className="text-[11px] text-gray-500"><Layers className="mr-1 inline h-3 w-3" />F&amp;O</div></div>
          <div className="px-2 py-3"><div className={`num text-sm font-bold ${pnlClass(posPnl)}`}>{inr(posPnl, 0)}</div><div className="text-[11px] text-gray-500"><Target className="mr-1 inline h-3 w-3" />Positions P&amp;L</div></div>
        </div>
      </section>

      <section className="surface p-4 sm:p-5">
        <Tabs value={tab} onChange={setTab} className="mb-2" options={[
          { value: "hold", label: "Holdings", count: holdings.length }, { value: "pos", label: "Positions", count: positions.length },
          { value: "fno", label: "F&O", count: fno.length }, { value: "orders", label: "Orders", count: orders.length },
        ]} />
        {tab === "hold" && <Holdings rows={holdings} empty="No holdings." holdings />}
        {tab === "pos" && <Holdings rows={positions} empty="No equity positions today." />}
        {tab === "fno" && <Holdings rows={fno} empty="No F&O positions today." />}
        {tab === "orders" && (orders.length === 0 ? <Empty>No orders today.</Empty> : (
          <ul className="-mx-4 divide-y divide-white/[0.05] sm:-mx-5">
            {orders.map((o, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                <Badge tone={o.side === "BUY" ? "ok" : o.side === "SELL" ? "error" : "neutral"}>{o.side || "—"}</Badge>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-white">{o.symbol}</div>
                  <div className="num truncate text-[11px] text-gray-500">{o.id}{o.time && ` · ${when(o.time)}`}</div>
                </div>
                <div className="num text-right text-xs">
                  <div className="text-gray-200">{o.filled ?? 0}/{o.qty ?? "—"} @ {inr(o.price)}</div>
                  <Badge tone={statusTone(o.status)}>{o.status || "—"}</Badge>
                </div>
              </li>
            ))}
          </ul>
        ))}
      </section>
    </div>
  );
}

export default function PortfolioPage() {
  const open = useMarketOpen();
  const { data, error, loading, reload } = useApi<Portfolio>("/api/dashboard/portfolio", open ? 30000 : 120000);
  const ov = useApi<Overview>("/api/dashboard/overview", 120000);
  const [view, setView] = useState<"book" | "analytics">("book");

  return (
    <div className="space-y-4">
      <PageTitle title="Portfolio" icon={Wallet}
        subtitle={data ? `INDstocks · updated ${when(data.fetched_at)} · prices live` : "Holdings, positions and analytics"}
        right={<>
          <Segmented value={view} onChange={setView} options={[{ value: "book", label: "Holdings" }, { value: "analytics", label: "Analytics" }]} />
          <Button variant="ghost" size="sm" icon={RefreshCw} onClick={reload} loading={loading && !!data} />
        </>} />
      <ErrorBox error={error} />
      {view === "analytics" ? <Analytics book={data?.indstocks || null} ov={ov.data} /> : loading && !data ? (
        <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-64" /></div>
      ) : data && !data.indstocks.available ? (
        <>
          <Notice tone="warning" icon={PlugZap} title="INDstocks not connected" action={<Link href="/broker" className="text-sm font-semibold underline">Connect</Link>}>{data.indstocks.error}</Notice>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard title="Engine positions" icon={Landmark} value={ov.data?.positions.length ?? "—"} detail="from the trade history" />
            <StatCard title="Realised P&L" icon={LineChart} value={<span className={pnlClass(ov.data?.realized_pnl)}>{inr(ov.data?.realized_pnl ?? null)}</span>} />
          </div>
          <p className="text-xs text-gray-500">Analytics work from the trade history even without the broker: switch to Analytics.</p>
        </>
      ) : data ? <Book book={data.indstocks} /> : null}
      <p className="text-center text-[11px] text-gray-500">Read only here. Orders go through the order ticket and the safety checker.</p>
    </div>
  );
}
