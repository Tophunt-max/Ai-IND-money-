"use client";

import { useState } from "react";

import Chart from "@/components/Chart";
import { ErrorBox, Loading } from "@/components/ui";
import { inr } from "@/lib/api";
import { useApi } from "@/lib/hooks";

const RANGES = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "5y"] as const;

interface History {
  interval: string;
  candles: { t: number; c: number }[];
}

/** Price chart of one symbol with range buttons (Yahoo Finance via the backend). */
export default function PriceChart({ symbol, initial = "3mo" }: { symbol: string; initial?: string }) {
  const [range, setRange] = useState(initial);
  const { data, error, loading } = useApi<History>(
    symbol ? `/api/dashboard/market/history?symbol=${encodeURIComponent(symbol)}&range=${range}` : null,
  );
  const intraday = range === "1d" || range === "5d";
  const isIndex = symbol.startsWith("^");
  const points = (data?.candles || []).map((c) => ({ t: c.t, v: c.c }));

  return (
    <div>
      <div className="flex gap-1 flex-wrap mb-3">
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={`px-2.5 py-1 rounded text-xs ${
              range === r ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            {r.toUpperCase()}
          </button>
        ))}
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <Loading text="Loading chart..." />
      ) : (
        <Chart
          points={points}
          format={(v) => (isIndex ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : inr(v))}
          timeLabel={(t) =>
            new Date(t * 1000).toLocaleString("en-IN", {
              timeZone: "Asia/Kolkata",
              ...(intraday
                ? { day: "2-digit", hour: "2-digit", minute: "2-digit" }
                : { day: "2-digit", month: "short", ...(range === "5y" ? { year: "2-digit" } : {}) }),
            })
          }
        />
      )}
    </div>
  );
}
