"use client";

import { GraduationCap, RotateCcw, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, pnlClass, Table } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi, useJob } from "@/lib/hooks";

export type Stats = { trades: number; net: number; profit_factor: number | null; win_rate?: number | null; max_drawdown?: number } | null | undefined;
export interface SwingVersion {
  version: string; state: string; created_at?: string; gate_failures?: string[] | null;
  shadow_since?: string | null; approved_by?: string | null; model_kind?: string | null;
  model?: Stats; baseline?: Stats; threshold?: number | null; brier?: number | null; brier_base_rate?: number | null;
  setups?: number | null; test_from?: string | null; test_to?: string | null;
  by_setup?: Record<string, { setups: number; taken: number; net_all: number; net_taken: number }> | null;
  importance?: { feature: string; brier_increase: number; share: number }[];
  base_rate?: number | null;
}
export interface SwingStatus {
  mode: string; champion: string | null; shadow: string | null; active: string | null; rules_only: boolean;
  shadow_days: number; versions: SwingVersion[];
  config: { symbols: string; model_kind: string; target_rr: number; train_days: number; max_trades: number; min_net_rr: number };
}

const net = (s: Stats) => (s ? <span className={pnlClass(s.net)}>{inr(s.net, 0)} <span className="text-gray-500">· {s.trades} tr</span></span> : "—");
const tone = (state: string) => (state === "champion" ? "ok" : state === "shadow" ? "info" : state === "retired" ? "neutral" : "warning");

/** What the swing book trades with: paper uses the champion, else the shadow; live only an approved champion. */
export function swingTradingMode(d: SwingStatus): { paper: string; live: string } {
  const paper = d.champion ? "champion" : d.shadow ? "shadow" : d.rules_only ? "rules only (no model)" : "off — no model";
  return { paper, live: d.champion ? "champion" : "off — needs an approved champion" };
}

/** The Adaptive AI swing model: daily setups ranked by a model trained on their net outcomes. */
export default function SwingModel() {
  const { isAdmin } = useAuth();
  const r = useApi<SwingStatus>("/api/dashboard/swing", 120000);
  const train = useJob();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const { reload } = r;

  useEffect(() => {
    if (train.job?.status === "done") reload();
  }, [train.job?.status, reload]);

  const post = async (key: string, path: string, what: string) => {
    const reason = prompt(`${what}\n\nReason (logged):`);
    if (!reason || reason.trim().length < 5) return;
    setBusy(key);
    setMsg(null);
    try {
      await api(path, { method: "POST", json: { reason: reason.trim() } });
      reload();
    } catch (e: any) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const d = r.data;
  const mode = d ? swingTradingMode(d) : null;
  const res = train.job?.status === "done" ? train.job.result : null;
  return (
    <Card title="Swing model (Adaptive AI)" icon={TrendingUp}
      subtitle="Daily setups (trend pullback, breakout, oversold bounce) scored by a model trained on their outcomes net of delivery charges, tested on later unseen dates. Every entry still passes the signal gate, the position sizer and the safety checker."
      right={d && <Badge tone={d.active ? "ok" : "warning"}>{d.active ? `active · ${d.champion === d.active ? "champion" : "shadow"}` : "no model"}</Badge>}>
      <ErrorBox error={r.error || msg || train.error || (train.job?.status === "failed" ? train.job.error : null)} />
      {!d || !mode ? null : (
        <div className="space-y-4">
          <KV cols={3} items={[
            ["Paper trading", mode.paper], ["Live trading", mode.live], ["Shadow period", `${d.shadow_days} days`],
            ["Champion", d.champion || "none"], ["Shadow", d.shadow || "none"], ["Universe", d.config.symbols],
            ["Model", d.config.model_kind], ["Target", `${d.config.target_rr} R · out by 15:20`], ["Min net R:R", d.config.min_net_rr],
          ]} />
          {res && (
            <Notice tone={res.state === "shadow" ? "ok" : "warning"} title={`Trained ${res.version}: ${res.state}`}>
              {res.report?.setups ?? 0} historical setups · test net {inr(res.report?.model?.net ?? 0, 0)} vs every setup {inr(res.report?.baseline?.net ?? 0, 0)}
              {(res.gate_failures || []).length > 0 && <> · gates: {res.gate_failures.slice(0, 3).join(" · ")}</>}
              {(res.missing || []).length > 0 && <> · no history for {res.missing.length} symbols</>}
            </Notice>
          )}
          {train.running && <Notice tone="info">Training on daily history (a few minutes, no orders)...</Notice>}
          {!d.versions.length ? (
            <Empty icon={GraduationCap}>No swing model yet. Train one here or with <code>skopaq swing-train</code>; until then the swing book makes no entries.</Empty>
          ) : (
            <Table head={["Model", "Unseen dates (taken)", "Every setup", "By setup", ""]}>
              {d.versions.map((v) => (
                <tr key={v.version} className="align-top">
                  <td><div className="flex items-center gap-2"><Badge tone={tone(v.state)}>{v.state}</Badge><span className="text-gray-100">{v.version}</span></div>
                    <div className="text-[11px] text-gray-500">{v.model_kind} · {v.setups ?? 0} setups · P {">="} {v.threshold ?? "—"}{v.test_from ? ` · test ${v.test_from} to ${v.test_to}` : ""}{v.created_at ? ` · ${when(v.created_at)}` : ""}</div>
                    {v.approved_by && <div className="text-[11px] text-gray-500">approved by {v.approved_by}</div>}
                    {(v.gate_failures || []).length > 0 && <div className="text-[11px] text-amber-300">{v.gate_failures!.slice(0, 3).join(" · ")}</div>}</td>
                  <td className="text-xs">{net(v.model)}
                    {v.model && <div className="text-gray-500">PF {v.model.profit_factor ?? "—"} · win {v.model.win_rate == null ? "—" : `${(v.model.win_rate * 100).toFixed(0)}%`}</div>}</td>
                  <td className="text-xs">{net(v.baseline)}</td>
                  <td className="text-xs text-gray-400">{Object.entries(v.by_setup || {}).map(([k, s]) => `${k.replace(/_/g, " ")} ${s.taken}/${s.setups}`).join(", ") || "—"}</td>
                  <td>{isAdmin && v.state === "shadow" && (
                    <Button size="sm" loading={busy === v.version}
                      onClick={() => post(v.version, `/api/dashboard/swing/${v.version}/promote`, `Promote ${v.version} to swing champion? It needs ${d.shadow_days} days in shadow; live sessions then trade with it.`)}>Promote</Button>
                  )}</td>
                </tr>
              ))}
            </Table>
          )}
          {isAdmin && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" icon={GraduationCap} loading={train.running} onClick={() => {
                if (confirm(`Train a new swing model on ${d.config.train_days} days of daily history? It registers as a candidate or shadow; nothing trades live until you promote it.`)) train.start({ kind: "swing_train" });
              }}>Train swing model</Button>
              {d.champion && (
                <Button size="sm" variant="ghost" icon={RotateCcw} loading={busy === "rb"}
                  onClick={() => post("rb", "/api/dashboard/swing/rollback", "Roll back the swing champion to the previous one?")}>Roll back champion</Button>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
