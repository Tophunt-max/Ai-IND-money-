"use client";

import { ArrowDownRight, ArrowUpRight, Brain, LineChart, NotebookPen, Radar, Search } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/components/AuthGate";
import PriceChart from "@/components/PriceChart";
import { Badge, Button, Card, ErrorBox, PageTitle, Skeleton, StatCard } from "@/components/ui";
import { inr, pct, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Quote {
  symbol: string;
  ltp: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number;
  as_of: string;
  source?: "indstocks" | "yahoo";
}

function Change({ q, big = false }: { q: { change_pct: number | null; change?: number | null }; big?: boolean }) {
  if (q.change_pct == null) return <span className="text-gray-500">—</span>;
  const up = q.change_pct >= 0;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={`num inline-flex items-center gap-1 rounded-lg px-1.5 py-0.5 font-medium ${big ? "text-sm" : "text-xs"} ${up ? "bg-emerald-500/10 text-emerald-300" : "bg-rose-500/10 text-rose-300"}`}>
      <Icon className={big ? "h-4 w-4" : "h-3.5 w-3.5"} />
      {pct(Math.abs(q.change_pct), 2)}
      {big && q.change != null && <span className="opacity-70">({q.change >= 0 ? "+" : ""}{q.change.toFixed(2)})</span>}
    </span>
  );
}

function Market() {
  const params = useSearchParams();
  const router = useRouter();
  const { isAdmin } = useAuth();
  const symbol = (params.get("symbol") || "").toUpperCase();
  const [input, setInput] = useState(symbol);
  useEffect(() => setInput(symbol), [symbol]);

  const indices = useApi<{ indices: (Quote & { name: string })[] }>("/api/dashboard/market/indices", 60000);
  const watch = useApi<{ symbols: string[] }>("/api/dashboard/market/watchlist");
  const quote = useApi<{ quotes: Record<string, Quote>; errors: Record<string, string> }>(
    symbol ? `/api/dashboard/market/quotes?symbols=${encodeURIComponent(symbol)}` : null,
    15000,
  );
  const q = symbol ? quote.data?.quotes[symbol] : undefined;
  const qErr = symbol ? quote.data?.errors[symbol] : undefined;
  const isIndex = symbol.startsWith("^");

  const go = (s: string) => {
    const v = s.trim().toUpperCase();
    if (v) router.push(`/market?symbol=${encodeURIComponent(v)}`);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(input);
  };

  return (
    <div className="space-y-6">
      <PageTitle title="Market" icon={LineChart} subtitle="Live NSE quotes from INDstocks when the token is set; indices and charts from Yahoo Finance"
        right={<Link href="/scanner"><Button variant="ghost" icon={Radar}>Scanner</Button></Link>} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {(indices.data?.indices || []).map((i) => (
          <button key={i.name} onClick={() => go(i.symbol)}
            className={`surface p-4 text-left transition hover:border-brand-500/30 ${symbol === i.symbol ? "ring-1 ring-brand-500/40" : ""}`}>
            <div className="text-xs font-medium text-gray-400">{i.name}</div>
            <div className="mt-1 flex items-end justify-between gap-2">
              <div className="num text-xl font-semibold text-white">{i.ltp?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "—"}</div>
              <Change q={i} />
            </div>
          </button>
        ))}
        {indices.loading && !indices.data && [0, 1, 2].map((i) => <Skeleton key={i} className="h-20" />)}
      </div>
      <ErrorBox error={indices.error} />

      <form onSubmit={submit} className="relative">
        <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
        <input list="nifty50" value={input} onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="Search NSE symbol: RELIANCE, TCS, HDFCBANK..." className="field h-12 pl-11 pr-24" />
        <datalist id="nifty50">{(watch.data?.symbols || []).map((s) => <option key={s} value={s} />)}</datalist>
        <Button type="submit" size="sm" disabled={!input.trim()} className="absolute right-2 top-1/2 -translate-y-1/2">Go</Button>
      </form>

      {!symbol && (
        <Card title="NIFTY 50" subtitle="Tap a stock for its quote and chart">
          <div className="flex flex-wrap gap-2">
            {(watch.data?.symbols || []).map((s) => (
              <button key={s} onClick={() => go(s)}
                className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-xs font-medium text-gray-300 transition hover:border-brand-500/30 hover:text-white">
                {s}
              </button>
            ))}
            {watch.loading && !watch.data && <Skeleton className="h-8 w-full" />}
          </div>
        </Card>
      )}

      {symbol && (
        <>
          <ErrorBox error={quote.error || qErr} />
          <div className="surface p-6">
            {quote.loading && !quote.data ? <Skeleton className="h-16 w-64" /> : q ? (
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-gray-400">{symbol}</div>
                  <div className="num mt-1 text-4xl font-semibold tracking-tight text-white">
                    {isIndex ? q.ltp?.toLocaleString("en-IN") : inr(q.ltp)}
                  </div>
                  <div className="mt-2"><Change q={q} big /></div>
                </div>
                <div className="flex flex-col items-end gap-1.5 text-right text-xs text-gray-500">
                  {q.source === "indstocks"
                    ? <Badge tone="ok" dot>LIVE · INDstocks</Badge>
                    : <Badge tone="warning" dot>Delayed · Yahoo Finance</Badge>}
                  <span>As of {when(q.as_of)} · refreshes every 15 s</span>
                </div>
              </div>
            ) : null}
            {q && (
              <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatCard title="Open" value={inr(q.open)} />
                <StatCard title="High" value={inr(q.high)} tone="ok" />
                <StatCard title="Low" value={inr(q.low)} tone="error" />
                <StatCard title="Prev close" value={inr(q.prev_close)} />
              </div>
            )}
            <div className="mt-6"><PriceChart symbol={symbol} /></div>
          </div>

          {isAdmin && !isIndex && (
            <div className="grid grid-cols-2 gap-3">
              <Link href={`/analyze?symbol=${encodeURIComponent(symbol)}`}><Button icon={Brain} size="lg" className="w-full">Analyze</Button></Link>
              <Link href={`/analyze?symbol=${encodeURIComponent(symbol)}&trade=1`}><Button variant="ghost" icon={NotebookPen} size="lg" className="w-full">Paper trade</Button></Link>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function MarketPage() {
  return (
    <Suspense>
      <Market />
    </Suspense>
  );
}
