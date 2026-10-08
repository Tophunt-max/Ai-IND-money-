"use client";

import { Banknote, Landmark, Layers, PlugZap, RefreshCw, Target, Wallet } from "lucide-react";
import Link from "next/link";

import {
  Badge, Button, Card, Empty, ErrorBox, Notice, PageTitle, pnlClass, Skeleton, StatCard, Table,
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

function PositionsTable({ rows, empty }: { rows: Row[]; empty: string }) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <Table head={["Symbol", "Qty", "Avg", "LTP", "P&L", "Day P&L"]}>
      {rows.map((p, i) => (
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
  );
}

function Book({ book }: { book: BrokerBook }) {
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
  const holdingPnl = holdings.reduce((s, h) => s + (num(h.pnl) || 0), 0);
  const posPnl = [...positions, ...fno].reduce((s, p) => s + (num(p.pnl) || 0), 0);
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
        <StatCard title="F&O balance" icon={Layers} value={inr(num(f?.option_buy_available))}
          detail={`Option buy · futures ${inr(num(f?.futures_available))} · intraday ${inr(num(f?.intraday_available))}`} />
        <StatCard title="Holdings value" icon={Landmark} value={inr(holdingValue)}
          detail={<span className={pnlClass(holdingPnl)}>P&L {inr(holdingPnl)}</span>} tone={holdingPnl >= 0 ? "ok" : "error"} />
        <StatCard title="Positions P&L" icon={Target} value={<span className={pnlClass(posPnl)}>{inr(posPnl)}</span>}
          detail={`${positions.length} equity · ${fno.length} F&O`} tone={posPnl === 0 ? "neutral" : posPnl > 0 ? "ok" : "error"} />
      </div>

      <Card title="Equity positions" subtitle="Today's delivery (CNC) and intraday positions">
        <PositionsTable rows={positions} empty="No equity positions today." />
      </Card>

      <Card title="F&O positions" subtitle="Futures and options: carry forward (MARGIN) and intraday">
        <PositionsTable rows={fno} empty="No F&O positions today." />
      </Card>

      <Card title="Holdings" subtitle="Delivery (CNC) shares in your demat">
        {holdings.length === 0 ? <Empty>No holdings.</Empty> : (
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

    </div>
  );
}

export default function PortfolioPage() {
  const open = useMarketOpen();
  const { data, error, loading, reload } = useApi<Portfolio>("/api/dashboard/portfolio", open ? 15000 : 60000);

  return (
    <div className="space-y-6">
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
