"use client";

import { Activity, CandlestickChart, LineChart } from "lucide-react";
import { useEffect, useState } from "react";

import CandleChart, { type Candle, type Level, type Marker, type Overlay } from "@/components/CandleChart";
import Chart from "@/components/Chart";
import { ErrorBox, Segmented, Skeleton } from "@/components/ui";
import { inr } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useAgo, useMarketOpen } from "@/lib/market";

const RANGES = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "5y"] as const;
const INTRADAY = new Set(["1d", "5d"]);

interface History {
  interval: string;
  candles: Candle[];
  live?: boolean;
  source?: "indstocks" | "yahoo";
}

/** Price chart of one symbol: candles or line, range buttons, refreshed automatically. */
export default function PriceChart({ symbol, initial = "1d" }: { symbol: string; initial?: string }) {
  const [range, setRange] = useState(initial);
  const [kind, setKind] = useState<"candle" | "line">("candle");
  // Taller on a desktop, a thumb-friendly height on a phone
  const [height, setHeight] = useState(340);
  useEffect(() => {
    const set = () => setHeight(window.innerWidth < 640 ? 300 : window.innerWidth < 1024 ? 360 : 420);
    set();
    window.addEventListener("resize", set);
    return () => window.removeEventListener("resize", set);
  }, []);
  const open = useMarketOpen();
  // While NSE is open: intraday charts every 15 s, daily ones every minute (today's candle
  // follows the live price). Closed: every 5 minutes.
  // INDstocks charts are live: the 1D chart every 10 s
  const [fast, setFast] = useState(false);
  const every = open ? (INTRADAY.has(range) ? (fast ? 10000 : 15000) : 60000) : 300000;
  const { data, error, loading, updatedAt } = useApi<History>(
    symbol ? `/api/dashboard/market/history?symbol=${encodeURIComponent(symbol)}&range=${range}` : null,
    every,
  );
  const ago = useAgo(updatedAt);
  // Indicator overlays, pattern markers and the latest signal's levels (evidence only)
  const [studies, setStudies] = useState(true);
  const isIdx = symbol.startsWith("^");
  const analysis = useApi<{ overlays: Overlay[]; patterns: Marker[]; indicators?: Record<string, number | null> }>(
    studies && symbol && !isIdx ? `/api/dashboard/market/analysis?symbol=${encodeURIComponent(symbol)}&range=${range}` : null,
    every * 2,
  );
  const sig = useApi<{ signals: { status: string; entry: number; stop: number; target: number; decided_at: string }[] }>(
    studies && symbol && !isIdx ? `/api/dashboard/signals?days=1&limit=20&symbol=${encodeURIComponent(symbol)}` : null,
    60000,
  );
  const latest = sig.data?.signals?.[0];
  const levels: Level[] = latest ? [
    { label: `${latest.status === "QUALIFIED" ? "Entry" : "Rejected entry"}`, price: latest.entry, tone: "entry" },
    { label: "Stop", price: latest.stop, tone: "stop" },
    { label: "Target", price: latest.target, tone: "target" },
  ] : [];
  const live = data?.source === "indstocks";
  useEffect(() => setFast(live), [live]);
  const intraday = INTRADAY.has(range);
  const isIndex = symbol.startsWith("^");
  const format = (v: number) => (isIndex ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : inr(v));
  const timeLabel = (t: number) =>
    new Date(t * 1000).toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      ...(intraday
        ? (range === "1d" ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", hour: "2-digit", minute: "2-digit" })
        : { day: "2-digit", month: "short", ...(range === "5y" || range === "1y" ? { year: "2-digit" } : {}) }),
    });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Segmented size="sm" value={range} onChange={setRange}
          options={RANGES.map((r) => ({ value: r as string, label: r.toUpperCase() }))} />
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-[11px] text-gray-500">
            <span className={`h-1.5 w-1.5 rounded-full ${open ? "animate-pulse bg-emerald-400" : "bg-gray-600"}`} />
            {open ? (data?.live ? "Live" : "Auto") : "Market closed"} · {live ? "INDstocks" : data ? "Yahoo (delayed)" : ""} · {ago}
          </span>
          {!isIdx && (
            <button type="button" onClick={() => setStudies(!studies)} title="EMA 9/21, candle patterns, signal levels"
              className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium ring-1 ring-inset transition ${studies ? "bg-brand-500/15 text-brand-200 ring-brand-500/30" : "text-gray-400 ring-white/10"}`}>
              <Activity className="h-3.5 w-3.5" /> Studies
            </button>
          )}
          <Segmented size="sm" value={kind} onChange={setKind} options={[
            { value: "candle", label: <CandlestickChart className="h-4 w-4" aria-label="Candles" /> },
            { value: "line", label: <LineChart className="h-4 w-4" aria-label="Line" /> },
          ]} />
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <div style={{ height }}><Skeleton className="h-full w-full" /></div>
      ) : data && kind === "candle" ? (
        <>
          <CandleChart candles={data.candles} format={format} timeLabel={timeLabel} live={!!data.live} height={height} rsi={studies}
            overlays={studies ? analysis.data?.overlays : undefined}
            markers={studies ? analysis.data?.patterns : undefined}
            levels={studies ? levels : undefined} />
          {studies && !isIdx && (
            <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-gray-500">
              <span className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-4 rounded-sm bg-amber-400" /> EMA 9</span>
              <span className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-4 rounded-sm bg-violet-400" /> EMA 21 · RSI</span>
              <span>pinch / wheel to zoom</span>
              <span>triangles: candle patterns (green bullish, red bearish)</span>
              {latest ? <span>levels: today&apos;s latest signal ({latest.status})</span> : <span>no signal today</span>}
              {analysis.error && <span className="text-amber-300">studies unavailable</span>}
            </div>
          )}
        </>
      ) : data ? (
        <Chart points={data.candles.map((c) => ({ t: c.t, v: c.c }))} height={height - 40} format={format} timeLabel={timeLabel} />
      ) : null}
    </div>
  );
}
