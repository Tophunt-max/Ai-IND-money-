"use client";

import { ArrowDownRight, ArrowUpRight, Brain, LineChart, NotebookPen, Radar, Search } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/components/AuthGate";
import PriceChart from "@/components/PriceChart";
import { Badge, Button, Card, ErrorBox, PageTitle, Skeleton, StatCard } from "@/components/ui";
import { inr, pct, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useAgo, useMarketOpen } from "@/lib/market";

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

/** Briefly tints its children green / red when *value* goes up / down. */
function Flash({ value, children }: { value: number | null | undefined; children: React.ReactNode }) {
  const prev = useRef(value);
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  useEffect(() => {
    if (value != null && prev.current != null && value !== prev.current) {
      setDir(value > prev.current ? "up" : "down");
      const id = setTimeout(() => setDir(null), 900);
      prev.current = value;
      return () => clearTimeout(id);
    }
    prev.current = value;
  }, [value]);
  return (
    <span className={`rounded-lg transition-colors duration-700 ${dir === "up" ? "bg-emerald-500/20" : dir === "down" ? "bg-rose-500/20" : "bg-transparent"}`}>
      {children}
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

  const open = useMarketOpen();
  // INDstocks is live: every 5 s while NSE is open (Yahoo lags anyway: 15 s)
  const [liveSource, setLiveSource] = useState(false);
  const every = open ? (liveSource ? 5000 : 15000) : 60000;
  const indices = useApi<{ indices: (Quote & { name: string })[]; source?: string }>("/api/dashboard/market/indices", every);
  const watch = useApi<{ symbols: string[] }>("/api/dashboard/market/watchlist");
  const quote = useApi<{ quotes: Record<string, Quote>; errors: Record<string, string> }>(
    symbol ? `/api/dashboard/market/quotes?symbols=${encodeURIComponent(symbol)}` : null,
    every,
  );
  const quoteAgo = useAgo(quote.updatedAt);
  const indicesAgo = useAgo(indices.updatedAt);
  const q = symbol ? quote.data?.quotes[symbol] : undefined;
  const indicesLive = (indices.data?.indices || []).some((i) => i.source === "indstocks");
  useEffect(() => setLiveSource(indicesLive || q?.source === "indstocks"), [indicesLive, q?.source]);
  const qErr = symbol ? quote.data?.errors[symbol] : undefined;
  const isIndex = symbol.startsWith("^");
  // Index levels are points, not rupees
  const fmt = (v: number | null) => (isIndex ? (v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: 2 })) : inr(v));

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
      <PageTitle title="Market" icon={LineChart} subtitle="Live quotes, indices and charts from INDstocks (INDmoney) when the token is set; Yahoo Finance only as a fallback"
        right={<Link href="/scanner"><Button variant="ghost" icon={Radar}>Scanner</Button></Link>} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {(indices.data?.indices || []).map((i) => (
          <button key={i.name} onClick={() => go(i.symbol)}
            className={`surface p-4 text-left transition hover:border-brand-500/30 ${symbol === i.symbol ? "ring-1 ring-brand-500/40" : ""}`}>
            <div className="flex items-center justify-between gap-2 text-xs font-medium text-gray-400">
              <span>{i.name}</span>
              <span className={`text-[10px] ${i.source === "indstocks" ? "text-emerald-400" : "text-amber-300/80"}`}>{i.source === "indstocks" ? "LIVE" : "Delayed"}</span>
            </div>
            <div className="mt-1 flex items-end justify-between gap-2">
              <div className="num text-xl font-semibold text-white"><Flash value={i.ltp}>{i.ltp?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "—"}</Flash></div>
              <Change q={i} />
            </div>
          </button>
        ))}
        {indices.loading && !indices.data && [0, 1, 2].map((i) => <Skeleton key={i} className="h-20" />)}
      </div>
      <div className="-mt-3 flex items-center gap-2 text-[11px] text-gray-500">
        <span className={`h-1.5 w-1.5 rounded-full ${open ? "animate-pulse bg-emerald-400" : "bg-gray-600"}`} />
        {open ? `NSE open · auto-refresh every ${every / 1000}s` : "NSE closed · refresh every minute"} · {indicesLive ? "INDstocks live" : "Yahoo Finance (no INDstocks token?)"} · updated {indicesAgo}
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
                    <Flash value={q.ltp}>{fmt(q.ltp)}</Flash>
                  </div>
                  <div className="mt-2"><Change q={q} big /></div>
                </div>
                <div className="flex flex-col items-end gap-1.5 text-right text-xs text-gray-500">
                  {q.source === "indstocks"
                    ? <Badge tone="ok" dot>LIVE · INDstocks</Badge>
                    : <Link href="/broker"><Badge tone="warning" dot>Delayed · Yahoo Finance — set the INDstocks token</Badge></Link>}
                  <span>As of {when(q.as_of)}</span>
                  <span>Updated {quoteAgo} · every {every / 1000}s</span>
                </div>
              </div>
            ) : null}
            {q && (
              <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatCard title="Open" value={fmt(q.open)} />
                <StatCard title="High" value={fmt(q.high)} tone="ok" />
                <StatCard title="Low" value={fmt(q.low)} tone="error" />
                <StatCard title="Prev close" value={fmt(q.prev_close)} />
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
