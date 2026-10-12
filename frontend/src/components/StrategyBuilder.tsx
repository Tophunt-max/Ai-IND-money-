"use client";

import { Puzzle, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, ErrorBox, Notice, Table } from "@/components/ui";
import { api, ApiError, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

export interface Target { type: string; horizon: number; min_move_pct?: number }
export interface Spec {
  name: string; description?: string; timeframes: number[]; periods: number[]; shifted: number;
  features: string[]; correlated: string[]; targets: Target[]; entry: { type: string; min?: number };
}
interface Saved { spec: Spec; saved_by: string; saved_at: string; revision: number }
interface Strategies {
  in_use: { kind: string; name?: string; set_by?: string; at?: string };
  env_strategy: string; continual: { adaptive: boolean; rl: boolean }; specs: Saved[];
  catalog: { features: Record<string, { kind: string; about: string }>; targets: Record<string, { task: string; about: string }>;
             entry_types: string[]; timeframes: number[] };
}

export const EMPTY: Spec = {
  name: "", description: "", timeframes: [60, 300], periods: [10, 20], shifted: 2,
  features: ["rsi", "roc", "atr_pct", "rel_vol", "ema_gap", "minute_of_session"], correlated: [],
  targets: [{ type: "win", horizon: 15, min_move_pct: 0.15 }], entry: { type: "auto" },
};

const nums = (s: string) => s.split(",").map((x) => Number(x.trim())).filter((x) => Number.isFinite(x) && x > 0);
const tf = (s: number) => (s >= 3600 ? `${s / 3600}h` : `${s / 60}m`);

/** Client-side checks that mirror the server's (the server is the authority). */
export function specProblems(s: Spec, targetTask: (t: string) => string | undefined): string[] {
  const out: string[] = [];
  if (!/^[a-z][a-z0-9_]{2,40}$/.test(s.name) || s.name === "default") out.push("name: lower-case letters, digits, _ (3–41)");
  if (s.timeframes[0] !== 60) out.push("the first timeframe must be 1m");
  if (!s.features.length) out.push("pick at least one feature");
  if (!s.periods.length || s.periods.some((p) => p < 2 || p > 200)) out.push("periods: 2–200");
  const tasks = new Set(s.targets.map((t) => targetTask(t.type)));
  if (tasks.size > 1) out.push("targets: all win, or all regression targets");
  if (s.entry.type === "probability" && !tasks.has("classifier")) out.push("probability entry needs a win target");
  if ((s.entry.type === "zscore" || s.entry.type === "value") && !tasks.has("regressor")) out.push(`${s.entry.type} entry needs a regression target`);
  return out;
}

/** Build, save and select your own adaptive strategy — no code, validated on the server. */
export default function StrategyBuilder() {
  const { isAdmin } = useAuth();
  const r = useApi<Strategies>("/api/dashboard/learning/strategies", 120000);
  const [spec, setSpec] = useState<Spec>(EMPTY);
  const [periods, setPeriods] = useState("10, 20");
  const [corr, setCorr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => { setPeriods(spec.periods.join(", ")); setCorr(spec.correlated.join(", ")); }, [spec.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const d = r.data;
  const task = (t: string) => d?.catalog.targets[t]?.task;
  const current: Spec = { ...spec, periods: nums(periods), correlated: corr.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) };
  const problems = d ? specProblems(current, task) : [];

  const call = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key); setMsg(null); setOk(null);
    try { await fn(); setOk(done); r.reload(); } catch (e: any) { setMsg(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(null); }
  };
  const activate = (choice: string, label: string) => {
    const reason = prompt(`Train with ${label} from now on?\n\nReason (logged):`);
    if (!reason || reason.trim().length < 5) return;
    call(`a-${choice}`, () => api("/api/dashboard/learning/strategies/activate", { method: "POST", json: { choice, reason: reason.trim() } }), `${label} selected for training`);
  };
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const inUse = d?.in_use.kind === "spec" ? d.in_use.name : d?.in_use.kind === "env" ? `.env: ${d.env_strategy}` : "built-in default";

  return (
    <Card title="Your strategy" icon={Puzzle}
      subtitle="Pick the features, timeframes, targets and entry rule the adaptive models learn — no code. The server checks every strategy for look-ahead before saving. A model trained with it is still only a candidate behind the gates."
      right={d && <Badge tone="info">training: {inUse}</Badge>}>
      <ErrorBox error={r.error || msg} />
      {ok && <Notice tone="ok" title="Done">{ok}</Notice>}
      {!d ? null : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span>Continual learning:</span>
            <Badge tone={d.continual.adaptive ? "ok" : "neutral"}>adaptive {d.continual.adaptive ? "on" : "off"}</Badge>
            <Badge tone={d.continual.rl ? "ok" : "neutral"}>RL {d.continual.rl ? "on" : "off"}</Badge>
            <span className="text-gray-500">(SKOPAQ_ADAPTIVE_CONTINUAL_LEARNING / SKOPAQ_RL_CONTINUAL_LEARNING, or --continual)</span>
          </div>
          <Table head={["Strategy", "Features", "Targets", "Entry", ""]}>
            <tr><td className="text-gray-100">built-in default</td><td className="text-xs text-gray-500">FreqAI-like set</td><td className="text-xs">win</td><td className="text-xs">auto</td>
              <td>{isAdmin && d.in_use.kind !== "default" && <Button size="sm" variant="ghost" loading={busy === "a-default"} onClick={() => activate("default", "the built-in default")}>Use</Button>}</td></tr>
            {d.env_strategy && <tr><td className="text-gray-100">.env: {d.env_strategy}</td><td className="text-xs text-gray-500">your Python class</td><td /><td />
              <td>{isAdmin && d.in_use.kind !== "env" && <Button size="sm" variant="ghost" loading={busy === "a-env"} onClick={() => activate("env", d.env_strategy)}>Use</Button>}</td></tr>}
            {d.specs.map((s) => (
              <tr key={s.spec.name} className="align-top">
                <td><div className="flex items-center gap-2"><span className="text-gray-100">{s.spec.name}</span>{d.in_use.kind === "spec" && d.in_use.name === s.spec.name && <Badge tone="ok">in use</Badge>}</div>
                  <div className="text-[11px] text-gray-500">rev {s.revision} · {s.saved_by} · {when(s.saved_at)}</div></td>
                <td className="text-xs">{s.spec.features.join(", ")}<div className="text-gray-500">{s.spec.timeframes.map(tf).join("/")} · periods {s.spec.periods.join(",")}{s.spec.correlated.length ? ` · + ${s.spec.correlated.join(",")}` : ""}</div></td>
                <td className="text-xs">{s.spec.targets.map((t) => `${t.type} ${t.horizon}`).join(", ")}</td>
                <td className="text-xs">{s.spec.entry.type}{s.spec.entry.min != null ? ` ≥ ${s.spec.entry.min}` : ""}</td>
                <td className="space-x-1 whitespace-nowrap">
                  <Button size="sm" variant="ghost" onClick={() => setSpec(s.spec)}>Edit</Button>
                  {isAdmin && !(d.in_use.kind === "spec" && d.in_use.name === s.spec.name) && <>
                    <Button size="sm" loading={busy === `a-${s.spec.name}`} onClick={() => activate(s.spec.name, s.spec.name)}>Use</Button>
                    <Button size="sm" variant="ghost" icon={Trash2} loading={busy === `d-${s.spec.name}`}
                      onClick={() => confirm(`Delete ${s.spec.name}?`) && call(`d-${s.spec.name}`, () => api(`/api/dashboard/learning/strategies/${s.spec.name}`, { method: "DELETE" }), `${s.spec.name} deleted`)}>Delete</Button>
                  </>}
                </td>
              </tr>
            ))}
          </Table>

          {isAdmin && (
            <div className="space-y-3 border-t border-white/6 pt-4 text-xs">
              <div className="text-sm font-medium text-gray-200">{d.specs.some((s) => s.spec.name === spec.name) ? `Edit ${spec.name}` : "New strategy"}</div>
              <div className="grid gap-3 sm:grid-cols-3">
                <label className="space-y-1"><span className="text-gray-400">Name</span>
                  <input aria-label="name" className="w-full rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={spec.name} onChange={(e) => setSpec({ ...spec, name: e.target.value.toLowerCase() })} placeholder="my_momentum" /></label>
                <label className="space-y-1"><span className="text-gray-400">Periods</span>
                  <input aria-label="periods" className="w-full rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={periods} onChange={(e) => setPeriods(e.target.value)} /></label>
                <label className="space-y-1"><span className="text-gray-400">Correlated symbols</span>
                  <input aria-label="correlated" className="w-full rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={corr} onChange={(e) => setCorr(e.target.value)} placeholder="HDFCBANK, ICICIBANK" /></label>
              </div>
              <div className="flex flex-wrap items-center gap-2"><span className="text-gray-400">Timeframes</span>
                {d.catalog.timeframes.map((t) => (
                  <label key={t} className="flex items-center gap-1"><input type="checkbox" disabled={t === 60} checked={spec.timeframes.includes(t)}
                    onChange={() => setSpec({ ...spec, timeframes: toggle(spec.timeframes, t).sort((a, b) => a - b) })} />{tf(t)}</label>
                ))}
                <span className="ml-3 text-gray-400">Shifted copies</span>
                <input aria-label="shifted" type="number" min={0} max={5} className="w-14 rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={spec.shifted} onChange={(e) => setSpec({ ...spec, shifted: Number(e.target.value) })} />
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {Object.entries(d.catalog.features).map(([f, info]) => (
                  <label key={f} title={info.about} className="flex items-center gap-1"><input type="checkbox" checked={spec.features.includes(f)} onChange={() => setSpec({ ...spec, features: toggle(spec.features, f) })} />
                    {f}<span className="text-gray-600">{info.kind === "period" ? "·p" : ""}</span></label>
                ))}
              </div>
              {spec.targets.map((t, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2"><span className="text-gray-400">Target {i + 1}</span>
                  <select aria-label={`target-${i}`} className="rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={t.type}
                    onChange={(e) => setSpec({ ...spec, targets: spec.targets.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)) })}>
                    {Object.keys(d.catalog.targets).map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                  <span>horizon</span><input type="number" min={1} max={120} className="w-16 rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={t.horizon}
                    onChange={(e) => setSpec({ ...spec, targets: spec.targets.map((x, j) => (j === i ? { ...x, horizon: Number(e.target.value) } : x)) })} />
                  {t.type === "win" && <><span>min move %</span><input type="number" step={0.01} min={0} className="w-20 rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={t.min_move_pct ?? 0.12}
                    onChange={(e) => setSpec({ ...spec, targets: spec.targets.map((x, j) => (j === i ? { ...x, min_move_pct: Number(e.target.value) } : x)) })} /></>}
                  {spec.targets.length > 1 && <Button size="sm" variant="ghost" onClick={() => setSpec({ ...spec, targets: spec.targets.filter((_, j) => j !== i) })}>remove</Button>}
                  <span className="text-gray-600">{d.catalog.targets[t.type]?.about}</span>
                </div>
              ))}
              {spec.targets.length < 4 && <Button size="sm" variant="ghost" onClick={() => setSpec({ ...spec, targets: [...spec.targets, { ...spec.targets[0], horizon: spec.targets[0].horizon + 5 }] })}>+ target</Button>}
              <div className="flex flex-wrap items-center gap-2"><span className="text-gray-400">Entry rule</span>
                <select aria-label="entry" className="rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={spec.entry.type}
                  onChange={(e) => setSpec({ ...spec, entry: e.target.value === "auto" ? { type: "auto" } : { type: e.target.value, min: spec.entry.min ?? (e.target.value === "probability" ? 0.6 : 1) } })}>
                  {d.catalog.entry_types.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
                {spec.entry.type !== "auto" && <><span>min</span><input type="number" step={0.01} className="w-20 rounded-sm bg-white/5 px-2 py-1 text-gray-100" value={spec.entry.min ?? 0}
                  onChange={(e) => setSpec({ ...spec, entry: { ...spec.entry, min: Number(e.target.value) } })} /></>}
                <span className="text-gray-600">auto = the threshold validated during training</span>
              </div>
              {problems.length > 0 && <div className="text-amber-300">{problems.join(" · ")}</div>}
              <div className="flex gap-2">
                <Button size="sm" disabled={problems.length > 0} loading={busy === "save"}
                  onClick={() => call("save", () => api("/api/dashboard/learning/strategies", { method: "POST", json: { spec: current } }), `${current.name} saved (look-ahead check passed)`)}>Save</Button>
                <Button size="sm" variant="ghost" onClick={() => setSpec(EMPTY)}>New</Button>
              </div>
              <div className="text-gray-500">After selecting it: <code>skopaq adaptive-train</code> (add <code>--continual</code> to continue the last model), or the next automatic retrain.</div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
