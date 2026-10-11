"use client";

import { CandlestickChart, Check, LineChart, Maximize2, Minimize2, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import CandleChart, { type Candle, type Level, type Marker } from "@/components/CandleChart";
import Chart from "@/components/Chart";
import { cx, ErrorBox, Segmented, Skeleton } from "@/components/ui";
import { inr } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { STUDIES } from "@/lib/indicators";
import { useQuote } from "@/lib/live";
import { useAgo, useMarketOpen } from "@/lib/market";
import { setPrefs, usePrefs, type IndicatorKey } from "@/lib/prefs";

const RANGES = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "5y"] as const;
const INTRADAY = new Set(["1d", "5d"]);

interface History {
  interval: string;
  candles: Candle[];
  live?: boolean;
  source?: "indstocks" | "yahoo";
}

/** The latest candle follows the live price between history refreshes (display only). */
export function withLivePrice(candles: Candle[], ltp: number | null | undefined, now = Date.now()): Candle[] {
  if (!ltp || !candles.length) return candles;
  const last = candles[candles.length - 1];
  const day = (t: number) => new Date((t + 19800) * 1000).toISOString().slice(0, 10);
  if (day(last.t) !== day(Math.floor(now / 1000))) return candles;   // only today's candle moves
  const merged = { ...last, c: ltp, h: Math.max(last.h ?? ltp, ltp), l: Math.min(last.l ?? ltp, ltp) };
  return [...candles.slice(0, -1), merged];
}

function StudiesMenu() {
  const [p] = usePrefs();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [open]);
  const toggle = (k: IndicatorKey) => setPrefs((x) => ({ indicators: x.indicators.includes(k) ? x.indicators.filter((i) => i !== k) : [...x.indicators, k] }));
  const active = p.indicators.filter((k) => k !== "volume" && k !== "levels").length;
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)}
        className={cx("inline-flex h-8 items-center gap-1.5 rounded-xl px-2.5 text-xs font-semibold ring-1 ring-inset transition",
          open || active ? "bg-brand-500/10 text-brand-300 ring-brand-500/30" : "text-gray-400 ring-white/[0.1] hover:text-white")}>
        <SlidersHorizontal className="h-3.5 w-3.5" /> Indicators{active ? ` · ${active}` : ""}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-2 w-60 rounded-2xl border border-white/[0.06] bg-ink-900 p-1.5 shadow-pop animate-scale-in">
          {STUDIES.map((s) => {
            const on = p.indicators.includes(s.key as IndicatorKey);
            return (
              <button key={s.key} type="button" onClick={() => toggle(s.key as IndicatorKey)}
                className="flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm text-gray-300 hover:bg-white/[0.05]">
                <span>{s.label}</span>
                <span className={cx("grid h-5 w-5 place-items-center rounded-md border", on ? "border-brand-500 bg-brand-500 text-onaccent" : "border-white/[0.15]")}>
                  {on && <Check className="h-3.5 w-3.5" />}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Price chart of one symbol: candles or line, ranges, indicators, drawings, levels. */
export default function PriceChart({ symbol, initial, extraLevels = [] }: { symbol: string; initial?: string; extraLevels?: Level[] }) {
  const [prefs] = usePrefs();
  const [range, setRange] = useState(initial || prefs.chartRange || "1d");
  const kind = prefs.chartType;
  const [full, setFull] = useState(false);
  const [height, setHeight] = useState(360);
  useEffect(() => {
    const set = () => setHeight(full ? Math.max(360, window.innerHeight - 170)
      : window.innerWidth < 640 ? 320 : window.innerWidth < 1024 ? 380 : 440);
    set();
    window.addEventListener("resize", set);
    return () => window.removeEventListener("resize", set);
  }, [full]);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFull(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);
  const open = useMarketOpen();
  const every = open ? (INTRADAY.has(range) ? 30000 : 120000) : 300000;
  const { data, error, loading, updatedAt } = useApi<History>(
    symbol ? `/api/dashboard/market/history?symbol=${encodeURIComponent(symbol)}&range=${range}` : null, every);
  const ago = useAgo(updatedAt);
  const isIdx = symbol.startsWith("^");
  const studies = prefs.indicators;
  const analysis = useApi<{ patterns: Marker[] }>(
    studies.includes("patterns") && symbol && !isIdx ? `/api/dashboard/market/analysis?symbol=${encodeURIComponent(symbol)}&range=${range}` : null, every * 2);
  const sig = useApi<{ signals: { status: string; entry: number; stop: number; target: number }[] }>(
    studies.includes("levels") && symbol && !isIdx ? `/api/dashboard/signals?days=1&limit=20&symbol=${encodeURIComponent(symbol)}` : null, 60000);
  const latest = sig.data?.signals?.[0];
  const levels: Level[] = [
    ...(latest ? [
      { label: latest.status === "QUALIFIED" ? "Signal entry" : "Rejected entry", price: latest.entry, tone: "entry" as const },
      { label: "Signal stop", price: latest.stop, tone: "stop" as const },
      { label: "Signal target", price: latest.target, tone: "target" as const },
    ] : []),
    ...extraLevels,
  ];
  const q = useQuote(symbol);
  const intraday = INTRADAY.has(range);
  const candles = useMemo(() => (data ? withLivePrice(data.candles, q?.ltp) : []), [data, q?.ltp]);
  const live = data?.source === "indstocks" || q?.source === "indstocks";
  const format = (v: number) => (isIdx ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : inr(v));
  const timeLabel = (t: number) =>
    new Date(t * 1000).toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      ...(intraday
        ? (range === "1d" ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", hour: "2-digit", minute: "2-digit" })
        : { day: "2-digit", month: "short", ...(range === "5y" || range === "1y" ? { year: "2-digit" } : {}) }),
    });

  const body = (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Segmented size="sm" value={range} onChange={(r) => { setRange(r); setPrefs({ chartRange: r }); }}
          options={RANGES.map((r) => ({ value: r as string, label: r.toUpperCase() }))} />
        <div className="flex items-center gap-1.5">
          <span className="hidden items-center gap-1.5 text-[11px] text-gray-500 sm:flex">
            <span className={cx("h-1.5 w-1.5 rounded-full", open ? "animate-pulse bg-emerald-400" : "bg-gray-500")} />
            {open ? (live ? "Live" : "Delayed") : "Closed"} · {ago}
          </span>
          {kind === "candle" && <StudiesMenu />}
          <Segmented size="sm" value={kind} onChange={(k) => setPrefs({ chartType: k })} options={[
            { value: "candle", label: <CandlestickChart className="h-4 w-4" aria-label="Candles" /> },
            { value: "line", label: <LineChart className="h-4 w-4" aria-label="Line" /> },
          ]} />
          <button type="button" onClick={() => setFull(!full)} aria-label={full ? "Exit full screen" : "Full screen"}
            className="grid h-8 w-8 place-items-center rounded-xl text-gray-400 ring-1 ring-inset ring-white/[0.1] hover:text-white">
            {full ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <div style={{ height }}><Skeleton className="h-full w-full" /></div>
      ) : data && kind === "candle" ? (
        <CandleChart candles={candles} format={format} timeLabel={timeLabel} live={live} height={height}
          studies={isIdx ? studies.filter((s) => s !== "patterns" && s !== "levels") : studies}
          intraday={intraday} markers={analysis.data?.patterns} levels={levels} drawKey={symbol} />
      ) : data ? (
        <Chart points={candles.map((c) => ({ t: c.t, v: c.c }))} height={height - 40} format={format} timeLabel={timeLabel} />
      ) : null}
    </div>
  );

  if (!full) return body;
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-ink-950 p-3 pt-[calc(0.75rem+env(safe-area-inset-top))] sm:p-6">
      <div className="mb-3 text-lg font-bold text-white">{symbol.replace("^", "")}</div>
      {body}
    </div>
  );
}
