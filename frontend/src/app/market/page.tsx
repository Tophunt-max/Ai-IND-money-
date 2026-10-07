"use client";

import Link from "next/link";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import PriceChart from "@/components/PriceChart";
import { Button, Card, ErrorBox, Loading, PageTitle, StatCard } from "@/components/ui";
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
}

function Change({ q }: { q: { change_pct: number | null } }) {
  if (q.change_pct == null) return <span className="text-gray-500">—</span>;
  const up = q.change_pct >= 0;
  return (
    <span className={up ? "text-green-400" : "text-red-400"}>
      {up ? "▲" : "▼"} {pct(Math.abs(q.change_pct), 2)}
    </span>
  );
}

function Market() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") || "").toUpperCase();
  const [input, setInput] = useState(symbol);
  useEffect(() => setInput(symbol), [symbol]);

  const indices = useApi<{ indices: (Quote & { name: string })[] }>("/api/dashboard/market/indices", 60000);
  const watch = useApi<{ symbols: string[] }>("/api/dashboard/market/watchlist");
  const quote = useApi<{ quotes: Record<string, Quote>; errors: Record<string, string> }>(
    symbol ? `/api/dashboard/market/quotes?symbols=${encodeURIComponent(symbol)}` : null,
    60000,
  );
  const q = symbol ? quote.data?.quotes[symbol] : undefined;
  const qErr = symbol ? quote.data?.errors[symbol] : undefined;

  const go = (s: string) => {
    const v = s.trim().toUpperCase();
    if (v) router.push(`/market?symbol=${encodeURIComponent(v)}`);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(input);
  };

  return (
    <div className="space-y-5">
      <PageTitle
        title="Market"
        right={<Link href="/scanner" className="text-sm text-blue-400">🔎 Scanner →</Link>}
      />

      {/* Indices */}
      <div className="grid grid-cols-3 gap-2">
        {(indices.data?.indices || []).map((i) => (
          <button
            key={i.name}
            onClick={() => go(i.symbol)}
            className="border border-gray-800 rounded-lg p-2 text-left hover:bg-gray-900"
          >
            <div className="text-[11px] text-gray-500">{i.name}</div>
            <div className="text-sm font-semibold">
              {i.ltp?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "—"}
            </div>
            <div className="text-[11px]"><Change q={i} /></div>
          </button>
        ))}
        {indices.loading && !indices.data && <div className="col-span-3"><Loading /></div>}
      </div>
      <ErrorBox error={indices.error} />

      {/* Search */}
      <form onSubmit={submit} className="flex gap-2">
        <input
          list="nifty50"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="Search NSE symbol: RELIANCE, TCS, HDFCBANK..."
          className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm uppercase"
        />
        <datalist id="nifty50">
          {(watch.data?.symbols || []).map((s) => <option key={s} value={s} />)}
        </datalist>
        <Button type="submit" disabled={!input.trim()}>Go</Button>
      </form>

      {!symbol && (
        <Card title="NIFTY 50">
          <div className="flex flex-wrap gap-2">
            {(watch.data?.symbols || []).map((s) => (
              <button
                key={s}
                onClick={() => go(s)}
                className="text-xs border border-gray-800 rounded px-2 py-1 text-gray-300 hover:bg-gray-800"
              >
                {s}
              </button>
            ))}
          </div>
        </Card>
      )}

      {symbol && (
        <>
          <ErrorBox error={quote.error || qErr} />
          {quote.loading && !quote.data ? (
            <Loading />
          ) : q ? (
            <div className="space-y-3">
              <div className="flex items-end justify-between flex-wrap gap-2">
                <div>
                  <div className="text-sm text-gray-400">{symbol}</div>
                  <div className="text-3xl font-bold">
                    {symbol.startsWith("^") ? q.ltp?.toLocaleString("en-IN") : inr(q.ltp)}
                  </div>
                  <div className="text-sm">
                    <Change q={q} />{" "}
                    <span className="text-gray-500">
                      {q.change != null && `(${q.change >= 0 ? "+" : ""}${q.change.toFixed(2)})`}
                    </span>
                  </div>
                </div>
                <div className="text-xs text-gray-500 text-right">
                  Yahoo Finance, may be delayed<br />{when(q.as_of)}
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <StatCard title="Open" value={inr(q.open)} />
                <StatCard title="High" value={inr(q.high)} />
                <StatCard title="Low" value={inr(q.low)} />
                <StatCard title="Prev close" value={inr(q.prev_close)} />
              </div>
            </div>
          ) : null}

          <Card>
            <PriceChart symbol={symbol} />
          </Card>

          {!symbol.startsWith("^") && (
            <div className="grid grid-cols-2 gap-3">
              <Link href={`/analyze?symbol=${encodeURIComponent(symbol)}`}>
                <Button className="w-full">🧠 Analyze</Button>
              </Link>
              <Link href={`/analyze?symbol=${encodeURIComponent(symbol)}&trade=1`}>
                <Button variant="ghost" className="w-full">📝 Paper trade</Button>
              </Link>
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
