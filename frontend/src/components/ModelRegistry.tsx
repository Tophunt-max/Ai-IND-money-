"use client";

import { GitBranch, RotateCcw } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, pnlClass, Skeleton, Table } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Stats { trades: number; net: number; expectancy: number | null; expectancy_ci90: [number, number] | null; profit_factor: number | null; max_drawdown: number; win_rate: number | null }

interface Version {
  version: string;
  strategy_id: string;
  state: "candidate" | "shadow" | "champion" | "retired";
  created_at: string;
  shadow_since?: string | null;
  approved_by?: string;
  gate_failures: string[];
  dataset: { labelled?: number; rows?: number; features?: number; train?: number; validation?: number; test?: number; first?: string; last?: string; fingerprint?: string };
  report: { samples?: number; threshold?: number; baseline?: Stats; model?: Stats; brier?: number | null; brier_base_rate?: number | null;
            drift_test_vs_train?: { alerts: string[] }; walk_forward?: { train: number; test: number; baseline_net: number; model_net: number | null }[] };
}

interface Registry { champion: Version | null; shadow: Version | null; versions: Version[]; history: { ts: string; event: string; version?: string; reason?: string; by?: string; approved_by?: string }[]; gates: Record<string, number | boolean> }

const stateTone = (s: string) => (s === "champion" ? "ok" : s === "shadow" ? "info" : s === "retired" ? "neutral" : "warning");

function StatsLine({ s }: { s?: Stats }) {
  if (!s) return <span className="text-gray-500">—</span>;
  return (
    <span>
      <span className={pnlClass(s.net)}>{inr(s.net, 0)}</span>
      <span className="text-gray-500"> · {s.trades} tr · PF {s.profit_factor ?? "—"}</span>
    </span>
  );
}

export default function ModelRegistry() {
  const { isAdmin } = useAuth();
  const reg = useApi<Registry>("/api/dashboard/learning/models", 60000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const post = async (key: string, path: string, what: string) => {
    const reason = prompt(`${what}\n\nReason (logged and sent to Telegram):`);
    if (!reason || reason.trim().length < 5) return;
    setBusy(key);
    setMsg(null);
    try {
      await api(path, { method: "POST", json: { reason: reason.trim() } });
      setMsg({ tone: "ok", text: "Done" });
      reg.reload();
    } catch (e: any) {
      setMsg({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const d = reg.data;
  return (
    <Card title="Models: champion vs challenger" icon={GitBranch}
      subtitle="Trained on point-in-time features, judged on unseen periods after costs. Gates → shadow (recorded, never deciding) → champion after paper days and an admin's approval."
      right={isAdmin && d?.champion && <Button size="sm" variant="ghost" icon={RotateCcw} loading={busy === "rollback"}
        onClick={() => post("rollback", "/api/dashboard/learning/rollback", "Roll back to the previous champion?")}>Roll back</Button>}>
      <ErrorBox error={reg.error} />
      {msg && <Notice tone={msg.tone} className="mb-3">{msg.text}</Notice>}
      {reg.loading && !d ? <Skeleton className="h-24" /> : !d?.versions.length ? (
        <Empty title="No trained models">Run <code>skopaq learn-train --symbols RELIANCE,TCS --days 20</code> on the server. Until a model passes every gate, the rule strategies trade alone.</Empty>
      ) : (
        <div className="space-y-4">
          <KV cols={2} items={[
            ["Champion", d.champion ? `${d.champion.version} (by ${d.champion.approved_by ?? "?"})` : "none: rules only"],
            ["Shadow", d.shadow ? `${d.shadow.version} since ${when(d.shadow.shadow_since)}` : "none"],
            ["Min test samples", String(d.gates.min_test_samples)],
            ["Shadow days before promotion", String(d.gates.shadow_days)],
          ]} />
          <Table head={["Version", "State", "Data", "Baseline (test)", "Model gate (test)", "Brier vs base", ""]}>
            {[...d.versions].reverse().map((v) => (
              <tr key={v.version} className="align-top">
                <td><div className="text-gray-100">{v.version}</div><div className="text-[11px] text-gray-500">{when(v.created_at)}</div></td>
                <td>
                  <Badge tone={stateTone(v.state)}>{v.state}</Badge>
                  {v.gate_failures.length > 0 && <ul className="mt-1 space-y-0.5 text-left text-[11px] text-amber-300/90">{v.gate_failures.slice(0, 4).map((g, i) => <li key={i}>{g}</li>)}</ul>}
                  {!!v.report.drift_test_vs_train?.alerts.length && <div className="mt-1 text-[11px] text-rose-300">drift: {v.report.drift_test_vs_train.alerts.join(", ")}</div>}
                </td>
                <td className="text-xs">{v.dataset.labelled ?? v.dataset.rows ?? "—"} rows<div className="text-gray-500">{v.dataset.train != null ? `${v.dataset.train}/${v.dataset.validation}/${v.dataset.test}` : v.dataset.features != null ? `${v.dataset.features} features` : ""}</div></td>
                <td className="text-xs"><StatsLine s={v.report.baseline} /></td>
                <td className="text-xs"><StatsLine s={v.report.model} />
                  {v.report.model?.expectancy_ci90 && <div className="text-gray-500">E 90% CI {v.report.model.expectancy_ci90.map((x) => x.toFixed(0)).join("…")}</div>}</td>
                <td className="text-xs">{v.report.brier?.toFixed(3) ?? "—"} / {v.report.brier_base_rate?.toFixed(3) ?? "—"}</td>
                <td>{isAdmin && v.state === "shadow" && <Button size="sm" loading={busy === v.version}
                  onClick={() => post(v.version, `/api/dashboard/learning/models/${v.version}/promote`, `Promote ${v.version} to champion?`)}>Promote</Button>}</td>
              </tr>
            ))}
          </Table>
          {d.history.length > 0 && (
            <div className="text-[11px] text-gray-500">
              {d.history.slice(0, 6).map((h, i) => <div key={i}>{when(h.ts)} · {h.event} {h.version ?? ""} {h.reason ? `— ${h.reason}` : ""}</div>)}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
