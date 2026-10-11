"use client";

import { Bot, RotateCcw } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, pnlClass, Table } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Stats = { trades: number; net: number; profit_factor: number | null; win_rate?: number | null; max_drawdown?: number; charges?: number } | null | undefined;
interface Policy {
  version: string; state: string; created_at?: string; algo?: string; model?: Stats; baseline?: Stats;
  test_sessions?: number; train_sessions?: number; exits?: Record<string, number>; actions?: Record<string, number>;
  gate_failures: string[]; shadow_since?: string | null; approved_by?: string | null;
}
export interface RLStatus {
  enabled: boolean; in_scalp_strategies: boolean; allow_live: boolean; paper_use_shadow: boolean; torch: boolean;
  algos: string[]; algo: string; paper_policy: string | null; live_policy: string | null;
  champion: string | null; shadow: string | null; shadow_days: number; versions: Policy[];
}

const net = (s: Stats) => (s ? <span className={pnlClass(s.net)}>{inr(s.net, 0)} <span className="text-gray-500">· {s.trades} tr</span></span> : "—");
const tone = (state: string) => (state === "champion" ? "ok" : state === "shadow" ? "info" : state === "retired" ? "neutral" : "warning");

/** Where an RL policy may trade: paper (shadow or champion) and live (approved champion + .env switch). */
export function rlTradingMode(d: RLStatus): { paper: string; live: string } {
  if (!d.enabled || !d.in_scalp_strategies) return { paper: "off", live: "off" };
  return {
    paper: d.paper_policy ? `on · ${d.paper_policy === d.champion ? "champion" : "shadow"}` : "no policy",
    live: !d.allow_live ? "off (SKOPAQ_RL_ALLOW_LIVE)" : d.live_policy ? "on · champion" : "needs an approved champion",
  };
}

/** PPO / A2C / DQN trading policies: out-of-sample results, shadow → champion, rollback. */
export default function RLAgents() {
  const { isAdmin } = useAuth();
  const r = useApi<RLStatus>("/api/dashboard/learning/rl", 120000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const post = async (key: string, path: string, what: string) => {
    const reason = prompt(`${what}\n\nReason (logged):`);
    if (!reason || reason.trim().length < 5) return;
    setBusy(key);
    setMsg(null);
    try {
      await api(path, { method: "POST", json: { reason: reason.trim() } });
      r.reload();
    } catch (e: any) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const d = r.data;
  const mode = d ? rlTradingMode(d) : null;
  return (
    <Card title="Reinforcement learning (PPO · A2C · DQN)" icon={Bot}
      subtitle="Policies learn in a replay of the scalper's own rules, net of charges, and are tested on later unseen days. A policy trades as the rl_policy scalp strategy behind every gate: shadow on paper, live only as an admin-approved champion."
      right={d && <Badge tone={d.enabled ? "ok" : "neutral"}>{d.enabled ? `enabled · ${d.algo.toUpperCase()}` : "off"}</Badge>}>
      <ErrorBox error={r.error || msg} />
      {!d || !mode ? null : (
        <div className="space-y-4">
          <KV cols={3} items={[
            ["Paper trading", mode.paper], ["Live trading", mode.live], ["PyTorch", d.torch ? "installed" : "missing"],
            ["Champion", d.champion || "none"], ["Shadow", d.shadow || "none"], ["Shadow period", `${d.shadow_days} days`],
          ]} />
          {d.enabled && !d.in_scalp_strategies && (
            <Notice tone="warning" title="Not trading">Add <code>rl_policy</code> to SKOPAQ_SCALP_STRATEGIES to let the policy propose scalps.</Notice>
          )}
          {!d.versions.length ? <Empty>No RL policy yet. Run <code>skopaq rl-train --algo ppo</code>.</Empty> : (
            <Table head={["Policy", "Unseen days", "Buy & hold", "Exits", ""]}>
              {d.versions.map((p) => (
                <tr key={p.version} className="align-top">
                  <td><div className="flex items-center gap-2"><Badge tone={tone(p.state)}>{p.state}</Badge><span className="text-gray-100">{p.version}</span></div>
                    <div className="text-[11px] text-gray-500">{(p.algo || "").toUpperCase()} · {p.train_sessions} train / {p.test_sessions} test sessions{p.created_at ? ` · ${when(p.created_at)}` : ""}</div>
                    {p.approved_by && <div className="text-[11px] text-gray-500">approved by {p.approved_by}</div>}
                    {p.gate_failures.length > 0 && <div className="text-[11px] text-amber-300">{p.gate_failures.slice(0, 3).join(" · ")}</div>}</td>
                  <td className="text-xs">{net(p.model)}
                    {p.model && <div className="text-gray-500">PF {p.model.profit_factor ?? "—"} · DD {inr(p.model.max_drawdown ?? 0, 0)} · charges {inr(p.model.charges ?? 0, 0)}</div>}</td>
                  <td className="text-xs">{net(p.baseline)}</td>
                  <td className="text-xs text-gray-400">{Object.entries(p.exits || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "—"}</td>
                  <td>{isAdmin && p.state === "shadow" && (
                    <Button size="sm" loading={busy === p.version}
                      onClick={() => post(p.version, `/api/dashboard/learning/rl/${p.version}/promote`, `Promote ${p.version} to RL champion? It needs ${d.shadow_days} days in shadow; live trading also needs SKOPAQ_RL_ALLOW_LIVE.`)}>Promote</Button>
                  )}</td>
                </tr>
              ))}
            </Table>
          )}
          {isAdmin && d.champion && (
            <Button size="sm" variant="ghost" icon={RotateCcw} loading={busy === "rb"}
              onClick={() => post("rb", "/api/dashboard/learning/rl/rollback", "Roll back the RL champion to the previous one?")}>Roll back champion</Button>
          )}
        </div>
      )}
    </Card>
  );
}
