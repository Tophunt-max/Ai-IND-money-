"use client";

import { Grid3x3, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { heatColor, sectorName, SectorBars, useHeatmap, type HeatStock } from "@/components/MarketWidgets";
import { Button, Card, cx, ErrorBox, PageTitle, Segmented, Skeleton, StatCard } from "@/components/ui";
import { useQuotes } from "@/lib/live";

type Size = "turnover" | "equal";

/** Tile size class by turnover rank within the market (bigger = more traded). */
function span(rank: number, total: number): string {
  const f = rank / Math.max(1, total);
  return f < 0.1 ? "col-span-2 row-span-2" : f < 0.3 ? "col-span-2" : "";
}

export default function HeatmapPage() {
  const heat = useHeatmap();
  const [size, setSize] = useState<Size>("turnover");
  const base = heat.data?.stocks || [];
  const { quotes } = useQuotes(base.map((s) => s.symbol));
  const stocks: HeatStock[] = useMemo(() => base.map((s) => {
    const q = quotes[s.symbol];
    return q ? { ...s, ltp: q.ltp ?? s.ltp, change_pct: q.change_pct ?? s.change_pct, change: q.change ?? s.change } : s;
  }), [base, quotes]);
  const rank = useMemo(() => new Map([...stocks].sort((a, b) => b.turnover - a.turnover).map((s, i) => [s.symbol, i])), [stocks]);
  const bySector = useMemo(() => {
    const m = new Map<string, HeatStock[]>();
    stocks.forEach((s) => m.set(s.sector, [...(m.get(s.sector) || []), s]));
    const order = (heat.data?.sectors || []).map((s) => s.sector);
    return Array.from(m.entries()).sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  }, [stocks, heat.data]);
  const priced = stocks.filter((s) => s.change_pct != null);
  const breadth = priced.length ? priced.filter((s) => (s.change_pct as number) > 0).length / priced.length : null;
  const best = [...priced].sort((a, b) => (b.change_pct as number) - (a.change_pct as number))[0];
  const worst = [...priced].sort((a, b) => (a.change_pct as number) - (b.change_pct as number))[0];

  return (
    <div className="space-y-4">
      <PageTitle title="Heatmap" icon={Grid3x3} subtitle={`NIFTY 50 by sector, coloured by the day's change${heat.data ? ` · ${heat.data.source}` : ""}`}
        right={<>
          <Segmented size="sm" value={size} onChange={setSize} options={[{ value: "turnover", label: "Size: traded value" }, { value: "equal", label: "Equal" }]} />
          <Button variant="ghost" size="sm" icon={RefreshCw} onClick={heat.reload} loading={heat.loading && !!heat.data} />
        </>} />
      <ErrorBox error={heat.error} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Advancing" value={<span className="text-emerald-400">{heat.data?.advances ?? "—"}</span>} detail="stocks up today" tone="ok" />
        <StatCard title="Declining" value={<span className="text-rose-400">{heat.data?.declines ?? "—"}</span>} detail="stocks down today" tone="error" />
        <StatCard title="Best" value={best ? best.symbol : "—"} detail={best ? <span className="text-emerald-400">+{((best.change_pct as number) * 100).toFixed(2)}%</span> : undefined} />
        <StatCard title="Worst" value={worst ? worst.symbol : "—"} detail={worst ? <span className="text-rose-400">{((worst.change_pct as number) * 100).toFixed(2)}%</span> : undefined} />
      </div>
      {breadth != null && (
        <div className="surface p-4">
          <div className="flex justify-between text-xs font-medium"><span className="text-emerald-400">{Math.round(breadth * 100)}% advancing</span><span className="text-rose-400">{Math.round((1 - breadth) * 100)}% declining</span></div>
          <div className="mt-2 flex h-2.5 overflow-hidden rounded-full bg-white/[0.05]">
            <span className="bg-emerald-500" style={{ width: `${breadth * 100}%` }} /><span className="flex-1 bg-rose-500" />
          </div>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <div className="space-y-4">
          {!heat.data ? <Skeleton className="h-96" /> : bySector.map(([sector, list]) => (
            <section key={sector} id={sector} className="surface scroll-mt-28 p-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-white">{sectorName(sector)}</h3>
                <span className="text-xs text-gray-500">{list.length} stocks</span>
              </div>
              <div className="grid auto-rows-[64px] grid-cols-3 gap-1.5 sm:grid-cols-5 lg:grid-cols-6">
                {[...list].sort((a, b) => b.turnover - a.turnover).map((s) => (
                  <Link key={s.symbol} href={`/market?symbol=${encodeURIComponent(s.symbol)}`}
                    className={cx("flex flex-col justify-center rounded-xl p-2 text-center transition hover:brightness-110", size === "turnover" && span(rank.get(s.symbol) || 0, stocks.length))}
                    style={{ background: heatColor(s.change_pct) }}>
                    <span className="truncate text-xs font-bold text-white">{s.symbol}</span>
                    <span className="num text-[11px] font-semibold text-white/80">{s.change_pct == null ? "—" : `${s.change_pct >= 0 ? "+" : ""}${(s.change_pct * 100).toFixed(2)}%`}</span>
                  </Link>
                ))}
              </div>
            </section>
          ))}
        </div>
        <Card title="Sectors" subtitle="Average day change">
          {heat.data ? <SectorBars sectors={heat.data.sectors} /> : <Skeleton className="h-64" />}
          <div className="mt-5 flex items-center gap-2 text-[11px] text-gray-500">
            <span>−3%</span>
            <div className="h-2 flex-1 rounded-full" style={{ background: `linear-gradient(90deg, ${heatColor(-0.03)}, ${heatColor(0)}, ${heatColor(0.03)})` }} />
            <span>+3%</span>
          </div>
        </Card>
      </div>
    </div>
  );
}
