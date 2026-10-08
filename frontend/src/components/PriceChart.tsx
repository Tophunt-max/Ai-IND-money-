"use client";

import { useState } from "react";

import Chart from "@/components/Chart";
import { ErrorBox, Loading, Segmented } from "@/components/ui";
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
      <div className="mb-4">
        <Segmented size="sm" value={range} onChange={setRange}
          options={RANGES.map((r) => ({ value: r as string, label: r.toUpperCase() }))} />
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
