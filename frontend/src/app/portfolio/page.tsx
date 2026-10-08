"use client";

import { Banknote, Landmark, PiggyBank, PlugZap, RefreshCw, Target, Wallet } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import {
  Badge, Button, Card, Empty, ErrorBox, Notice, PageTitle, pnlClass, Segmented, Skeleton, StatCard, Table,
} from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Row = Record<string, any>;

interface BrokerBook {
  available: boolean;
  error?: string;
  errors?: Record<string, string>;
  positions?: Row[];
  holdings?: Row[];
  funds?: Row | null;
  orders?: Row[];
}

interface Portfolio {
  mode: string;
  indstocks: BrokerBook;
  kite: BrokerBook;
  fetched_at: string;
}

const num = (v: any) => (v == null || v === "" ? null : Number(v));

/** INDstocks and Kite order rows have different keys. */
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

function Book({ book, broker }: { book: BrokerBook; broker: "INDstocks" | "Kite" }) {
  if (!book.available) {
    return (
      <Notice tone="warning" icon={PlugZap} title={`${broker} not available`}
        action={<Link href="/broker" className="text-sm font-medium underline">Connect</Link>}>
        {book.error}
      </Notice>
    );
  }
  const f = book.funds;
  const positions = book.positions || [];
  const holdings = book.holdings || [];
  const orders = (book.orders || []).map(order);
  const holdingValue = holdings.reduce((s, h) => s + (num(h.last_price) || 0) * (num(h.quantity) || 0), 0);
  const holdingPnl = holdings.reduce((s, h) => s + (num(h.pnl) || 0), 0);
  const posPnl = positions.reduce((s, p) => s + (num(p.pnl) || 0), 0);
  const errors = Object.entries(book.errors || {});

  return (
    <div className="space-y-5">
      {errors.length > 0 && (
        <Notice tone="warning" title="Some data could not be read">
          {errors.map(([k, v]) => <div key={k}><b className="capitalize">{k}</b>: {v}</div>)}
        </Notice>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Available cash" icon={Banknote} tone="info" value={inr(num(f?.available_cash))}
          detail={f?.available_margin != null ? `Margin ${inr(num(f.available_margin))}` : undefined} />
        <StatCard title="Used margin" icon={PiggyBank} value={inr(num(f?.used_margin))}
          detail={f?.total_collateral ? `Collateral ${inr(num(f.total_collateral))}` : undefined} />
        <StatCard title="Holdings value" icon={Landmark} value={inr(holdingValue)}
          detail={<span className={pnlClass(holdingPnl)}>P&L {inr(holdingPnl)}</span>} tone={holdingPnl >= 0 ? "ok" : "error"} />
        <StatCard title="Positions P&L" icon={Target} value={<span className={pnlClass(posPnl)}>{inr(posPnl)}</span>}
          detail={`${positions.length} open`} tone={posPnl === 0 ? "neutral" : posPnl > 0 ? "ok" : "error"} />
      </div>

      <Card title="Positions" subtitle="Today's intraday and carry-forward positions">
        {positions.length === 0 ? <Empty>No open positions at {broker}.</Empty> : (
          <Table head={["Symbol", "Qty", "Avg", "LTP", "P&L", "Day P&L"]}>
            {positions.map((p, i) => (
              <tr key={i} className="hover:bg-white/[0.02]">
                <td><div className="font-medium text-white">{p.symbol}</div><div className="text-[11px] text-gray-500">{p.exchange} · {p.product}</div></td>
                <td>{num(p.quantity)}</td>
                <td>{inr(num(p.average_price))}</td>
                <td>{inr(num(p.last_price))}</td>
                <td className={pnlClass(num(p.pnl))}>{inr(num(p.pnl))}</td>
                <td className={pnlClass(num(p.day_pnl))}>{inr(num(p.day_pnl))}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Holdings" subtitle="Delivery (CNC) shares in your demat">
        {holdings.length === 0 ? <Empty>No holdings at {broker}.</Empty> : (
          <Table head={["Symbol", "Qty", "Avg", "LTP", "P&L", "Today"]}>
            {holdings.map((h, i) => (
              <tr key={i} className="hover:bg-white/[0.02]">
                <td><div className="font-medium text-white">{h.symbol}</div><div className="text-[11px] text-gray-500">{h.exchange}</div></td>
                <td>{num(h.quantity)}</td>
                <td>{inr(num(h.average_price))}</td>
                <td>{inr(num(h.last_price))}</td>
                <td className={pnlClass(num(h.pnl))}>{inr(num(h.pnl))}</td>
                <td className={pnlClass(num(h.day_change_pct))}>{h.day_change_pct != null ? `${Number(h.day_change_pct).toFixed(2)}%` : "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Today's orders" subtitle="The broker's order book (read only)">
        {orders.length === 0 ? <Empty>No orders today.</Empty> : (
          <Table head={["Order", "Side", "Qty", "Filled", "Price", "Status"]}>
            {orders.map((o, i) => (
              <tr key={i} className="hover:bg-white/[0.02]">
                <td><div className="font-medium text-white">{o.symbol}</div><div className="text-[11px] text-gray-500">{o.id} {o.time && `· ${when(o.time)}`}</div></td>
                <td><Badge tone={o.side === "BUY" ? "ok" : o.side === "SELL" ? "error" : "neutral"}>{o.side || "—"}</Badge></td>
                <td>{o.qty ?? "—"}</td>
                <td>{o.filled ?? "—"}</td>
                <td>{inr(o.price)}</td>
                <td><Badge tone={statusTone(o.status)}>{o.status || "—"}</Badge></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {broker === "Kite" && <KiteExtras />}
    </div>
  );
}

function KiteExtras() {
  const mf = useApi<{ holdings: Row[]; sips: Row[] }>("/api/dashboard/kite/mutual-funds");
  const gtt = useApi<{ gtts: Row[] }>("/api/dashboard/kite/gtt");
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Mutual funds" subtitle="Holdings and SIPs at Zerodha Coin">
        <ErrorBox error={mf.error} />
        {mf.loading && !mf.data ? <Skeleton className="h-20" /> : mf.data && (
          mf.data.holdings.length + mf.data.sips.length === 0 ? <Empty>No mutual funds or SIPs.</Empty> : (
            <div className="space-y-3">
              {mf.data.holdings.map((h, i) => (
                <div key={i} className="flex items-center justify-between gap-3 text-sm">
                  <div className="min-w-0"><div className="truncate text-gray-200">{h.fund}</div><div className="num text-xs text-gray-500">{h.units} units @ {inr(num(h.avg_price))}</div></div>
                  <div className={`num shrink-0 font-medium ${pnlClass(num(h.pnl))}`}>{inr(num(h.pnl))}</div>
                </div>
              ))}
              {mf.data.sips.map((s, i) => (
                <div key={`s${i}`} className="flex items-center justify-between gap-3 text-sm">
                  <div className="min-w-0"><div className="truncate text-gray-200">{s.fund}</div><div className="text-xs text-gray-500">SIP {inr(num(s.amount), 0)} · {s.frequency} · next {s.next_date || "—"}</div></div>
                  <Badge tone={s.status === "ACTIVE" ? "ok" : "neutral"}>{s.status}</Badge>
                </div>
              ))}
            </div>
          )
        )}
      </Card>
      <Card title="GTT orders" subtitle="Good-till-triggered orders at Kite">
        <ErrorBox error={gtt.error} />
        {gtt.loading && !gtt.data ? <Skeleton className="h-20" /> : gtt.data && (
          gtt.data.gtts.length === 0 ? <Empty>No GTT orders.</Empty> : (
            <div className="divide-y divide-white/[0.04]">
              {gtt.data.gtts.map((g, i) => (
                <div key={i} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <div>
                    <div className="font-medium text-gray-200">{g.condition?.tradingsymbol || "—"}</div>
                    <div className="num text-xs text-gray-500">{g.type} · trigger {(g.condition?.trigger_values || []).map((v: number) => inr(v)).join(" / ")}</div>
                  </div>
                  <Badge tone={g.status === "active" ? "ok" : "neutral"}>{g.status}</Badge>
                </div>
              ))}
            </div>
          )
        )}
      </Card>
    </div>
  );
}

export default function PortfolioPage() {
  const { data, error, loading, reload } = useApi<Portfolio>("/api/dashboard/portfolio", 60000);
  const [broker, setBroker] = useState<"indstocks" | "kite">("indstocks");

  return (
    <div className="space-y-6">
      <PageTitle
        title="Portfolio"
        icon={Wallet}
        subtitle={data ? `Live from your brokers · updated ${when(data.fetched_at)}` : "Live from your brokers"}
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload} loading={loading && !!data}>Refresh</Button>}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented value={broker} onChange={setBroker} options={[
          { value: "indstocks", label: <span className="flex items-center gap-2">INDstocks {data && <span className={`h-1.5 w-1.5 rounded-full ${data.indstocks.available ? "bg-emerald-400" : "bg-gray-600"}`} />}</span> },
          { value: "kite", label: <span className="flex items-center gap-2">Zerodha Kite {data && <span className={`h-1.5 w-1.5 rounded-full ${data.kite.available ? "bg-emerald-400" : "bg-gray-600"}`} />}</span> },
        ]} />
        <span className="text-xs text-gray-500">Read only: orders are placed by the safety-checked pipeline only.</span>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : data ? (
        <Book key={broker} book={data[broker]} broker={broker === "kite" ? "Kite" : "INDstocks"} />
      ) : null}
    </div>
  );
}
