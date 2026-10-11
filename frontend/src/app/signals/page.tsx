"use client";

import { AlertTriangle, CheckCircle2, Filter, ShieldCheck, Signal } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, PageTitle, pnlClass, Segmented, Skeleton, StatCard, Table } from "@/components/ui";
import { api, ApiError, inr, pct, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useAgo } from "@/lib/market";

type Status = "QUALIFIED" | "REJECTED" | "EXPIRED";

interface SignalRow {
  signal_id: string;
  strategy_id: string;
  strategy_version: string;
  symbol: string;
  direction: number;
  decided_at: string;
  timeframe_s: number;
  entry: number;
  stop: number;
  target: number;
  qty: number;
  reason: string;
  status: Status;
  reasons: string[];
  passed: string[];
  net_rr: number | null;
  net_reward: number | null;
  net_risk: number | null;
  est_charges: number | null;
  est_slippage: number | null;
  mode: string;
  evidence?: {
    source?: string;
    regime?: { label?: string; trend?: string };
    patterns?: { bias?: string; patterns?: { name: string }[] };
    data_quality?: { ok: boolean; blocking: string[]; last_bar_age_s?: number | null };
    htf?: { direction?: string; confirmed?: boolean };
  };
  execution?: { status: string; filled: number; fill_price: number | null; reason: string } | null;
  outcome?: { net_pnl: number; gross_pnl: number; charges: number; exit_reason: string } | null;
}

interface Signals { signals: SignalRow[]; counts: Record<string, number>; mode: string }

interface Blocker { internal_id: string; side: string; symbol: string; qty: string; ts: string; blocker: string }

interface Risk {
  mode: string;
  kill_switch: { halted: boolean; reason: string };
  ceilings: Record<string, number | boolean>;
  working: Record<string, number>;
  realized: { day: number; week: number; month: number; loss_streak: number; last_loss_at: string | null } | null;
  realized_source: string;
  charges: { schedule: string; effective_from: string; excluded: string[]; notes: string };
}

const statusTone = (s: string) => (s === "QUALIFIED" ? "ok" : s === "EXPIRED" ? "warning" : "error");

function Reconcile() {
  const { isAdmin } = useAuth();
  const rec = useApi<{ blocking_entries: boolean; blockers: Blocker[] }>("/api/dashboard/control/reconcile", 15000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const resolve = async (b: Blocker) => {
    const note = prompt(`Resolve ${b.side} ${b.qty} ${b.symbol}?\n\nCheck the broker's order book first. What did it show?`);
    if (!note || note.trim().length < 5) return;
    setBusy(b.internal_id);
    setMsg(null);
    try {
      await api(`/api/dashboard/control/reconcile/${b.internal_id}/resolve`, { method: "POST", json: { note: note.trim() } });
      rec.reload();
    } catch (e: any) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Reconciliation" icon={ShieldCheck}
      subtitle="New entries stop while an order's outcome is unknown. Protective exits keep running.">
      <ErrorBox error={rec.error || msg} />
      {rec.loading && !rec.data ? <Skeleton className="h-10" /> : rec.data?.blockers.length ? (
        <>
          <Notice tone="warning" title="New entries are blocked">Check each order in the broker&apos;s order book, then resolve it here.</Notice>
          <Table head={["Order", "Why", "Since", ""]} className="mt-3">
            {rec.data.blockers.map((b) => (
              <tr key={b.internal_id}>
                <td>{b.side} {b.qty} {b.symbol}<div className="text-[11px] text-gray-500">{b.internal_id.slice(0, 12)}</div></td>
                <td>{b.blocker}</td>
                <td>{when(b.ts)}</td>
                <td>{isAdmin && <Button size="sm" variant="ghost" loading={busy === b.internal_id} onClick={() => resolve(b)}>Resolve</Button>}</td>
              </tr>
            ))}
          </Table>
        </>
      ) : <div className="flex items-center gap-2 text-sm text-emerald-300"><CheckCircle2 className="h-4 w-4" /> Nothing to reconcile: entries are not blocked.</div>}
    </Card>
  );
}

function RiskCard() {
  const risk = useApi<Risk>("/api/dashboard/risk", 30000);
  const r = risk.data;
  return (
    <Card title="Risk limits" icon={AlertTriangle} subtitle="Immutable ceilings (code) and the working limits under them">
      <ErrorBox error={risk.error} />
      {!r ? <Skeleton className="h-32" /> : (
        <div className="space-y-4">
          {r.kill_switch.halted && <Notice tone="warning" title="Kill switch on">{r.kill_switch.reason}</Notice>}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard title="Realized today" value={<span className={pnlClass(r.realized?.day)}>{inr(r.realized?.day)}</span>}
              detail={r.realized_source === "supabase" ? "from stored trades" : "trade history unavailable"} />
            <StatCard title="This week" value={<span className={pnlClass(r.realized?.week)}>{inr(r.realized?.week)}</span>} />
            <StatCard title="Loss streak" value={`${r.realized?.loss_streak ?? "—"} / ${r.working.max_consecutive_losses}`}
              tone={(r.realized?.loss_streak ?? 0) >= r.working.max_consecutive_losses ? "error" : "neutral"} />
            <StatCard title="Charge schedule" value={<span className="text-sm">{r.charges.schedule}</span>} detail={`since ${r.charges.effective_from}`} />
          </div>
          <KV cols={2} items={[
            ["Daily loss stop", pct(r.ceilings.max_daily_loss_pct as number, 0)],
            ["Weekly / monthly", `${pct(r.ceilings.max_weekly_loss_pct as number, 0)} / ${pct(r.ceilings.max_monthly_loss_pct as number, 0)}`],
            ["Max position", pct(r.ceilings.max_position_pct as number, 0)],
            ["Max open positions", String(r.ceilings.max_open_positions)],
            ["Shares / order (working ≤ ceiling)", `${r.working.max_shares_per_order} ≤ ${r.ceilings.max_shares_per_position}`],
            ["Lots / order (working ≤ ceiling)", `${r.working.max_lots_per_order} ≤ ${r.ceilings.max_lots_per_position}`],
            ["Cool-down after a loss", `${r.ceilings.cool_down_after_loss_minutes} min`],
            ["One entry at a time", `waits ${r.working.entry_lock_wait_seconds}s, then refused`],
          ]} />
          <p className="text-[11px] text-gray-500">Charges are estimates ({r.charges.notes}) Not modelled: {r.charges.excluded.join(", ")}.</p>
        </div>
      )}
    </Card>
  );
}

export default function SignalsPage() {
  const [days, setDays] = useState(1);
  const [status, setStatus] = useState<"" | Status>("");
  const path = `/api/dashboard/signals?days=${days}${status ? `&status=${status}` : ""}`;
  const s = useApi<Signals>(path, 15000);
  const ago = useAgo(s.updatedAt);
  const counts = s.data?.counts || {};

  return (
    <div className="space-y-6">
      <PageTitle title="Signals & risk" icon={Signal}
        subtitle="Every setup the strategies found, the evidence they saw, and why each was traded or skipped"
        right={s.data && <Badge tone={s.data.mode === "live" ? "error" : "info"}>{s.data.mode === "live" ? "LIVE" : "PAPER"}</Badge>} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard title="Qualified" value={counts.QUALIFIED ?? 0} tone="ok" />
        <StatCard title="Rejected" value={counts.REJECTED ?? 0} tone="error" />
        <StatCard title="Expired" value={counts.EXPIRED ?? 0} tone="warning" />
        <StatCard title="Updated" value={<span className="text-sm">{ago}</span>} detail="every 15 s" />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Reconcile />
        <RiskCard />
      </div>

      <Card title="Signal log" icon={Filter} subtitle="A QUALIFIED signal still passes the risk gateway (limits, funds, kill switch) before any order"
        right={<div className="flex flex-wrap gap-2">
          <Segmented value={days} onChange={setDays} options={[{ value: 1, label: "Today" }, { value: 5, label: "5 days" }, { value: 30, label: "30 days" }]} />
          <Segmented value={status} onChange={setStatus} options={[{ value: "", label: "All" }, { value: "QUALIFIED", label: "Qualified" }, { value: "REJECTED", label: "Rejected" }]} />
        </div>}>
        <ErrorBox error={s.error} />
        {s.loading && !s.data ? <Skeleton className="h-40" /> : !s.data?.signals.length ? (
          <Empty title="No signals">The scalper writes every candidate here while a session runs.</Empty>
        ) : (
          <Table head={["Signal", "Status", "Levels", "Net R:R", "Evidence", "Result"]}>
            {s.data.signals.map((r) => (
              <tr key={r.signal_id} className="align-top">
                <td>
                  <div className="font-medium text-gray-100">{r.symbol} <span className="text-gray-500">{r.direction > 0 ? "long" : "short"}</span></div>
                  <div className="text-[11px] text-gray-500">{r.strategy_id} v{r.strategy_version} · {when(r.decided_at)} · {Math.round(r.timeframe_s / 60)}m</div>
                </td>
                <td>
                  <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                  {r.reasons.length > 0 && <ul className="mt-1 space-y-0.5 text-left text-[11px] text-rose-300/90">{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>}
                </td>
                <td className="text-xs">
                  <div>entry {r.entry.toFixed(2)} × {r.qty}</div>
                  <div className="text-rose-300">stop {r.stop.toFixed(2)}</div>
                  <div className="text-emerald-300">target {r.target.toFixed(2)}</div>
                </td>
                <td className="text-xs">
                  <div className="font-medium">{r.net_rr != null ? r.net_rr.toFixed(2) : "—"}</div>
                  <div className="text-gray-500">charges {inr(r.est_charges)}</div>
                  <div className="text-gray-500">slippage {inr(r.est_slippage)}</div>
                </td>
                <td className="text-xs">
                  <div>{r.evidence?.regime?.label ?? "—"}</div>
                  <div className="text-gray-500">{(r.evidence?.patterns?.patterns || []).map((p) => p.name).join(", ") || "no pattern"}</div>
                  <div className="text-gray-500">HTF {r.evidence?.htf?.direction ?? "—"} · {r.evidence?.source ?? "?"}{r.evidence?.data_quality && !r.evidence.data_quality.ok ? " · data!" : ""}</div>
                </td>
                <td className="text-xs">
                  {r.execution ? <div>{r.execution.status}{r.execution.fill_price ? ` @ ${r.execution.fill_price.toFixed(2)}` : ""}</div> : <div className="text-gray-500">not sent</div>}
                  {r.outcome && <div className={pnlClass(r.outcome.net_pnl)}>net {inr(r.outcome.net_pnl)} <span className="text-gray-500">({r.outcome.exit_reason})</span></div>}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
