"use client";

import { CandlestickChart, LineChart } from "lucide-react";
import { useEffect, useState } from "react";

import CandleChart, { type Candle } from "@/components/CandleChart";
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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Segmented size="sm" value={range} onChange={setRange}
          options={RANGES.map((r) => ({ value: r as string, label: r.toUpperCase() }))} />
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-[11px] text-gray-500">
            <span className={`h-1.5 w-1.5 rounded-full ${open ? "animate-pulse bg-emerald-400" : "bg-gray-600"}`} />
            {open ? (data?.live ? "Live" : "Auto") : "Market closed"} · {live ? "INDstocks" : data ? "Yahoo (delayed)" : ""} · {ago}
          </span>
          <Segmented size="sm" value={kind} onChange={setKind} options={[
            { value: "candle", label: <CandlestickChart className="h-4 w-4" aria-label="Candles" /> },
            { value: "line", label: <LineChart className="h-4 w-4" aria-label="Line" /> },
          ]} />
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <Skeleton className="h-80 w-full" />
      ) : data && kind === "candle" ? (
        <CandleChart candles={data.candles} format={format} timeLabel={timeLabel} live={!!data.live} />
      ) : data ? (
        <Chart points={data.candles.map((c) => ({ t: c.t, v: c.c }))} height={300} format={format} timeLabel={timeLabel} />
      ) : null}
    </div>
  );
}
