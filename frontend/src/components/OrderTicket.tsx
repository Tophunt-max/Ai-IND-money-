"use client";

import { Minus, Plus, Search, ShieldCheck } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, inr } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useQuote } from "@/lib/live";
import { newIdempotencyKey, useSymbolSearch } from "@/lib/search";

import { useAuth } from "./AuthGate";
import { useToast } from "./Toast";
import { Badge, Button, Change, cx, Field, Flash, Notice, Segmented, Sheet, SymbolAvatar } from "./ui";

export interface TicketOptions {
  symbol?: string;
  side?: "BUY" | "SELL";
  quantity?: number;
  price?: number;
  stop?: number;
  target?: number;
  note?: string;
}

const TicketCtx = createContext<(o?: TicketOptions) => void>(() => {});
/** Open the order ticket from anywhere (chart, watchlist, AI idea, the Trade button). */
export const useOrderTicket = () => useContext(TicketCtx);

/** Estimated order figures (exported for tests). */
export function ticketMath(o: { side: "BUY" | "SELL"; qty: number; price: number | null; stop?: number | null; target?: number | null }) {
  const value = o.price ? o.qty * o.price : null;
  const risk = o.price && o.stop && o.side === "BUY" && o.stop < o.price ? (o.price - o.stop) * o.qty : null;
  const reward = o.price && o.target && o.side === "BUY" && o.target > o.price ? (o.target - o.price) * o.qty : null;
  const rr = risk && reward ? reward / risk : null;
  const problems: string[] = [];
  if (!Number.isFinite(o.qty) || o.qty < 1) problems.push("Quantity must be at least 1");
  if (o.side === "BUY" && o.stop && o.price && o.stop >= o.price) problems.push("Stop-loss must be below the price");
  if (o.side === "BUY" && o.target && o.price && o.target <= o.price) problems.push("Target must be above the price");
  return { value, risk, reward, rr, problems };
}

export function OrderTicketProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<TicketOptions | null>(null);
  const open = useCallback((o: TicketOptions = {}) => setOpts(o), []);
  return (
    <TicketCtx.Provider value={open}>
      {children}
      {opts && <Ticket opts={opts} onClose={() => setOpts(null)} />}
    </TicketCtx.Provider>
  );
}

function SymbolPicker({ onPick }: { onPick: (s: string) => void }) {
  const [q, setQ] = useState("");
  const { results } = useSymbolSearch(q, true, 8);
  return (
    <div>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} placeholder="Search a stock, e.g. RELIANCE"
          className="field h-11 pl-9" onKeyDown={(e) => e.key === "Enter" && q.trim() && onPick(results[0]?.symbol || q.trim())} />
      </div>
      <ul className="mt-2 divide-y divide-white/[0.05]">
        {results.filter((r) => r.kind === "stock").map((r) => (
          <li key={r.symbol}>
            <button type="button" onClick={() => onPick(r.symbol)} className="flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-white/[0.04]">
              <SymbolAvatar symbol={r.symbol} size={32} />
              <div className="min-w-0"><div className="text-sm font-semibold text-white">{r.symbol}</div><div className="text-xs text-gray-500">{r.name.replace(/_/g, " ").toLowerCase()}</div></div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Ticket({ opts, onClose }: { opts: TicketOptions; onClose: () => void }) {
  const { isAdmin } = useAuth();
  const toast = useToast();
  const me = useApi<{ mode: string }>("/api/dashboard/me");
  const live = me.data?.mode === "live";
  const [symbol, setSymbol] = useState((opts.symbol || "").toUpperCase());
  const [side, setSide] = useState<"BUY" | "SELL">(opts.side || "BUY");
  const [qty, setQty] = useState(opts.quantity || 1);
  const [type, setType] = useState<"MARKET" | "LIMIT">(opts.price ? "LIMIT" : "MARKET");
  const [price, setPrice] = useState(opts.price ? String(opts.price) : "");
  const [stop, setStop] = useState(opts.stop ? String(opts.stop) : "");
  const [target, setTarget] = useState(opts.target ? String(opts.target) : "");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = useQuote(symbol || null);
  const ltp = q?.ltp ?? null;
  useEffect(() => {
    if (type === "LIMIT" && !price && ltp) setPrice(ltp.toFixed(2));
  }, [type, price, ltp]);

  const px = type === "LIMIT" && price ? Number(price) : ltp;
  const m = useMemo(() => ticketMath({ side, qty, price: px, stop: stop ? Number(stop) : null, target: target ? Number(target) : null }),
    [side, qty, px, stop, target]);
  const buy = side === "BUY";

  const submit = async () => {
    setError(null);
    if (m.problems.length) return setError(m.problems[0]);
    if (type === "LIMIT" && !(Number(price) > 0)) return setError("A LIMIT order needs a price");
    if (live && typed.trim() !== "LIVE") return setError("Type LIVE to place a real-money order");
    setBusy(true);
    try {
      const res: any = await api("/api/dashboard/control/orders", {
        method: "POST", headers: { "Idempotency-Key": newIdempotencyKey() },
        json: {
          symbol, side, quantity: qty, order_type: type,
          price: type === "LIMIT" ? Number(price) : undefined,
          stop_loss: buy && stop ? Number(stop) : undefined, confirm_live: live,
        },
      });
      const ok = res?.ok !== false;
      let note = res?.message || (ok ? "Order sent" : "Order refused");
      if (ok && buy && target) {
        // The target goes on the position's exit plan (the monitor books it)
        try {
          const plan: any = await api("/api/dashboard/control/plan", { method: "POST", json: { symbol, target: Number(target) } });
          note += ` · target ${inr(Number(target))}: ${plan?.message || "saved"}`;
        } catch (e: any) {
          note += ` · target not saved: ${e instanceof ApiError ? e.message : String(e)}`;
        }
      }
      toast({ tone: ok ? "ok" : "error", title: `${side} ${qty} ${symbol}${live ? " (LIVE)" : " (paper)"}`, body: note });
      if (ok) onClose();
      else setError(note);
    } catch (e: any) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open onClose={onClose} title={symbol ? (
      <div className="flex items-center gap-3">
        <SymbolAvatar symbol={symbol} size={36} />
        <div>
          <div className="flex items-center gap-2">{symbol} {live ? <Badge tone="error" dot>LIVE</Badge> : <Badge tone="warning" dot>PAPER</Badge>}</div>
          <div className="num flex items-center gap-2 text-xs font-medium text-gray-400">
            NSE · <Flash value={ltp}>{inr(ltp)}</Flash> <Change value={q?.change_pct} size="xs" pill={false} />
          </div>
        </div>
      </div>
    ) : "New order"}
      footer={symbol && isAdmin ? (
        <div className="space-y-2">
          {live && (
            <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Real money: type LIVE to confirm" className="field h-10 text-center font-semibold tracking-widest" />
          )}
          <Button size="lg" variant={buy ? "success" : "danger"} className="w-full" loading={busy} onClick={submit}>
            {buy ? "Buy" : "Sell"} {qty} {symbol} {type === "LIMIT" && price ? `@ ${inr(Number(price))}` : "at market"}
          </Button>
        </div>
      ) : undefined}>
      {!symbol ? <SymbolPicker onPick={setSymbol} /> : !isAdmin ? (
        <Notice tone="neutral">View-only account: orders need an admin.</Notice>
      ) : (
        <div className="space-y-4 pt-1">
          <div className="grid grid-cols-2 gap-1 rounded-2xl bg-ink-850 p-1">
            {(["BUY", "SELL"] as const).map((s) => (
              <button key={s} type="button" onClick={() => setSide(s)}
                className={cx("rounded-xl py-2.5 text-sm font-bold transition",
                  side === s ? (s === "BUY" ? "bg-emerald-500 text-onaccent shadow" : "bg-rose-500 text-onaccent shadow") : "text-gray-400 hover:text-white")}>
                {s === "BUY" ? "Buy" : "Sell"}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Quantity">
              <div className="flex items-center rounded-xl border border-white/[0.1] bg-ink-850">
                <button type="button" aria-label="Less" onClick={() => setQty((x) => Math.max(1, x - 1))} className="grid h-10 w-10 place-items-center text-gray-400 hover:text-white"><Minus className="h-4 w-4" /></button>
                <input inputMode="numeric" value={qty} onChange={(e) => setQty(Math.max(0, Math.floor(Number(e.target.value.replace(/\D/g, "")) || 0)))}
                  className="num h-10 w-full min-w-0 bg-transparent text-center text-sm font-semibold text-white outline-none" aria-label="Quantity" />
                <button type="button" aria-label="More" onClick={() => setQty((x) => x + 1)} className="grid h-10 w-10 place-items-center text-gray-400 hover:text-white"><Plus className="h-4 w-4" /></button>
              </div>
            </Field>
            <Field label="Order type">
              <Segmented value={type} onChange={setType} options={[{ value: "MARKET", label: "Market" }, { value: "LIMIT", label: "Limit" }]} />
            </Field>
          </div>
          {type === "LIMIT" && (
            <Field label="Limit price (₹)"><input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className="field num h-10" /></Field>
          )}
          {buy && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Stop-loss (₹)" hint={px ? <button type="button" className="link" onClick={() => setStop((px * 0.97).toFixed(2))}>−3% → {inr(px * 0.97)}</button> : undefined}>
                <input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} placeholder="optional" className="field num h-10" />
              </Field>
              <Field label="Target (₹)" hint={px ? <button type="button" className="link" onClick={() => setTarget((px * 1.06).toFixed(2))}>+6% → {inr(px * 1.06)}</button> : undefined}>
                <input inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="optional" className="field num h-10" />
              </Field>
            </div>
          )}
          <div className="grid grid-cols-3 gap-2 rounded-2xl bg-ink-850 p-3 text-center">
            <div><div className="text-[11px] text-gray-500">Approx. value</div><div className="num text-sm font-bold text-white">{inr(m.value, 0)}</div></div>
            <div><div className="text-[11px] text-gray-500">Risk to stop</div><div className="num text-sm font-bold text-rose-400">{m.risk == null ? "—" : inr(m.risk, 0)}</div></div>
            <div><div className="text-[11px] text-gray-500">Reward : risk</div><div className="num text-sm font-bold text-emerald-400">{m.rr == null ? "—" : `${m.rr.toFixed(2)} : 1`}</div></div>
          </div>
          {error && <Notice tone="error">{error}</Notice>}
          <p className="flex items-start gap-1.5 text-[11px] text-gray-500">
            <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0 text-emerald-400" />
            Goes through the safety checker (limits, funds, kill switch). Paper orders are placed inside a running session; the target is set on the position&apos;s exit plan.
          </p>
        </div>
      )}
    </Sheet>
  );
}
