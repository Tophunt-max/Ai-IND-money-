"use client";

import {
  Ban, CirclePlay, CircleStop, Gauge, Pause, Pencil, Play, Power, Radio, RefreshCw, Send,
  ShieldAlert, ShieldCheck, SlidersHorizontal, Target, TrendingUp, X, Zap,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import {
  Badge, Button, Card, Empty, ErrorBox, Field, KV, Notice, PageTitle, pnlClass, Segmented,
  Select, Skeleton, StatCard, Table,
} from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { saveEnv, type EnvData } from "@/lib/env";
import { useApi } from "@/lib/hooks";
import { useEventStream } from "@/lib/stream";

interface Row {
  symbol: string;
  quantity: number;
  product: string;
  entry_price: number;
  ltp: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  stop_loss: number | null;
  target: number | null;
  target_hit: boolean;
  booked_qty: number;
  high_water_mark: number;
  exiting: boolean;
  pending_exit: boolean;
}

interface Control {
  mode: "paper" | "live";
  scheduler: { ok: boolean; enabled?: boolean; mode?: string; confirm_live?: boolean; start?: string; deadline?: string; error?: string };
  halt: { halted: boolean; reason: string; since: string; source: string; text: string };
  session: null | {
    phase: string; mode: string; session_date: string; started_at: number; stopping: boolean;
    candidates_scanned: number; candidates_analyzed: number; trades_opened: number;
    trades_rejected: number; sells_executed: number; orders_unconfirmed: number; errors: string[];
    age_s: number;
  };
  monitor: null | {
    mode: string; live: boolean; positions: Row[]; sells_executed: number; total_pnl: number;
    exit_reasons: string[]; feed: { connected: boolean; ticks: number } | null;
    check_every_s: number; age_s: number;
  };
  scalper: null | Scalper;
  scalp_enabled: boolean;
  fno: null | Fno;
  fno_enabled: boolean;
  fno_instrument: string;
  active: boolean;
  pending_commands: number;
  last_session: null | { phase: string; session_date: string; updated_at: number; errors: string[] };
}

interface ScalpRow {
  symbol: string; strategy: string; quantity: number; entry_price: number; ltp: number | null;
  stop_loss: number; target: number; breakeven: boolean; high: number; pnl: number | null;
  opened_at: string;
}

interface Scalper {
  mode: string; positions: ScalpRow[];
  trades: { symbol: string; strategy: string; qty: number; entry: number; exit: number; pnl: number; reason: string; opened_at: string; closed_at: string }[];
  entries: number; net_pnl: number; by_strategy: Record<string, { trades: number; wins: number; pnl: number }>;
  skipped_cost: number; rejected: number; symbols: string[]; strategies: string[]; window: string;
  limits: { max_trades: number; max_open: number; max_daily_loss: number };
  blocked: string | null; age_s: number;
}

interface FnoRow {
  symbol: string; underlying: string; kind: string; strategy: string; quantity: number;
  lots: number; lot_size: number; entry_price: number; ltp: number | null; stop_loss: number;
  breakeven: boolean; underlying_ltp: number | null; underlying_stop: number | null;
  underlying_target: number | null; pnl: number | null; opened_at: string; expiry: string | null;
}

interface Fno {
  mode: string; instrument: string; positions: FnoRow[];
  trades: Scalper["trades"];
  entries: number; net_pnl: number; by_strategy: Scalper["by_strategy"];
  skipped_cost: number; skipped_size: number; skipped_contract: number; rejected: number;
  underlyings: Record<string, number | null>; strategies: string[]; window: string;
  limits: { max_trades: number; max_open: number; max_daily_loss: number; max_lots: number; risk_per_trade: number };
  notes: string[]; blocked: string | null; age_s: number;
}

interface Order {
  order_id: string; side: string; status: string; state: string; name: string;
  requested: number | null; filled: number | null; price: number | null; own: boolean;
  open: boolean; created_at: string | null; message: string;
}

const EXIT_KEYS = [
  ["SKOPAQ_MONITOR_TARGET_MODE", "Target mode"],
  ["SKOPAQ_MONITOR_TARGET_RR", "Risk:reward (rr)"],
  ["SKOPAQ_MONITOR_TARGET_PCT", "Target % (pct)"],
  ["SKOPAQ_MONITOR_TARGET_INR", "Target ₹ (inr)"],
  ["SKOPAQ_MONITOR_PARTIAL_BOOKING_PCT", "Book at target (0–1)"],
  ["SKOPAQ_MONITOR_TRAILING_STOP_PCT", "Trailing stop"],
  ["SKOPAQ_MONITOR_HARD_STOP_PCT", "Hard stop"],
  ["SKOPAQ_DAEMON_MAX_TRADES_PER_SESSION", "Max BUYs / session"],
  ["SKOPAQ_MAX_SHARES_PER_ORDER", "Max shares / equity order (≤ 5000)"],
  ["SKOPAQ_MAX_LOTS_PER_ORDER", "Max lots / F&O order (≤ 20)"],
] as const;

const SCALP_KEYS = [
  ["SKOPAQ_SCALP_ENABLED", "Scalper on"],
  ["SKOPAQ_SCALP_SYMBOLS", "Symbols"],
  ["SKOPAQ_SCALP_STRATEGIES", "Strategies (priority)"],
  ["SKOPAQ_SCALP_RISK_PER_TRADE_PCT", "Risk / scalp"],
  ["SKOPAQ_SCALP_MAX_POSITION_VALUE_INR", "Max value / scalp ₹"],
  ["SKOPAQ_SCALP_MAX_TRADES_PER_DAY", "Max scalps / day"],
  ["SKOPAQ_SCALP_MAX_OPEN", "Max open"],
  ["SKOPAQ_SCALP_MAX_DAILY_LOSS_INR", "Daily loss limit ₹"],
  ["SKOPAQ_SCALP_ENTRY_START", "First entry"],
  ["SKOPAQ_SCALP_ENTRY_END", "Last entry"],
  ["SKOPAQ_SCALP_FLATTEN_AT", "Flatten at"],
  ["SKOPAQ_SCALP_RR", "Risk:reward"],
] as const;

const FNO_KEYS = [
  ["SKOPAQ_FNO_ENABLED", "F&O engine on"],
  ["SKOPAQ_FNO_UNDERLYINGS", "Underlyings"],
  ["SKOPAQ_FNO_INSTRUMENT", "Instrument"],
  ["SKOPAQ_FNO_ALLOW_BEARISH", "Buy PEs on bearish setups"],
  ["SKOPAQ_FNO_STRATEGIES", "Strategies (priority)"],
  ["SKOPAQ_FNO_STRIKE_OFFSET", "Strike (0 ATM, -1 ITM, +1 OTM)"],
  ["SKOPAQ_FNO_RISK_PER_TRADE_INR", "Risk / trade ₹"],
  ["SKOPAQ_FNO_MAX_LOTS", "Max lots / trade (≤ max lots / F&O order)"],
  ["SKOPAQ_FNO_MAX_PREMIUM_INR", "Max premium / trade ₹"],
  ["SKOPAQ_FNO_PREMIUM_STOP_PCT", "Premium stop"],
  ["SKOPAQ_FNO_TRAIL_PCT", "Trail after +1R"],
  ["SKOPAQ_FNO_MAX_TRADES_PER_DAY", "Max trades / day"],
  ["SKOPAQ_FNO_MAX_DAILY_LOSS_INR", "Daily loss limit ₹"],
  ["SKOPAQ_FNO_ENTRY_START", "First entry"],
  ["SKOPAQ_FNO_ENTRY_END", "Last entry"],
  ["SKOPAQ_FNO_FLATTEN_AT", "Flatten at"],
  ["SKOPAQ_FNO_RR", "Risk:reward (underlying)"],
] as const;

const n2 = (v: number | null | undefined) => (v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }));

function confirmLive(what: string): boolean {
  const typed = prompt(`⚠️ REAL MONEY\n\n${what}\n\nType LIVE to confirm:`);
  return (typed || "").trim() === "LIVE";
}

export default function ControlPage() {
  const { isAdmin } = useAuth();
  const stream = useEventStream<Control>("/api/dashboard/control/stream", "/api/dashboard/control");
  const c = stream.data;
  const live = c?.mode === "live";
  const orders = useApi<{ orders: Order[]; note?: string }>(live ? "/api/dashboard/control/orders" : null, 15000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "warning" | "error"; text: string } | null>(null);

  const act = async (key: string, path: string, body: unknown = {}, done?: string) => {
    setBusy(key);
    setMsg(null);
    try {
      const res: any = await api(path, { method: "POST", json: body });
      const text = res?.message ?? done ?? "Done";
      const tone = res?.ok === false ? "error" : res?.warning ? "warning" : "ok";
      setMsg({ tone, text: res?.warning ? `${text} — ${res.warning}` : text });
      stream.reload();
      if (live) orders.reload();
      return res;
    } catch (e: any) {
      setMsg({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const positions = c?.monitor?.positions ?? [];
  const totalPnl = positions.reduce((s, p) => s + (p.pnl || 0), 0);
  const engineTone = c?.halt.halted ? "warning" : c?.active ? "ok" : "neutral";

  return (
    <div className="space-y-6">
      <PageTitle title="Control center" icon={Gauge}
        subtitle="Run the auto-trading engine: pause, start, stop, close positions, change stops and targets, place orders"
        right={<div className="flex items-center gap-2">
          <Badge tone={stream.live ? "ok" : "warning"} dot>{stream.live ? "Live" : "Polling"}</Badge>
          {c && <Badge tone={live ? "error" : "info"}>{live ? "LIVE · real money" : "PAPER"}</Badge>}
        </div>} />

      <ErrorBox error={stream.error && !c ? stream.error : null} />
      {msg && <Notice tone={msg.tone} title={msg.tone === "error" ? "Not done" : undefined}>{msg.text}</Notice>}
      {!isAdmin && c && <Notice tone="info">View only: an admin account can use the controls.</Notice>}

      {!c ? (
        <div className="grid gap-3 sm:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard title="Engine" icon={Power} tone={engineTone}
              value={c.halt.halted ? "Paused" : c.session ? c.session.phase : c.monitor ? "Monitoring" : "Idle"}
              detail={c.session?.stopping ? "stopping…" : c.session ? `session ${c.session.session_date}` : c.last_session ? `last: ${c.last_session.session_date} ${c.last_session.phase}` : "no session running"} />
            <StatCard title="Auto sessions" icon={CirclePlay} tone={c.scheduler.enabled ? "ok" : "neutral"}
              value={!c.scheduler.ok ? "Invalid" : c.scheduler.enabled ? "On" : "Off"}
              detail={c.scheduler.ok ? `${c.scheduler.mode}${c.scheduler.mode === "live" && !c.scheduler.confirm_live ? " (not confirmed)" : ""} · ${c.scheduler.start}–${c.scheduler.deadline}` : c.scheduler.error} />
            <StatCard title="Open P&L" icon={Target} tone={totalPnl > 0 ? "ok" : totalPnl < 0 ? "error" : "neutral"}
              value={<span className={pnlClass(totalPnl)}>{inr(totalPnl)}</span>}
              detail={`${positions.length} position(s) · booked ${inr(c.monitor?.total_pnl ?? 0)}`} />
            <StatCard title="Price feed" icon={Radio} tone={c.monitor?.feed?.connected ? "ok" : c.monitor ? "warning" : "neutral"}
              value={!c.monitor ? "—" : c.monitor.feed ? (c.monitor.feed.connected ? "WebSocket" : "REST fallback") : "REST"}
              detail={c.monitor ? `checks every ${c.monitor.check_every_s}s${c.monitor.feed ? ` · ${c.monitor.feed.ticks} ticks` : ""}` : "no monitor running"} />
          </div>

          <ReadinessCard live={live} />

          {c.halt.halted && <Notice tone="warning" icon={Pause} title="New BUYs are paused">{c.halt.text}. Exits, stops and targets keep running.</Notice>}

          {isAdmin && (
            <Card title="Engine controls" icon={SlidersHorizontal}
              subtitle="Pause stops new BUYs only. Stop ends the running session, which sells what it holds.">
              <div className="flex flex-wrap gap-2">
                {c.halt.halted ? (
                  <Button variant="success" icon={Play} loading={busy === "resume"} onClick={() => act("resume", "/api/dashboard/control/resume", {}, "Trading resumed")}>Resume BUYs</Button>
                ) : (
                  <Button variant="subtle" icon={Pause} loading={busy === "pause"} onClick={() => {
                    const reason = prompt("Pause new BUYs — reason (optional):", "paused from the dashboard");
                    if (reason !== null) act("pause", "/api/dashboard/control/pause", { reason }, "New BUYs paused");
                  }}>Pause BUYs</Button>
                )}
                <Button variant="ghost" icon={Power} loading={busy === "auto"} disabled={!c.scheduler.ok}
                  onClick={() => act("auto", "/api/dashboard/control/auto", { enabled: !c.scheduler.enabled }, `Auto sessions ${c.scheduler.enabled ? "off" : "on"}`)}>
                  Auto sessions {c.scheduler.enabled ? "off" : "on"}
                </Button>
                <Button variant="primary" icon={CirclePlay} loading={busy === "start"} disabled={c.active || !c.scheduler.enabled}
                  onClick={() => {
                    if (live && c.scheduler.mode === "live" && !confirmLive("Start a LIVE session now: it scans, buys and sells with real money.")) return;
                    act("start", "/api/dashboard/control/start", { job: "daemon" }, "Start requested");
                  }}>Start session now</Button>
                {live && (
                  <Button variant="ghost" icon={ShieldAlert} loading={busy === "startmon"} disabled={c.active || !c.scheduler.enabled}
                    onClick={() => act("startmon", "/api/dashboard/control/start", { job: "monitor" }, "Monitor start requested")}>Start monitor</Button>
                )}
                <Button variant="danger" icon={CircleStop} loading={busy === "stop"} disabled={!c.active}
                  onClick={() => {
                    if (!confirm("Stop the running session? A daemon session sells every position it holds (CLOSING).")) return;
                    act("stop", "/api/dashboard/control/stop", { reason: "stopped from the dashboard" }, "Stop requested");
                  }}>Stop session</Button>
              </div>
              {c.session && (
                <div className="mt-5">
                  <KV cols={3} items={[
                    ["Phase", c.session.phase],
                    ["Scanned / analysed", `${c.session.candidates_scanned} / ${c.session.candidates_analyzed}`],
                    ["BUYs / rejected", `${c.session.trades_opened} / ${c.session.trades_rejected}`],
                    ["Exits", String(c.session.sells_executed)],
                    ["Unconfirmed", String(c.session.orders_unconfirmed)],
                    ["Updated", `${Math.round(c.session.age_s)}s ago`],
                  ]} />
                  {c.session.errors?.length > 0 && <div className="mt-3"><ErrorBox error={c.session.errors.join("; ")} /></div>}
                </div>
              )}
              <p className="mt-4 text-xs text-gray-500">
                The scheduler starts a requested session within about 30 s, between 09:00 and 15:00 IST on a trading day (Telegram says if it refuses).
                {live && " Start monitor guards positions you hold (e.g. bought here) without opening new ones."}
              </p>
            </Card>
          )}

          <PositionsCard c={c} isAdmin={isAdmin} busy={busy} act={act} live={live} />

          <ScalperCard c={c} isAdmin={isAdmin} busy={busy} act={act} live={live} />

          <FnoCard c={c} isAdmin={isAdmin} busy={busy} act={act} live={live} />

          {isAdmin && <OrderCard live={live} active={!!c.monitor} busy={busy} act={act} />}

          {live && (
            <Card title="Today's orders" subtitle="The INDstocks order book. Skopaq's own orders are marked."
              right={<Button variant="ghost" size="sm" icon={RefreshCw} onClick={orders.reload} loading={orders.loading}>Refresh</Button>}>
              <ErrorBox error={orders.error} />
              {!orders.data ? <Skeleton className="h-16" /> : orders.data.orders.length === 0 ? <Empty>No orders today.</Empty> : (
                <Table head={["Order", "Side", "Qty", "Filled", "Price", "Status", ""]}>
                  {orders.data.orders.map((o) => (
                    <tr key={o.order_id} className="hover:bg-white/[0.02]">
                      <td><div className="font-medium text-white">{o.name || o.order_id}</div>
                        <div className="text-[11px] text-gray-500">{o.order_id}{o.own && " · Skopaq"}{o.created_at && ` · ${when(o.created_at)}`}</div></td>
                      <td><Badge tone={o.side === "BUY" ? "ok" : "error"}>{o.side}</Badge></td>
                      <td>{o.requested ?? "—"}</td>
                      <td>{o.filled ?? "—"}</td>
                      <td>{inr(o.price)}</td>
                      <td><Badge tone={o.open ? "warning" : o.state === "filled" ? "ok" : "neutral"}>{o.status}</Badge></td>
                      <td>{isAdmin && o.open && (
                        <Button variant="ghost" size="sm" icon={Ban} loading={busy === `cancel-${o.order_id}`}
                          onClick={() => confirm(`Cancel order ${o.order_id}?`) && act(`cancel-${o.order_id}`, `/api/dashboard/control/orders/${o.order_id}/cancel`, {}, "Cancel sent")}>Cancel</Button>
                      )}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          )}

          {isAdmin && <SettingsCard keys={EXIT_KEYS} title="Exits & risk"
            subtitle="Target, booking and stops for new swing positions, and the order size limits of every strategy (equity shares, F&O lots; capped by the safety ceilings 5000 / 20, and still bound by the ₹ order-value and 15% position rules). Changes apply from the next session. All settings: Environment." />}
          {isAdmin && <SettingsCard keys={SCALP_KEYS} title="Scalping"
            subtitle="The intraday scalper runs inside the daily session when on (INTRADAY orders, flattened before the close). Changes apply from the next session." />}
          {isAdmin && <SettingsCard keys={FNO_KEYS} title="F&O (options buying)"
            subtitle="Bullish setups buy a CE, bearish ones a PE (or the future, long only). Never option writing or short futures. INTRADAY, flattened before the close. Changes apply from the next session." />}

          {c.monitor?.exit_reasons?.length ? (
            <Card title="Recent exits" subtitle="This session">
              <ul className="space-y-1.5 text-sm text-gray-300">
                {c.monitor.exit_reasons.slice().reverse().map((r, i) => <li key={i} className="font-mono text-xs">{r}</li>)}
              </ul>
            </Card>
          ) : null}
        </>
      )}
    </div>
  );
}

function PositionsCard({ c, isAdmin, busy, act, live }: {
  c: Control; isAdmin: boolean; busy: string | null; live: boolean;
  act: (key: string, path: string, body?: unknown, done?: string) => Promise<any>;
}) {
  const [edit, setEdit] = useState<string | null>(null);
  const [sl, setSl] = useState("");
  const [tg, setTg] = useState("");
  const rows = c.monitor?.positions ?? [];

  const save = async (symbol: string) => {
    const body: any = { symbol };
    if (sl.trim()) body.stop_loss = Number(sl);
    if (tg.trim()) body.target = Number(tg);
    const res = await act(`plan-${symbol}`, "/api/dashboard/control/plan", body);
    if (res?.ok !== false) setEdit(null);
  };
  const close = (symbol?: string) => {
    const what = symbol ? `Close ${symbol} at MARKET?` : "Close ALL positions at MARKET?";
    if (live ? !confirmLive(what) : !confirm(what)) return;
    act(symbol ? `close-${symbol}` : "closeall", "/api/dashboard/control/close", symbol ? { symbol } : {});
  };

  return (
    <Card title="Live positions" icon={Target}
      subtitle={c.monitor ? `From the running monitor · updated ${Math.round(c.monitor.age_s)}s ago` : "No monitor is running"}
      right={isAdmin && (
        <Button variant="danger" size="sm" icon={X} loading={busy === "closeall"} disabled={!rows.length && !(live && !c.monitor)} onClick={() => close()}>Close all</Button>
      )}>
      {!c.monitor ? (
        <Empty icon={Target}>
          Positions show here while a session or monitor runs.{" "}
          {live ? <>Broker positions: <Link href="/portfolio" className="link">Portfolio</Link>. Close all works without a session too.</> : "Paper positions exist only inside a session."}
        </Empty>
      ) : rows.length === 0 ? <Empty>No open positions.</Empty> : (
        <Table head={["Symbol", "Qty", "Entry", "LTP", "P&L", "Stop", "Target", "High", ""]}>
          {rows.map((p) => (
            <tr key={p.symbol} className="hover:bg-white/[0.02]">
              <td>
                <div className="font-medium text-white">{p.symbol}</div>
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {p.target_hit && <Badge tone="ok">booked {p.booked_qty} · trailing</Badge>}
                  {p.exiting && <Badge tone="warning">selling</Badge>}
                  {p.pending_exit && <Badge tone="warning">exit working</Badge>}
                </div>
              </td>
              <td>{p.quantity}</td>
              <td>{n2(p.entry_price)}</td>
              <td>{n2(p.ltp)}</td>
              <td className={pnlClass(p.pnl)}>{inr(p.pnl)}<div className="text-[11px]">{p.pnl_pct == null ? "" : `${p.pnl_pct > 0 ? "+" : ""}${p.pnl_pct}%`}</div></td>
              {edit === p.symbol ? (
                <>
                  <td><input className="field w-24 py-1 text-right" placeholder={n2(p.stop_loss)} value={sl} onChange={(e) => setSl(e.target.value)} /></td>
                  <td><input className="field w-24 py-1 text-right" placeholder={p.target ? n2(p.target) : "0 = off"} value={tg} onChange={(e) => setTg(e.target.value)} /></td>
                  <td colSpan={2}><div className="flex justify-end gap-1">
                    <Button size="sm" loading={busy === `plan-${p.symbol}`} onClick={() => save(p.symbol)}>Save</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button>
                  </div></td>
                </>
              ) : (
                <>
                  <td className="text-rose-300">{n2(p.stop_loss)}</td>
                  <td className="text-emerald-300">{p.target ? n2(p.target) : "—"}</td>
                  <td>{n2(p.high_water_mark)}</td>
                  <td>{isAdmin && (
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" icon={Pencil} title="Change stop / target" onClick={() => { setEdit(p.symbol); setSl(""); setTg(""); }} />
                      <Button size="sm" variant="danger" loading={busy === `close-${p.symbol}`} disabled={p.exiting || p.quantity <= 0} onClick={() => close(p.symbol)}>Close</Button>
                    </div>
                  )}</td>
                </>
              )}
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

function OrderCard({ live, active, busy, act }: {
  live: boolean; active: boolean; busy: string | null;
  act: (key: string, path: string, body?: unknown, done?: string) => Promise<any>;
}) {
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [qty, setQty] = useState("1");
  const [type, setType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [price, setPrice] = useState("");
  const [stop, setStop] = useState("");
  const blocked = !live && !active;

  const place = () => {
    const sym = symbol.trim().toUpperCase();
    const q = parseInt(qty, 10);
    if (!sym || !q) return;
    const desc = `${side} ${q} ${sym} ${type}${type === "LIMIT" ? ` @ ${price}` : ""}`;
    if (live ? !confirmLive(`Place a real order: ${desc}`) : !confirm(`Place ${desc} (paper)?`)) return;
    act("order", "/api/dashboard/control/orders", {
      symbol: sym, side, quantity: q, order_type: type,
      price: type === "LIMIT" && price ? Number(price) : undefined,
      stop_loss: side === "BUY" && stop ? Number(stop) : undefined,
      confirm_live: live,
    });
  };

  return (
    <Card title="Manual order" icon={Send}
      subtitle="Through the same safety checks as the engine. A BUY gets an exit plan (stop, target) the monitor follows.">
      {blocked && <Notice tone="info" className="mb-4">Paper mode: orders are placed inside a running session — start one first.</Notice>}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Symbol" className="w-36"><input className="field uppercase" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="RELIANCE" /></Field>
        <Field label="Side"><Segmented value={side} onChange={setSide} options={[{ value: "BUY", label: "BUY" }, { value: "SELL", label: "SELL" }]} /></Field>
        <Field label="Quantity" className="w-28"><input className="field" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))} /></Field>
        <Field label="Type" className="w-32">
          <Select value={type} onChange={(e) => setType(e.target.value as any)}><option>MARKET</option><option>LIMIT</option></Select>
        </Field>
        {type === "LIMIT" && <Field label="Price" className="w-28"><input className="field" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} /></Field>}
        {side === "BUY" && <Field label="Stop-loss (optional)" className="w-36"><input className="field" inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} placeholder="hard stop" /></Field>}
        <Button variant={side === "BUY" ? "success" : "danger"} icon={Send} loading={busy === "order"}
          disabled={blocked || !symbol.trim() || !parseInt(qty, 10) || (type === "LIMIT" && !price)} onClick={place}>
          {side} {live ? "(LIVE)" : ""}
        </Button>
      </div>
    </Card>
  );
}

function ScalperCard({ c, isAdmin, busy, act, live }: {
  c: Control; isAdmin: boolean; busy: string | null; live: boolean;
  act: (key: string, path: string, body?: unknown, done?: string) => Promise<any>;
}) {
  const sc = c.scalper;
  const close = (symbol?: string) => {
    const what = symbol ? `Close the ${symbol} scalp at MARKET?` : "Close ALL scalps at MARKET?";
    if (live ? !confirmLive(what) : !confirm(what)) return;
    act(symbol ? `sclose-${symbol}` : "scloseall", "/api/dashboard/control/close",
      symbol ? { symbol, scope: "scalp" } : { scope: "scalp" });
  };
  if (!sc) {
    return (
      <Card title="Scalper" icon={Zap} subtitle={c.scalp_enabled ? "On: it runs inside the next session (09:30–14:45 entries)" : "Off — turn it on under Scalping below"}>
        <Empty icon={Zap}>{c.scalp_enabled ? "Not running now." : "The intraday scalper is off."} Backtest it first: <code className="text-xs">skopaq scalp-backtest RELIANCE --days 5</code></Empty>
      </Card>
    );
  }
  const strat = Object.entries(sc.by_strategy || {});
  return (
    <Card title="Scalper" icon={Zap}
      subtitle={`${sc.strategies.join(", ")} on ${sc.symbols.length} symbol(s) · ${sc.window} · updated ${Math.round(sc.age_s)}s ago`}
      right={isAdmin && <Button variant="danger" size="sm" icon={X} loading={busy === "scloseall"} disabled={!sc.positions.length} onClick={() => close()}>Close all scalps</Button>}>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Net P&L (after charges)" value={<span className={pnlClass(sc.net_pnl)}>{inr(sc.net_pnl)}</span>} tone={sc.net_pnl > 0 ? "ok" : sc.net_pnl < 0 ? "error" : "neutral"} />
        <StatCard title="Scalps today" value={`${sc.entries} / ${sc.limits.max_trades}`} detail={`${sc.positions.length} open (max ${sc.limits.max_open})`} />
        <StatCard title="Skipped" value={String(sc.skipped_cost)} detail={`target below the charges · ${sc.rejected} refused`} />
        <StatCard title="New entries" value={sc.blocked ? "No" : "Yes"} tone={sc.blocked ? "warning" : "ok"} detail={sc.blocked || "waiting for a setup"} />
      </div>
      {strat.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {strat.map(([name, row]) => (
            <Badge key={name} tone={row.pnl > 0 ? "ok" : row.pnl < 0 ? "error" : "neutral"}>
              {name}: {row.trades} ({row.wins} won) {inr(row.pnl, 0)}
            </Badge>
          ))}
        </div>
      )}
      <div className="mt-5">
        {sc.positions.length === 0 ? <Empty>No open scalps.</Empty> : (
          <Table head={["Symbol", "Qty", "Entry", "LTP", "P&L", "Stop", "Target", ""]}>
            {sc.positions.map((p) => (
              <tr key={p.symbol} className="hover:bg-white/[0.02]">
                <td><div className="font-medium text-white">{p.symbol}</div><div className="text-[11px] text-gray-500">{p.strategy}{p.breakeven && " · trailing"}</div></td>
                <td>{p.quantity}</td>
                <td>{n2(p.entry_price)}</td>
                <td>{n2(p.ltp)}</td>
                <td className={pnlClass(p.pnl)}>{inr(p.pnl)}</td>
                <td className="text-rose-300">{n2(p.stop_loss)}</td>
                <td className="text-emerald-300">{n2(p.target)}</td>
                <td>{isAdmin && <Button size="sm" variant="danger" loading={busy === `sclose-${p.symbol}`} onClick={() => close(p.symbol)}>Close</Button>}</td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      {sc.trades.length > 0 && (
        <div className="mt-5">
          <Table head={["Trade", "Qty", "Entry", "Exit", "Net", "Why"]}>
            {sc.trades.slice().reverse().map((t, i) => (
              <tr key={i}>
                <td><div className="text-white">{t.symbol}</div><div className="text-[11px] text-gray-500">{t.strategy} · {t.opened_at.slice(11, 16)}–{t.closed_at.slice(11, 16)}</div></td>
                <td>{t.qty}</td>
                <td>{n2(t.entry)}</td>
                <td>{n2(t.exit)}</td>
                <td className={pnlClass(t.pnl)}>{inr(t.pnl)}</td>
                <td className="max-w-[260px] truncate text-xs text-gray-400">{t.reason}</td>
              </tr>
            ))}
          </Table>
        </div>
      )}
    </Card>
  );
}

function SettingsCard({ keys, title, subtitle }: {
  keys: readonly (readonly [string, string])[]; title: string; subtitle: string;
}) {
  const env = useApi<EnvData>("/api/dashboard/settings/env");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const settings = useMemo(() => {
    const m: Record<string, { value: string; choices: string[]; help: string; kind: string }> = {};
    for (const s of env.data?.settings ?? []) m[s.key] = { value: s.value ?? s.default, choices: s.choices, help: s.help, kind: s.kind };
    return m;
  }, [env.data]);
  useEffect(() => setDraft({}), [env.data]);

  const changed = Object.entries(draft).filter(([k, v]) => settings[k] && v !== settings[k].value);
  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      env.setData(await saveEnv({ set: Object.fromEntries(changed) }));
      setSaved(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={title} icon={SlidersHorizontal} subtitle={subtitle}
      right={<Link href="/settings/environment" className="text-sm text-gray-400 underline">Environment</Link>}>
      <ErrorBox error={env.error || error} />
      {saved && <Notice tone="ok" className="mb-4">Saved.</Notice>}
      {!env.data ? <Skeleton className="h-20" /> : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {keys.map(([key, label]) => {
              const s = settings[key];
              if (!s) return null;
              const value = draft[key] ?? s.value;
              return (
                <Field key={key} label={label} hint={s.help}>
                  {s.choices.length || s.kind === "bool" ? (
                    <Select value={value} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}>
                      {(s.choices.length ? s.choices : ["true", "false"]).map((ch) => <option key={ch}>{ch}</option>)}
                    </Select>
                  ) : (
                    <input className="field" value={value} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
                  )}
                </Field>
              );
            })}
          </div>
          <div className="mt-4 flex justify-end">
            <Button icon={SlidersHorizontal} loading={busy} disabled={!changed.length} onClick={save}>Save {changed.length || ""}</Button>
          </div>
        </>
      )}
    </Card>
  );
}

function FnoCard({ c, isAdmin, busy, act, live }: {
  c: Control; isAdmin: boolean; busy: string | null; live: boolean;
  act: (key: string, path: string, body?: unknown, done?: string) => Promise<any>;
}) {
  const f = c.fno;
  const close = (symbol?: string) => {
    const what = symbol ? `Close ${symbol} at MARKET?` : "Close ALL F&O positions at MARKET?";
    if (live ? !confirmLive(what) : !confirm(what)) return;
    act(symbol ? `fclose-${symbol}` : "fcloseall", "/api/dashboard/control/close",
      symbol ? { symbol, scope: "fno" } : { scope: "fno" });
  };
  if (!f) {
    return (
      <Card title="F&O" icon={TrendingUp} subtitle={c.fno_enabled ? `On (${c.fno_instrument}): it runs inside the next session` : "Off — turn it on under F&O below"}>
        <Empty icon={TrendingUp}>{c.fno_enabled ? "Not running now." : "The F&O engine is off."} Try it on paper first: <code className="text-xs">skopaq fno --underlyings NIFTY</code></Empty>
        {isAdmin && live && (
          <div className="mt-3"><Button variant="danger" size="sm" icon={X} loading={busy === "fcloseall"} onClick={() => close()}>Close all F&O at the broker</Button></div>
        )}
      </Card>
    );
  }
  const strat = Object.entries(f.by_strategy || {});
  const unders = Object.entries(f.underlyings || {});
  return (
    <Card title="F&O" icon={TrendingUp}
      subtitle={`${f.instrument} · ${f.strategies.join(", ")} · ${f.window} · updated ${Math.round(f.age_s)}s ago`}
      right={isAdmin && <Button variant="danger" size="sm" icon={X} loading={busy === "fcloseall"} disabled={!f.positions.length} onClick={() => close()}>Close all F&O</Button>}>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Net P&L (after charges)" value={<span className={pnlClass(f.net_pnl)}>{inr(f.net_pnl)}</span>} tone={f.net_pnl > 0 ? "ok" : f.net_pnl < 0 ? "error" : "neutral"} />
        <StatCard title="Trades today" value={`${f.entries} / ${f.limits.max_trades}`} detail={`${f.positions.length} open (max ${f.limits.max_open}) · ≤${f.limits.max_lots} lot(s)`} />
        <StatCard title="Skipped" value={String(f.skipped_cost + f.skipped_size + f.skipped_contract)} detail={`${f.skipped_size} too big for ₹${f.limits.risk_per_trade} risk · ${f.skipped_cost} charges · ${f.skipped_contract} no contract · ${f.rejected} refused`} />
        <StatCard title="New entries" value={f.blocked ? "No" : "Yes"} tone={f.blocked ? "warning" : "ok"} detail={f.blocked || "waiting for a setup"} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {unders.map(([name, ltp]) => <Badge key={name} tone="neutral">{name} {n2(ltp)}</Badge>)}
        {strat.map(([name, row]) => (
          <Badge key={name} tone={row.pnl > 0 ? "ok" : row.pnl < 0 ? "error" : "neutral"}>
            {name}: {row.trades} ({row.wins} won) {inr(row.pnl, 0)}
          </Badge>
        ))}
      </div>
      {f.notes?.length > 0 && <div className="mt-3"><Notice tone="warning">{f.notes.join(" · ")}</Notice></div>}
      <div className="mt-5">
        {f.positions.length === 0 ? <Empty>No open F&O positions.</Empty> : (
          <Table head={["Contract", "Lots", "Entry", "LTP", "P&L", "Premium stop", "Underlying stop / target", ""]}>
            {f.positions.map((p) => (
              <tr key={p.symbol} className="hover:bg-white/[0.02]">
                <td><div className="font-medium text-white">{p.symbol}</div><div className="text-[11px] text-gray-500">{p.kind} · {p.strategy}{p.breakeven && " · trailing"}{p.expiry && ` · exp ${p.expiry}`}</div></td>
                <td>{p.lots} × {p.lot_size}</td>
                <td>{n2(p.entry_price)}</td>
                <td>{n2(p.ltp)}</td>
                <td className={pnlClass(p.pnl)}>{inr(p.pnl)}</td>
                <td className="text-rose-300">{n2(p.stop_loss)}</td>
                <td className="text-xs"><span className="text-rose-300">{n2(p.underlying_stop)}</span> / <span className="text-emerald-300">{n2(p.underlying_target)}</span><div className="text-[11px] text-gray-500">{p.underlying} {n2(p.underlying_ltp)}</div></td>
                <td>{isAdmin && <Button size="sm" variant="danger" loading={busy === `fclose-${p.symbol}`} onClick={() => close(p.symbol)}>Close</Button>}</td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      {f.trades.length > 0 && (
        <div className="mt-5">
          <Table head={["Trade", "Qty", "Entry", "Exit", "Net", "Why"]}>
            {f.trades.slice().reverse().map((t, i) => (
              <tr key={i}>
                <td><div className="text-white">{t.symbol}</div><div className="text-[11px] text-gray-500">{t.strategy} · {t.opened_at.slice(11, 16)}–{t.closed_at.slice(11, 16)}</div></td>
                <td>{t.qty}</td>
                <td>{n2(t.entry)}</td>
                <td>{n2(t.exit)}</td>
                <td className={pnlClass(t.pnl)}>{inr(t.pnl)}</td>
                <td className="max-w-[260px] truncate text-xs text-gray-400">{t.reason}</td>
              </tr>
            ))}
          </Table>
        </div>
      )}
    </Card>
  );
}

interface ReadinessCheck { name: string; status: "ok" | "warn" | "fail"; detail: string; fix: string }
interface Readiness { live: boolean; passed: boolean; checked_at: string; checks: ReadinessCheck[] }

function ReadinessCard({ live }: { live: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const r = useApi<Readiness>(`/api/dashboard/readiness?live=true${refresh ? `&refresh=true&n=${refresh}` : ""}`, 300000);
  const d = r.data;
  const bad = d?.checks.filter((c) => c.status !== "ok") ?? [];
  const tone = !d ? "neutral" : !d.passed ? "error" : bad.length ? "warning" : "ok";
  return (
    <Card title="Live readiness" icon={ShieldCheck}
      subtitle={d ? `Checked ${d.checked_at.slice(11, 16)} IST · token, account, clock, static IP, funds${live ? "" : " — judged for LIVE (you are on paper)"}` : "Token, account, clock, static IP, funds"}
      right={<div className="flex items-center gap-2">
        {d && <Badge tone={tone} dot>{!d.passed ? "Not ready" : bad.length ? "Ready, with warnings" : "Ready"}</Badge>}
        <Button variant="ghost" size="sm" icon={RefreshCw} loading={r.loading} onClick={() => setRefresh((n) => n + 1)}>Check</Button>
      </div>}>
      <ErrorBox error={r.error} />
      {!d ? <Skeleton className="h-16" /> : (
        <div className="grid gap-2 sm:grid-cols-2">
          {d.checks.map((c) => (
            <div key={c.name} className="flex items-start gap-2 rounded-xl bg-white/[0.02] p-3 ring-1 ring-white/[0.05]">
              <Badge tone={c.status === "ok" ? "ok" : c.status === "warn" ? "warning" : "error"}>{c.status === "ok" ? "OK" : c.status === "warn" ? "WARN" : "FAIL"}</Badge>
              <div className="min-w-0">
                <div className="text-sm font-medium text-white">{c.name}</div>
                <div className="text-xs text-gray-400">{c.detail}</div>
                {c.fix && c.status !== "ok" && <div className="mt-1 text-[11px] text-amber-200/80">→ {c.fix}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
