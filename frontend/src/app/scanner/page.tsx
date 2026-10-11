"use client";

import { ArrowRight, Brain, Play, Radar } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { swingTradingMode, type SwingStatus } from "@/components/SwingModel";
import { Badge, Button, Card, Empty, ErrorBox, Field, KV, Notice, PageTitle, Progress, Select } from "@/components/ui";
import { useApi, useJob } from "@/lib/hooks";

export interface SwingIdea {
  symbol: string;
  setup: string;
  reason: string;
  entry: number;
  stop: number;
  target: number;
  prob: number | null;
  score: number;
}

interface ScanResult {
  model: string | null;
  model_state: string;
  threshold: number | null;
  rules_only: boolean;
  ideas: SwingIdea[];
}

const SETUP_TONE: Record<string, "ok" | "info" | "warning"> = { trend_pullback: "info", breakout: "ok", oversold_bounce: "warning" };
const n2 = (v: number) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pctFrom = (a: number, b: number) => `${(((b - a) / a) * 100).toFixed(1)}%`;

function IdeaList({ items }: { items: SwingIdea[] }) {
  return (
    <div className="-mx-2 divide-y divide-white/[0.04]">
      {items.map((c, i) => (
        <div key={`${c.symbol}-${i}`} className="flex items-start justify-between gap-3 rounded-xl px-2 py-3 hover:bg-white/[0.02]">
          <div className="flex min-w-0 gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-ink-700 to-ink-800 text-xs font-bold text-gray-200 ring-1 ring-white/[0.06]">
              {c.symbol.slice(0, 2)}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-white">{c.symbol}</span>
                <Badge tone={SETUP_TONE[c.setup] || "neutral"}>{c.setup.replace(/_/g, " ")}</Badge>
                {c.prob != null && <Badge tone={c.prob >= 0.6 ? "ok" : "neutral"}>P(win) {(c.prob * 100).toFixed(0)}%</Badge>}
              </div>
              <div className="num mt-1 flex flex-wrap gap-x-3 text-xs">
                <span className="text-gray-300">Entry ₹{n2(c.entry)}</span>
                <span className="text-rose-300">SL ₹{n2(c.stop)} ({pctFrom(c.entry, c.stop)})</span>
                <span className="text-emerald-300">TG ₹{n2(c.target)} (+{pctFrom(c.entry, c.target).replace("-", "")})</span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-gray-400">{c.reason}</p>
            </div>
          </div>
          <Link href={`/market?symbol=${encodeURIComponent(c.symbol)}`} className="link flex shrink-0 items-center gap-1 text-xs font-medium">
            Chart <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      ))}
    </div>
  );
}

export default function ScannerPage() {
  const [max, setMax] = useState(10);
  const { isAdmin } = useAuth();
  const { job, error, running, start } = useJob();
  const swing = useApi<SwingStatus>("/api/dashboard/swing", 120000);
  const res: ScanResult | null = job?.status === "done" ? job.result : null;
  const ideas = res?.ideas || [];
  const mode = swing.data ? swingTradingMode(swing.data) : null;

  return (
    <div className="space-y-3 sm:space-y-4">
      <PageTitle title="Swing scanner" icon={Radar} subtitle="The Adaptive AI scores today's daily setups with the swing model. Nothing is bought here." />

      <div className="grid gap-3 lg:grid-cols-3">
        <Card title="Scan for swing ideas" icon={Play} className="lg:col-span-2">
          <p className="mb-4 text-sm text-gray-400">
            Reads a year of daily candles for each symbol, finds a trend pullback, breakout or oversold bounce on the last closed bar, and keeps it when the model&apos;s probability clears its validated threshold.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Ideas" className="w-36">
              <Select value={max} onChange={(e) => setMax(Number(e.target.value))}>
                {[3, 5, 10, 20].map((v) => <option key={v} value={v}>{v}</option>)}
              </Select>
            </Field>
            <Button icon={Radar} loading={running} onClick={() => start({ kind: "scan", max_candidates: max })} disabled={!isAdmin}>
              {running ? "Scanning..." : "Run scan"}
            </Button>
          </div>
          {running && <div className="mt-4"><Progress value={60} /><p className="mt-2 text-xs text-gray-500">Scoring setups (up to a minute)...</p></div>}
          {!isAdmin && <p className="mt-3 text-xs text-gray-500">View-only account: running a scan needs an admin.</p>}
        </Card>

        <Card title="Swing model" icon={Brain}
          right={swing.data && <Badge tone={swing.data.active ? "ok" : "warning"} dot>{swing.data.active ? "Ready" : "No model"}</Badge>}>
          <ErrorBox error={swing.error} />
          {swing.data && mode && (
            <>
              <KV items={[
                ["Paper", mode.paper],
                ["Live", mode.live],
                ["Universe", swing.data.config.symbols],
                ["Target", `${swing.data.config.target_rr} R · same-day exit`],
              ]} />
              <Link href="/learning" className="link mt-3 inline-flex items-center gap-1 text-xs font-medium">Train / promote <ArrowRight className="h-3.5 w-3.5" /></Link>
            </>
          )}
        </Card>
      </div>

      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />

      {res && (
        <Card title={`${ideas.length} swing ideas`}
          subtitle={res.model ? `Model ${res.model} (${res.model_state}) · P >= ${res.threshold ?? "—"}` : res.rules_only ? "Rules only (paper): ranked by trend, no model" : "No swing model"}>
          {ideas.length === 0
            ? <Empty icon={Radar}>{!res.model && !res.rules_only
              ? "No swing model yet, so no ideas: train one on the AI learning page (or set SKOPAQ_SWING_RULES_ONLY=true for paper)."
              : "No setup cleared the model today (without an INDstocks token or market data there is nothing to score)."}</Empty>
            : <IdeaList items={ideas} />}
        </Card>
      )}

      {!job && (
        <Notice tone="neutral" icon={Radar}>Run a scan to see today&apos;s swing ideas. The trading session runs the same scan at the open and sends each idea through the signal gate and the safety checker.</Notice>
      )}
    </div>
  );
}
