"use client";

import { BarChart3, BrainCircuit, FlaskConical, Layers, Radar, Sparkles, Target } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { BarChart } from "@/components/charts";
import { IdeaCard, useLatestIdeas } from "@/components/IdeaCards";
import { swingTradingMode, type SwingStatus, type SwingVersion } from "@/components/SwingModel";
import { Badge, Button, Card, cx, Empty, ErrorBox, Gauge, KV, Notice, PageTitle, pnlClass, Progress, Segmented, Skeleton, StatCard } from "@/components/ui";
import { inr, pct, when } from "@/lib/api";
import { useApi, useJob } from "@/lib/hooks";
import { SETUP_LABEL, type SwingIdeaDict } from "@/lib/types";

const FEATURE_LABEL: Record<string, string> = {
  ret_1: "1-day return", ret_5: "5-day return", ret_20: "20-day return", ret_60: "60-day return", rsi: "RSI 14",
  atr_pct: "ATR % (volatility)", dist_ema20: "Distance to EMA 20", dist_sma50: "Distance to SMA 50",
  dist_sma200: "Distance to SMA 200", sma50_slope: "SMA 50 slope", vol_ratio: "Volume vs 20-day avg",
  range_pos20: "Position in 20-day range", close_pos: "Close in day's range", gap: "Opening gap", setup: "Setup type",
};

interface SignalReport {
  signals: { total: number; qualified: number; rejected: number; executed: number; closed: number; win_rate: number | null; net: number;
    by_strategy: Record<string, { decisions: number; qualified: number; executed: number; closed: number; wins: number; net: number; win_rate: number | null }>;
    top_rejections: [string, number][] };
}

function Importance({ items }: { items: NonNullable<SwingVersion["importance"]> }) {
  const max = Math.max(1e-9, ...items.map((i) => i.share));
  if (!items.length) return <Empty icon={BarChart3}>Train a new model to see what drives it (older models have no importance report).</Empty>;
  return (
    <ul className="space-y-2.5">
      {items.map((i) => (
        <li key={i.feature}>
          <div className="mb-1 flex justify-between text-xs"><span className="text-gray-300">{FEATURE_LABEL[i.feature] || i.feature}</span><span className="num text-gray-500">{(i.share * 100).toFixed(1)}%</span></div>
          <div className="h-2 rounded-full bg-white/[0.05]"><div className="h-full rounded-full bg-gradient-to-r from-brand-500 to-violet-500" style={{ width: `${(i.share / max) * 100}%` }} /></div>
        </li>
      ))}
    </ul>
  );
}

function ModelDetail({ v }: { v: SwingVersion }) {
  const setups = Object.entries(v.by_setup || {});
  const improvement = v.model && v.baseline ? v.model.net - v.baseline.net : null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Net on unseen dates" value={<span className={pnlClass(v.model?.net)}>{inr(v.model?.net ?? null, 0)}</span>} detail={`${v.model?.trades ?? 0} trades taken`} tone={(v.model?.net ?? 0) >= 0 ? "ok" : "error"} />
        <StatCard title="vs every setup" value={<span className={pnlClass(improvement)}>{improvement == null ? "—" : `${improvement >= 0 ? "+" : ""}${inr(improvement, 0)}`}</span>} detail={`baseline ${inr(v.baseline?.net ?? null, 0)}`} />
        <StatCard title="Win rate" value={pct(v.model?.win_rate ?? null, 0)} detail={`PF ${v.model?.profit_factor?.toFixed(2) ?? "—"}`} />
        <StatCard title="Calibration (Brier)" value={v.brier != null ? v.brier.toFixed(3) : "—"} detail={v.brier_base_rate != null ? `base rate ${v.brier_base_rate.toFixed(3)} · lower is better` : undefined}
          tone={v.brier != null && v.brier_base_rate != null ? (v.brier < v.brier_base_rate ? "ok" : "warning") : "neutral"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="What drives the model" icon={Sparkles} subtitle="Permutation importance on unseen dates: how much worse the forecasts get when a feature is shuffled">
          <Importance items={v.importance || []} />
        </Card>
        <Card title="By setup" icon={Layers} subtitle="Unseen dates: what the model took vs every setup">
          {!setups.length ? <Empty>No setups in the test period.</Empty> : (
            <div className="space-y-4">
              {setups.map(([name, s]) => (
                <div key={name}>
                  <div className="mb-1.5 flex items-center justify-between text-sm"><span className="font-semibold text-white">{SETUP_LABEL[name] || name}</span><span className="num text-xs text-gray-500">took {s.taken} of {s.setups}</span></div>
                  <Progress value={s.setups ? (s.taken / s.setups) * 100 : 0} />
                  <div className="num mt-1.5 flex justify-between text-xs"><span className="text-gray-500">taken <b className={pnlClass(s.net_taken)}>{inr(s.net_taken, 0)}</b></span><span className="text-gray-500">all <b className={pnlClass(s.net_all)}>{inr(s.net_all, 0)}</b></span></div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      {v.gate_failures && v.gate_failures.length > 0 && (
        <Notice tone="warning" title="Did not pass the gates (stays a candidate)">{v.gate_failures.join(" · ")}</Notice>
      )}
    </div>
  );
}

export default function AIPage() {
  const { isAdmin } = useAuth();
  const sw = useApi<SwingStatus>("/api/dashboard/swing", 120000);
  const rep = useApi<SignalReport>("/api/dashboard/report?days=30", 300000);
  const latest = useLatestIdeas();
  const scan = useJob();
  const [sel, setSel] = useState<string | null>(null);
  const [view, setView] = useState<"cards" | "list">("cards");
  const d = sw.data;
  const mode = d ? swingTradingMode(d) : null;
  const versions = d?.versions || [];
  const chosen = versions.find((v) => v.version === (sel || d?.active || versions[0]?.version));
  const ideas: SwingIdeaDict[] = scan.job?.status === "done" ? scan.job.result?.ideas || [] : latest.ideas;
  const swingStrategies = useMemo(() => Object.entries(rep.data?.signals.by_strategy || {}).filter(([k]) => k.startsWith("swing.")), [rep.data]);
  const best = ideas.find((i) => i.prob != null);

  return (
    <div className="space-y-5">
      <PageTitle title="AI insights" icon={BrainCircuit} subtitle="What the Adaptive AI sees today, how its swing model did on dates it never saw, and what drives it"
        right={<>
          <Link href="/learning"><Button variant="ghost" size="sm" icon={FlaskConical}>Models &amp; training</Button></Link>
          {isAdmin && <Button size="sm" icon={Radar} loading={scan.running} onClick={() => scan.start({ kind: "scan", max_candidates: 12 })}>{scan.running ? "Scanning…" : "Run swing scan"}</Button>}
        </>} />
      <ErrorBox error={sw.error || scan.error || (scan.job?.status === "failed" ? scan.job.error : null)} />

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card title="Swing model" icon={Target}>
          {!d || !mode ? <Skeleton className="h-40" /> : (
            <div className="space-y-4">
              <div className="flex flex-col items-center">
                <Gauge value={best?.prob ?? null} threshold={versions.find((v) => v.version === d.active)?.threshold ?? null} size={150} label={best ? `best idea: ${best.symbol}` : "no idea scored yet"} />
              </div>
              <KV items={[
                ["Active", d.active ? <span key="a" className="flex items-center justify-end gap-1.5">{d.active === d.champion ? <Badge tone="ok">champion</Badge> : <Badge tone="info">shadow</Badge>}</span> : <Badge tone="warning">none</Badge>],
                ["Paper trades with", mode.paper], ["Live trades with", mode.live],
                ["Universe", d.config.symbols], ["Target", `${d.config.target_rr} R · same-day exit`],
              ]} />
              {!d.active && <Notice tone="warning">No model yet: no swing entries. Train one on <Link className="link" href="/learning">AI learning</Link>.</Notice>}
            </div>
          )}
        </Card>

        <section className="min-w-0">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 className="text-[15px] font-semibold text-white">Today&apos;s ideas</h2>
              <p className="text-xs text-gray-500">{scan.running ? "Scoring daily setups…" : latest.at && scan.job?.status !== "done" ? `Last scan ${when(latest.at)}` : scan.job?.status === "done" ? "Fresh scan" : "No scan yet"}</p>
            </div>
            <Segmented size="sm" value={view} onChange={setView} options={[{ value: "cards", label: "Cards" }, { value: "list", label: "List" }]} />
          </div>
          {scan.running ? <Skeleton className="h-48" /> : ideas.length === 0 ? (
            <div className="surface"><Empty icon={Radar} title="No ideas">Run a swing scan. Without a model (and no rules-only mode) nothing qualifies.</Empty></div>
          ) : view === "cards" ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{ideas.map((i) => <IdeaCard key={i.symbol} idea={i} />)}</div>
          ) : (
            <div className="surface divide-y divide-white/[0.05]">
              {ideas.map((i) => (
                <Link key={i.symbol} href={`/market?symbol=${encodeURIComponent(i.symbol)}`} className="num flex items-center gap-3 px-4 py-3 text-sm hover:bg-white/[0.03]">
                  <span className="w-28 font-sans font-semibold text-white">{i.symbol}</span>
                  <Badge tone="info">{SETUP_LABEL[i.setup] || i.setup}</Badge>
                  <span className="ml-auto text-gray-400">{inr(i.entry)} · TG <span className="text-emerald-400">{inr(i.target)}</span> / <span className="text-rose-400">{inr(i.stop)}</span></span>
                  <span className="w-12 text-right font-bold text-emerald-400">{i.prob != null ? `${Math.round(i.prob * 100)}%` : "—"}</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>

      <Card title="Model versions" icon={BarChart3} subtitle="Net P&L on unseen test dates: for each version, what the model took (blue bar) and taking every setup (green or red bar after it). Tap a version for its details.">
        {!versions.length ? <Empty>No swing model trained yet.</Empty> : (
          <>
            <BarChart height={150} format={(v) => inr(v, 0)} zeroLine
              bars={[...versions].reverse().flatMap((v) => [
                { label: v.version.slice(-6), v: v.model?.net ?? 0, tone: "accent" as const, hint: `${v.version} · model` },
                { label: "", v: v.baseline?.net ?? 0, tone: (v.baseline?.net ?? 0) >= 0 ? ("up" as const) : ("down" as const), hint: `${v.version} · every setup` },
              ])} />
            <div className="no-scrollbar mt-3 flex gap-2 overflow-x-auto">
              {versions.map((v) => (
                <button key={v.version} type="button" onClick={() => setSel(v.version)} className={cx("chip shrink-0", chosen?.version === v.version && "chip-active")}>
                  {v.state === "champion" ? "Champion · " : ""}{v.version.replace("swing-", "")}
                </button>
              ))}
            </div>
          </>
        )}
      </Card>
      {chosen && <ModelDetail v={chosen} />}

      <Card title="Live record (last 30 days)" icon={Target} subtitle="Journalled swing signals: decided, qualified by the gate, executed and closed">
        {rep.error ? <ErrorBox error={rep.error} /> : !rep.data ? <Skeleton className="h-24" /> : !swingStrategies.length ? (
          <Empty>No swing signals journalled in the last 30 days.</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            {swingStrategies.map(([k, s]) => (
              <div key={k} className="rounded-2xl bg-ink-850 p-4">
                <div className="text-sm font-semibold text-white">{SETUP_LABEL[k.replace("swing.", "")] || k}</div>
                <div className="num mt-2 text-xs text-gray-500">{s.decisions} decided · {s.qualified} qualified · {s.executed} executed</div>
                <div className="num mt-1 flex items-baseline justify-between"><span className={cx("text-lg font-bold", pnlClass(s.net))}>{inr(s.net, 0)}</span><span className="text-xs text-gray-500">win {pct(s.win_rate, 0)} of {s.closed}</span></div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
