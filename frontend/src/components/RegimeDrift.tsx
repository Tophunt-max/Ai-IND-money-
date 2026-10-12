"use client";

import { Compass, Waves } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, Notice, Table } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Row = { trades: number; expectancy: number | null; expectancy_ci90: [number, number] | null; allowed: boolean };
interface MapEntry { version: string; created_at: string; approved_by?: string; approved_at?: string; map: { regimes: Record<string, Record<string, Row>>; min_trades: number } }

function MapTable({ m }: { m: MapEntry }) {
  const rows = Object.entries(m.map.regimes).flatMap(([regime, strat]) => Object.entries(strat).map(([s, r]) => ({ regime, s, ...r })));
  return (
    <Table head={["Regime", "Strategy", "Trades", "Expectancy (90% CI)", "Use"]}>
      {rows.map((r) => (
        <tr key={`${r.regime}${r.s}`}>
          <td>{r.regime}</td><td>{r.s}</td><td>{r.trades}</td>
          <td>{inr(r.expectancy, 0)} {r.expectancy_ci90 ? `(${r.expectancy_ci90.map((x) => x.toFixed(0)).join("…")})` : ""}</td>
          <td><Badge tone={r.allowed ? "ok" : "neutral"}>{r.allowed ? "validated" : "idle"}</Badge></td>
        </tr>
      ))}
    </Table>
  );
}

/** The regime → strategy map (candidate / approved) and the champion model's drift. */
export default function RegimeDrift() {
  const { isAdmin } = useAuth();
  const reg = useApi<{ approved: MapEntry | null; candidate: MapEntry | null }>("/api/dashboard/learning/regimes", 120000);
  const drift = useApi<{ available: boolean; reason?: string; version?: string; psi?: Record<string, number>; alerts?: string[]; recent_signals?: number }>("/api/dashboard/learning/drift", 300000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const post = async (key: string, path: string, what: string) => {
    const reason = prompt(`${what}\n\nReason (logged):`);
    if (!reason || reason.trim().length < 5) return;
    setBusy(key);
    setMsg(null);
    try {
      await api(path, { method: "POST", json: { reason: reason.trim() } });
      reg.reload();
    } catch (e: any) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const d = reg.data;
  const top = Object.entries(drift.data?.psi || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 *:min-w-0">
      <Card title="Regime → strategy map" icon={Compass}
        subtitle="Which strategy may trade in which market regime, from held-out trades. Without an approved map every strategy runs."
        right={isAdmin && d?.approved && <Button size="sm" variant="ghost" loading={busy === "rb"}
          onClick={() => post("rb", "/api/dashboard/learning/regimes/rollback", "Roll back the regime map?")}>Roll back</Button>}>
        <ErrorBox error={reg.error || msg} />
        {d?.approved ? (
          <div className="space-y-2">
            <div className="text-[11px] text-gray-500">Approved {d.approved.version} by {d.approved.approved_by} · {when(d.approved.approved_at)}</div>
            <MapTable m={d.approved} />
          </div>
        ) : <Notice tone="neutral">No approved map: all configured strategies run (the safe fallback).</Notice>}
        {d?.candidate && d.candidate.version !== d.approved?.version && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-gray-400">Candidate {d.candidate.version}</span>
              {isAdmin && <Button size="sm" loading={busy === "ap"} onClick={() => post("ap", "/api/dashboard/learning/regimes/approve", `Approve ${d.candidate!.version}?`)}>Approve</Button>}
            </div>
            <MapTable m={d.candidate} />
          </div>
        )}
        {!d?.approved && !d?.candidate && <Empty>Build one with <code>skopaq learn-train --regimes</code>.</Empty>}
      </Card>
      <Card title="Model drift" icon={Waves} subtitle="Champion's training features vs the last 5 days of signals (PSI; > 0.25 is drift)">
        <ErrorBox error={drift.error} />
        {!drift.data ? null : !drift.data.available ? <Empty>{drift.data.reason}</Empty> : (
          <div className="space-y-2">
            {drift.data.alerts?.length ? <Notice tone="warning" title="Drift">{drift.data.alerts.join(", ")}: retrain or roll back.</Notice>
              : <Badge tone="ok">no drift over {drift.data.recent_signals} signals</Badge>}
            <Table head={["Feature", "PSI"]}>
              {top.map(([k, v]) => <tr key={k}><td>{k}</td><td className={v > 0.25 ? "text-amber-300" : ""}>{v.toFixed(3)}</td></tr>)}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
