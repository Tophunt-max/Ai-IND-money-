"use client";

import { Banknote, Landmark, Layers, PlugZap, RefreshCw, Target, Wallet } from "lucide-react";
import Link from "next/link";

import { useState } from "react";

import { Donut } from "@/components/charts";
import {
  Badge, Button, Card, Change, Empty, ErrorBox, Notice, PageTitle, pnlClass, Segmented, Skeleton, StatCard,
} from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useMarketOpen } from "@/lib/market";

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

interface Portfolio {
  mode: string;
  indstocks: BrokerBook;
  fetched_at: string;
}

const num = (v: any) => (v == null || v === "" ? null : Number(v));

/** INDstocks order rows (a few alternate key names accepted). */
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

const PALETTE = ["#5b8cff", "#0ecb81", "#fbbf24", "#a78bfa", "#f472b6", "#22d3ee", "#fb923c", "#94a3b8"];

/** Positions / holdings as dense rows: symbol and quantity left, P&L and LTP right. */
function Rows({ rows, empty, holdings = false }: { rows: Row[]; empty: string; holdings?: boolean }) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <ul className="-mx-3 divide-y divide-white/4 sm:-mx-4">
      {rows.map((p, i) => {
        const pnl = num(p.pnl);
        const avg = num(p.average_price);
        const ltp = num(p.last_price);
        const qty = num(p.quantity);
        const ret = avg && ltp ? ltp / avg - 1 : null;
        return (
          <li key={i}>
            <Link href={`/market?symbol=${encodeURIComponent(p.symbol)}`} className="flex items-center gap-3 px-3 py-2.5 hover:bg-white/3 sm:px-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[13px] font-semibold text-white">{p.symbol}</span>
                  {!holdings && p.product && <span className="rounded-sm bg-white/6 px-1 text-[9px] font-semibold uppercase text-gray-400">{p.product}</span>}
                </div>
                <div className="num text-[11px] text-gray-500">{qty} × {inr(avg)}{holdings ? "" : ` · ${p.exchange || ""}`}</div>
              </div>
              <div className="num text-right">
                <div className={`text-[13px] font-semibold ${pnlClass(pnl)}`}>{inr(pnl)}</div>
                <div className="flex items-center justify-end gap-1.5 text-[11px] text-gray-400">
                  LTP {inr(ltp)} <Change value={ret} size="xs" pill={false} />
                </div>
                {holdings && p.day_change_pct != null && <div className="text-[10px] text-gray-500">today {Number(p.day_change_pct).toFixed(2)}%</div>}
                {!holdings && num(p.day_pnl) != null && <div className={`text-[10px] ${pnlClass(num(p.day_pnl))}`}>day {inr(num(p.day_pnl))}</div>}
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Book({ book }: { book: BrokerBook }) {
  const [tab, setTab] = useState<"pos" | "fno" | "hold" | "orders">("pos");
  if (!book.available) {
    return (
      <Notice tone="warning" icon={PlugZap} title="INDstocks not available"
        action={<Link href="/broker" className="text-sm font-medium underline">Connect</Link>}>
        {book.error}
      </Notice>
    );
  }
  const f = book.funds;
  const positions = book.positions || [];
  const fno = book.fno_positions || [];
  const holdings = book.holdings || [];
  const orders = (book.orders || []).map(order);
  const holdingValue = holdings.reduce((s, h) => s + (num(h.last_price) || 0) * (num(h.quantity) || 0), 0);
  const holdingCost = holdings.reduce((s, h) => s + (num(h.average_price) || 0) * (num(h.quantity) || 0), 0);
  const holdingPnl = holdings.reduce((s, h) => s + (num(h.pnl) || 0), 0);
  const posPnl = [...positions, ...fno].reduce((s, p) => s + (num(p.pnl) || 0), 0);
  const errors = Object.entries(book.errors || {});
  const alloc = holdings.map((h) => ({ label: h.symbol, v: Math.round((num(h.last_price) || 0) * (num(h.quantity) || 0)) }))
    .sort((a, b) => b.v - a.v);
  const slices = [...alloc.slice(0, 7), ...(alloc.length > 7 ? [{ label: "Others", v: alloc.slice(7).reduce((s, x) => s + x.v, 0) }] : [])]
    .map((x, i) => ({ ...x, color: PALETTE[i % PALETTE.length] }));

  return (
    <div className="space-y-3 sm:space-y-4">
      {errors.length > 0 && (
        <Notice tone="warning" title="Some data could not be read">
          {errors.map(([k, v]) => <div key={k}><b className="capitalize">{k}</b>: {v}</div>)}
        </Notice>
      )}
      <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
        <StatCard title="Available cash" icon={Banknote} tone="info" value={inr(num(f?.available_cash))}
          detail={f?.available_margin != null ? `Margin ${inr(num(f.available_margin))}` : undefined} />
        <StatCard title="F&O balance" icon={Layers} value={inr(num(f?.option_buy_available))}
          detail={`Futures ${inr(num(f?.futures_available))} · intraday ${inr(num(f?.intraday_available))}`} />
        <StatCard title="Holdings" icon={Landmark} value={inr(holdingValue, 0)}
          detail={<span className={pnlClass(holdingPnl)}>P&L {inr(holdingPnl)} {holdingCost ? `(${((holdingPnl / holdingCost) * 100).toFixed(2)}%)` : ""}</span>}
          tone={holdingPnl >= 0 ? "ok" : "error"} />
        <StatCard title="Positions P&L" icon={Target} value={<span className={pnlClass(posPnl)}>{inr(posPnl)}</span>}
          detail={`${positions.length} equity · ${fno.length} F&O`} tone={posPnl === 0 ? "neutral" : posPnl > 0 ? "ok" : "error"} />
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <section className="surface lg:col-span-2">
          <div className="border-b border-white/6 p-2">
            <Segmented size="sm" value={tab} onChange={setTab} options={[
              { value: "pos", label: `Positions ${positions.length}` }, { value: "fno", label: `F&O ${fno.length}` },
              { value: "hold", label: `Holdings ${holdings.length}` }, { value: "orders", label: `Orders ${orders.length}` },
            ]} />
          </div>
          <div className="p-3 sm:p-4">
            {tab === "pos" && <Rows rows={positions} empty="No equity positions today." />}
            {tab === "fno" && <Rows rows={fno} empty="No F&O positions today." />}
            {tab === "hold" && <Rows rows={holdings} empty="No holdings." holdings />}
            {tab === "orders" && (orders.length === 0 ? <Empty>No orders today.</Empty> : (
              <ul className="-mx-3 divide-y divide-white/4 sm:-mx-4">
                {orders.map((o, i) => (
                  <li key={i} className="flex items-center gap-3 px-3 py-2.5 sm:px-4">
                    <Badge tone={o.side === "BUY" ? "ok" : o.side === "SELL" ? "error" : "neutral"}>{o.side || "—"}</Badge>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-semibold text-white">{o.symbol}</div>
                      <div className="num truncate text-[10px] text-gray-500">{o.id}{o.time && ` · ${when(o.time)}`}</div>
                    </div>
                    <div className="num text-right text-[11px]">
                      <div className="text-gray-200">{o.filled ?? 0}/{o.qty ?? "—"} @ {inr(o.price)}</div>
                      <Badge tone={statusTone(o.status)}>{o.status || "—"}</Badge>
                    </div>
                  </li>
                ))}
              </ul>
            ))}
          </div>
        </section>
        <Card title="Allocation" subtitle="Holdings by market value">
          {slices.length === 0 ? <Empty>No holdings.</Empty> : <Donut slices={slices} size={130}
            center={<div><div className="num text-sm font-bold text-white">{inr(holdingValue, 0)}</div><div className="text-[10px] text-gray-500">{holdings.length} stocks</div></div>} />}
        </Card>
      </div>
    </div>
  );
}

export default function PortfolioPage() {
  const open = useMarketOpen();
  const { data, error, loading, reload } = useApi<Portfolio>("/api/dashboard/portfolio", open ? 15000 : 60000);

  return (
    <div className="space-y-3 sm:space-y-4">
      <PageTitle
        title="Portfolio"
        icon={Wallet}
        subtitle={data ? `Live from INDstocks · updated ${when(data.fetched_at)}` : "Live from INDstocks"}
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload} loading={loading && !!data}>Refresh</Button>}
      />
      <p className="text-xs text-gray-500">Read only: orders are placed by the safety-checked pipeline only.</p>
      <ErrorBox error={error} />
      {loading && !data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : data ? (
        <Book book={data.indstocks} />
      ) : null}
    </div>
  );
}
