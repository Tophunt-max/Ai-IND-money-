"use client";

import { Activity } from "lucide-react";

import { Badge, Card, ErrorBox, KV, Skeleton } from "@/components/ui";
import { when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Analysis {
  symbol: string;
  provider: string;
  source: "live" | "delayed";
  bars: number;
  interval: string;
  last_bar: string;
  indicators: Record<string, number | null>;
  regime: { label: string; trend: string; adx: number | null; reason: string };
  data_quality: { ok: boolean; problems: string[] };
  patterns: { t: number; bias: string; names: string[] }[];
  overlays: { t: number; ema9: number | null; ema21: number | null; rsi: number | null; atr: number | null }[];
}

const n = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));
const biasTone = (b: string) => (b === "bullish" ? "ok" : b === "bearish" ? "error" : b === "conflict" ? "warning" : "neutral");

/** Deterministic indicators, candle patterns and regime of the chart's candles (evidence only). */
export default function AnalysisPanel({ symbol }: { symbol: string }) {
  const a = useApi<Analysis>(`/api/dashboard/market/analysis?symbol=${encodeURIComponent(symbol)}&range=3mo`, 300000);
  const d = a.data;
  const i = d?.indicators || {};
  return (
    <Card title="Technical analysis" icon={Activity}
      subtitle="Computed from closed candles only. Evidence for the strategies, not a buy or sell signal."
      right={d && <Badge tone={d.source === "live" ? "ok" : "warning"} dot>{d.source === "live" ? "LIVE" : "DELAYED"} · {d.provider} · {d.interval}</Badge>}>
      <ErrorBox error={a.error} />
      {a.loading && !d ? <Skeleton className="h-28" /> : d && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={d.regime.trend.startsWith("trending") ? "info" : "neutral"}>{d.regime.label}</Badge>
            <Badge tone={d.data_quality.ok ? "ok" : "warning"}>{d.data_quality.ok ? "data valid" : "data problems"}</Badge>
            <span className="text-[11px] text-gray-500">{d.bars} bars · last {when(d.last_bar)}</span>
          </div>
          <KV cols={3} items={[
            ["RSI 14", n(i.rsi, 1)], ["ADX 14", n(i.adx, 1)], ["ATR %", i.atr_pct == null ? "—" : `${(i.atr_pct * 100).toFixed(2)}%`],
            ["EMA 9 / 21", `${n(i.ema_fast)} / ${n(i.ema_slow)}`], ["MACD hist", n(i.macd_hist)], ["BB width", i.bb_width_pct == null ? "—" : `${(i.bb_width_pct * 100).toFixed(2)}%`],
            ["Support", n(i.support)], ["Resistance", n(i.resistance)], ["Rel. volume", n(i.rel_volume)],
          ]} />
          {!d.data_quality.ok && <div className="text-[11px] text-amber-300">{d.data_quality.problems.join("; ")}</div>}
          <div>
            <div className="mb-1.5 text-xs font-medium text-gray-400">Recent candle patterns</div>
            <div className="flex flex-wrap gap-1.5">
              {d.patterns.slice(-10).reverse().map((p) => (
                <span key={p.t} className="inline-flex items-center gap-1.5 text-[11px] text-gray-400">
                  <Badge tone={biasTone(p.bias)}>{p.names.join(", ")}</Badge>
                  {new Date(p.t * 1000).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" })}
                </span>
              ))}
              {!d.patterns.length && <span className="text-[11px] text-gray-500">None detected.</span>}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
