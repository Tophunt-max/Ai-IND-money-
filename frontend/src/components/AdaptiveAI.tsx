"use client";

import { BrainCircuit, RotateCcw } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, pnlClass, Table } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Stats = { trades: number; net: number; profit_factor: number | null } | undefined;
interface Block { from: string; to: string; baseline_net: number; model_net: number; model_di_net: number; outlier_rate: number }
interface Version {
  version: string; state: string; created_at: string; gate_failures: string[];
  dataset: { rows?: number; features?: number; symbols?: string[]; correlated?: string[]; target?: string };
  report: { model_kind?: string; outlier_rate?: number; baseline?: Stats; model?: Stats; model_di?: Stats;
            rolling?: { retrains: number; blocks?: Block[]; baseline?: Stats; model?: Stats; model_di?: Stats } };
}
interface Params { version: string; best_params: Record<string, number>; holdout_current: { net: number; trades: number };
  holdout_best: { net: number; trades: number }; better_on_holdout: boolean; approved_by?: string; created_at: string }
interface BundleInfo {
  version: string; ok: boolean; error?: string; files?: number; bytes?: number; created_at?: string;
  strategy?: string; model?: string; task?: string; targets?: string[]; features?: number;
  rule?: { type: string; threshold?: number; k?: number };
  pipeline?: { kept: number; input_columns: number; pca_components: number; svm: boolean; di: boolean; dbscan: boolean; removed: Record<string, number> };
  importance?: [string, number][]; continued_from?: string | null;
}
interface Adaptive {
  bundles?: BundleInfo[]; model_kinds?: Record<string, boolean>; strategy?: string;
  enabled: boolean; retrain_hours: number; config: { model_kind: string; target: string; horizon_bars: number; train_days: number; di_threshold: number; features: { correlated: string[]; timeframes: number[] } };
  state: { ok?: boolean; finished_at?: string; version?: string; state?: string; error?: string };
  versions: Version[]; params: { approved: Params | null; candidate: Params | null };
}

const net = (s: Stats) => (s ? <span className={pnlClass(s.net)}>{inr(s.net, 0)} <span className="text-gray-500">· {s.trades} tr</span></span> : "—");

/** FreqAI-style adaptive models, their rolling-retrain backtests, and hyperopt parameters. */
export default function AdaptiveAI() {
  const { isAdmin } = useAuth();
  const a = useApi<Adaptive>("/api/dashboard/learning/adaptive", 120000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const post = async (key: string, path: string, what: string) => {
    const reason = prompt(`${what}\n\nReason (logged):`);
    if (!reason || reason.trim().length < 5) return;
    setBusy(key);
    setMsg(null);
    try {
      await api(path, { method: "POST", json: { reason: reason.trim() } });
      a.reload();
    } catch (e: any) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const d = a.data;
  const latest = d?.versions?.[0];
  const cand = d?.params.candidate;
  return (
    <Card title="Adaptive AI (FreqAI-style)" icon={BrainCircuit}
      subtitle="Expanded multi-timeframe features, outlier detection (dissimilarity index), periodic retraining and rolling backtests. A retrained model is only a candidate / shadow: an admin promotes it."
      right={d && <Badge tone={d.enabled ? "ok" : "neutral"}>{d.enabled ? `auto-retrain every ${d.retrain_hours} h` : "auto-retrain off"}</Badge>}>
      <ErrorBox error={a.error || msg} />
      {!d ? null : (
        <div className="space-y-4">
          <KV cols={3} items={[
            ["Model", d.config.model_kind], ["Target", `${d.config.target} · ${d.config.horizon_bars} bars`],
            ["Window", `${d.config.train_days} days`], ["Timeframes", d.config.features.timeframes.map((t) => `${t / 60}m`).join(", ")],
            ["Correlated", d.config.features.correlated.join(", ") || "none"], ["DI threshold", String(d.config.di_threshold)],
          ]} />
          {d.state.finished_at && (
            <Notice tone={d.state.ok ? "ok" : "warning"} title={d.state.ok ? "Last retrain" : "Last retrain failed"}>
              {when(d.state.finished_at)}: {d.state.ok ? `${d.state.version} → ${d.state.state}` : d.state.error}
            </Notice>
          )}
          {!latest ? <Empty>No adaptive model yet. Run <code>skopaq adaptive-train</code> or turn on SKOPAQ_ADAPTIVE_AUTO_RETRAIN.</Empty> : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                <Badge tone={latest.state === "champion" ? "ok" : latest.state === "shadow" ? "info" : "warning"}>{latest.state}</Badge>
                <span>{latest.version} · {latest.report.model_kind} · {latest.dataset.features} features · {latest.dataset.rows} rows · outliers {((latest.report.outlier_rate ?? 0) * 100).toFixed(0)}%</span>
              </div>
              <Table head={["Out of sample", "Every bar / setup", "Model", "Model + outlier gate"]}>
                <tr><td>Test period</td><td>{net(latest.report.baseline)}</td><td>{net(latest.report.model)}</td><td>{net(latest.report.model_di)}</td></tr>
                <tr><td>Rolling retrain ({latest.report.rolling?.retrains ?? 0}×)</td><td>{net(latest.report.rolling?.baseline)}</td><td>{net(latest.report.rolling?.model)}</td><td>{net(latest.report.rolling?.model_di)}</td></tr>
              </Table>
              {latest.gate_failures.length > 0 && <div className="text-[11px] text-amber-300">{latest.gate_failures.slice(0, 4).join(" · ")}</div>}
            </>
          )}
          <div className="border-t border-white/6 pt-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium text-gray-200">Model bundles <span className="text-xs text-gray-500">· strategy: {d.strategy}</span></span>
              <div className="flex flex-wrap gap-1">
                {Object.entries(d.model_kinds || {}).map(([k, ok]) => <Badge key={k} tone={ok ? "ok" : "neutral"}>{k}</Badge>)}
              </div>
            </div>
            {!d.bundles?.length ? <div className="text-xs text-gray-500">No bundles yet.</div> : (
              <Table head={["Bundle", "Model", "Pipeline", "Entry rule", "Integrity"]}>
                {d.bundles.map((b) => (
                  <tr key={b.version} className="align-top">
                    <td><div className="text-gray-100">{b.version}</div>
                      <div className="text-[11px] text-gray-500">{b.strategy} · {b.features} features · {(b.targets || []).join(", ")}</div>
                      {b.importance?.length ? <div className="text-[11px] text-gray-500">top: {b.importance.slice(0, 3).map(([n]) => n).join(", ")}</div> : null}
                      {b.continued_from && <div className="text-[11px] text-sky-300">continued from {b.continued_from}</div>}</td>
                    <td className="text-xs">{b.model}<div className="text-gray-500">{b.task}</div></td>
                    <td className="text-xs">{b.pipeline ? <>
                      {b.pipeline.kept}/{b.pipeline.input_columns} cols{b.pipeline.pca_components ? ` → ${b.pipeline.pca_components} PCA` : ""}
                      <div className="text-gray-500">{["svm", "di", "dbscan"].filter((k) => (b.pipeline as any)[k]).join(" · ") || "no outlier filter"}</div>
                      {Object.keys(b.pipeline.removed || {}).length > 0 && <div className="text-gray-500">removed {Object.entries(b.pipeline.removed).map(([k, v]) => `${k} ${v}`).join(", ")}</div>}
                    </> : "—"}</td>
                    <td className="text-xs">{b.rule ? (b.rule.type === "probability" ? `P ≥ ${b.rule.threshold}` : `mean + ${b.rule.k}·std`) : "—"}</td>
                    <td className="text-xs">{b.ok ? <Badge tone="ok">verified</Badge> : <Badge tone="error">{b.error?.slice(0, 40) || "failed"}</Badge>}
                      {b.bytes != null && <div className="text-gray-500">{(b.bytes / 1024).toFixed(0)} KB · {b.files} files</div>}</td>
                  </tr>
                ))}
              </Table>
            )}
          </div>
          <div className="border-t border-white/6 pt-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-gray-200">Strategy parameters (hyperopt)</span>
              {isAdmin && d.params.approved && <Button size="sm" variant="ghost" icon={RotateCcw} loading={busy === "prb"}
                onClick={() => post("prb", "/api/dashboard/learning/params/rollback", "Roll back the strategy parameters?")}>Roll back</Button>}
            </div>
            <div className="text-xs text-gray-400">In use: {d.params.approved ? `${d.params.approved.version} (by ${d.params.approved.approved_by})` : "the configured defaults"}</div>
            {cand && cand.version !== d.params.approved?.version && (
              <div className="mt-2 space-y-1 text-xs">
                <div className="text-gray-300">Candidate {cand.version}: {Object.entries(cand.best_params).map(([k, v]) => `${k}=${typeof v === "number" ? +v.toFixed(4) : v}`).join(", ")}</div>
                <div className="text-gray-500">Holdout: current {inr(cand.holdout_current.net, 0)} ({cand.holdout_current.trades} tr) vs candidate {inr(cand.holdout_best.net, 0)} ({cand.holdout_best.trades} tr)</div>
                {cand.better_on_holdout
                  ? isAdmin && <Button size="sm" loading={busy === "pap"} onClick={() => post("pap", "/api/dashboard/learning/params/approve", `Use ${cand.version} from the next session?`)}>Approve</Button>
                  : <Badge tone="neutral">not better on the holdout: cannot be approved</Badge>}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
