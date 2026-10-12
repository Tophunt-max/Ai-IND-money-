"use client";

import { ArrowLeft, Radar, Search, Star } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import AnalysisPanel from "@/components/AnalysisPanel";
import { useAuth } from "@/components/AuthGate";
import PriceChart from "@/components/PriceChart";
import { Badge, Button, Change, cx, ErrorBox, Segmented, Skeleton } from "@/components/ui";
import { inr, when } from "@/lib/api";
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

const MINE_KEY = "aiind:watchlist";

/** The user's own list (kept in this browser). */
function useMyList() {
  const [list, setList] = useState<string[]>([]);
  useEffect(() => {
    try { setList(JSON.parse(localStorage.getItem(MINE_KEY) || "[]")); } catch { setList([]); }
  }, []);
  const save = (next: string[]) => {
    setList(next);
    try { localStorage.setItem(MINE_KEY, JSON.stringify(next)); } catch { /* private mode */ }
  };
  return {
    list,
    has: (s: string) => list.includes(s),
    toggle: (s: string) => save(list.includes(s) ? list.filter((x) => x !== s) : [s, ...list].slice(0, 50)),
  };
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
    <span className={`rounded-sm px-0.5 transition-colors duration-700 ${dir === "up" ? "bg-emerald-500/25" : dir === "down" ? "bg-rose-500/25" : "bg-transparent"}`}>
      {children}
    </span>
  );
}

/** Quotes for any number of symbols (the API takes 25 per call). */
function useQuotes(symbols: string[], every: number) {
  const a = symbols.slice(0, 25);
  const b = symbols.slice(25, 50);
  const qa = useApi<{ quotes: Record<string, Quote>; errors: Record<string, string> }>(
    a.length ? `/api/dashboard/market/quotes?symbols=${encodeURIComponent(a.join(","))}` : null, every);
  const qb = useApi<{ quotes: Record<string, Quote>; errors: Record<string, string> }>(
    b.length ? `/api/dashboard/market/quotes?symbols=${encodeURIComponent(b.join(","))}` : null, every);
  return {
    quotes: { ...(qa.data?.quotes || {}), ...(qb.data?.quotes || {}) },
    loading: (qa.loading && !qa.data) || (qb.loading && !qb.data),
    error: qa.error || qb.error,
    updatedAt: qa.updatedAt,
  };
}

const SORTS = [
  { value: "az", label: "A–Z" }, { value: "gain", label: "Gainers" }, { value: "lose", label: "Losers" },
] as const;

function Watchlist({ go, every }: { go: (s: string) => void; every: number }) {
  const watch = useApi<{ symbols: string[] }>("/api/dashboard/market/watchlist");
  const mine = useMyList();
  const [tab, setTab] = useState<"nifty" | "mine">("nifty");
  const [sort, setSort] = useState<(typeof SORTS)[number]["value"]>("az");
  const [filter, setFilter] = useState("");
  const symbols = tab === "mine" ? mine.list : (watch.data?.symbols || []);
  const { quotes, loading, error, updatedAt } = useQuotes(symbols, every);
  const ago = useAgo(updatedAt);
  const rows = useMemo(() => {
    const r = symbols.filter((s) => !filter || s.includes(filter.toUpperCase())).map((s) => ({ s, q: quotes[s] }));
    if (sort === "gain") r.sort((x, y) => (y.q?.change_pct ?? -9) - (x.q?.change_pct ?? -9));
    else if (sort === "lose") r.sort((x, y) => (x.q?.change_pct ?? 9) - (y.q?.change_pct ?? 9));
    else r.sort((x, y) => x.s.localeCompare(y.s));
    return r;
  }, [symbols, quotes, sort, filter]);
  const adv = Object.values(quotes).filter((q) => (q.change_pct ?? 0) > 0).length;
  const dec = Object.values(quotes).filter((q) => (q.change_pct ?? 0) < 0).length;

  return (
    <section className="surface overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/6 p-2.5">
        <Segmented size="sm" value={tab} onChange={setTab}
          options={[{ value: "nifty", label: "NIFTY 50" }, { value: "mine", label: `My list (${mine.list.length})` }]} />
        <Segmented size="sm" value={sort} onChange={setSort} options={SORTS} />
      </div>
      <div className="flex items-center gap-2 border-b border-white/6 px-3 py-2">
        <Search className="h-3.5 w-3.5 text-gray-500" />
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter"
          className="w-full bg-transparent text-sm text-gray-100 outline-hidden placeholder:text-gray-600" />
        {Object.keys(quotes).length > 0 && (
          <span className="num shrink-0 text-[10px] text-gray-500">
            <span className="text-emerald-400">{adv} up</span> · <span className="text-rose-400">{dec} down</span> · {ago}
          </span>
        )}
      </div>
      <ErrorBox error={error} />
      {tab === "mine" && mine.list.length === 0 ? (
        <p className="px-3 py-8 text-center text-sm text-gray-500">Tap ☆ on a stock to add it to your list.</p>
      ) : (
        <ul className="divide-y divide-white/4">
          {rows.map(({ s, q }) => (
            <li key={s}>
              <button type="button" onClick={() => go(s)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition hover:bg-white/3 active:bg-white/6">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-semibold text-gray-100">{s}</div>
                  <div className="num text-[10px] text-gray-500">{q ? `NSE · H ${q.high?.toFixed(2) ?? "—"} L ${q.low?.toFixed(2) ?? "—"}` : "NSE"}</div>
                </div>
                <div className="num text-right">
                  <div className={cx("text-[13px] font-semibold", (q?.change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    <Flash value={q?.ltp}>{q?.ltp != null ? q.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : loading ? "…" : "—"}</Flash>
                  </div>
                  <div className="text-[11px]"><Change value={q?.change_pct ?? null} abs={q?.change} size="xs" pill={false} /></div>
                </div>
              </button>
            </li>
          ))}
          {loading && !rows.length && [0, 1, 2, 3, 4].map((i) => <li key={i} className="p-3"><Skeleton className="h-8" /></li>)}
        </ul>
      )}
    </section>
  );
}

function Detail({ symbol, every, back }: { symbol: string; every: number; back: () => void }) {
  const { isAdmin } = useAuth();
  const mine = useMyList();
  const quote = useApi<{ quotes: Record<string, Quote>; errors: Record<string, string> }>(
    `/api/dashboard/market/quotes?symbols=${encodeURIComponent(symbol)}`, every);
  const ago = useAgo(quote.updatedAt);
  const q = quote.data?.quotes[symbol];
  const qErr = quote.data?.errors[symbol];
  const isIndex = symbol.startsWith("^");
  // Index levels are points, not rupees
  const fmt = (v: number | null | undefined) => (isIndex ? (v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: 2 })) : inr(v));
  const range = q && q.high != null && q.low != null && q.high > q.low && q.ltp != null ? ((q.ltp - q.low) / (q.high - q.low)) * 100 : null;

  return (
    <div className="space-y-3 pb-16 lg:pb-0">
      <ErrorBox error={quote.error || qErr} />
      <section className="surface p-3 sm:p-4">
        <div className="flex items-start gap-2">
          <button type="button" onClick={back} aria-label="Back to the watchlist" className="-ml-1 rounded-md p-1 text-gray-400 hover:bg-white/6 hover:text-white lg:hidden">
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-bold text-white">{symbol.replace("^", "")}</h2>
              <span className="text-[10px] font-semibold uppercase text-gray-500">{isIndex ? "Index" : "NSE"}</span>
              {!isIndex && (
                <button type="button" onClick={() => mine.toggle(symbol)} aria-label={mine.has(symbol) ? "Remove from my list" : "Add to my list"}
                  className={cx("rounded-sm p-0.5", mine.has(symbol) ? "text-amber-300" : "text-gray-600 hover:text-gray-300")}>
                  <Star className="h-4 w-4" fill={mine.has(symbol) ? "currentColor" : "none"} />
                </button>
              )}
            </div>
            {quote.loading && !quote.data ? <Skeleton className="mt-1 h-8 w-40" /> : q ? (
              <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className={cx("num text-2xl font-bold sm:text-3xl", (q.change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                  <Flash value={q.ltp}>{fmt(q.ltp)}</Flash>
                </span>
                <Change value={q.change_pct} abs={q.change} size="sm" />
              </div>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1 text-right text-[10px] text-gray-500">
            {q?.source === "indstocks" ? <Badge tone="ok" dot>LIVE</Badge>
              : q ? <Link href="/broker"><Badge tone="warning" dot>DELAYED</Badge></Link> : null}
            {q && <span>{when(q.as_of)}</span>}
            <span>{ago}</span>
          </div>
        </div>
        {q && (
          <div className="num mt-3 grid grid-cols-4 gap-2 border-t border-white/6 pt-3 text-[11px]">
            {[["Open", q.open], ["High", q.high], ["Low", q.low], ["Prev", q.prev_close]].map(([k, v]) => (
              <div key={k as string}><div className="text-[10px] uppercase text-gray-500">{k}</div><div className="font-semibold text-gray-100">{fmt(v as number | null)}</div></div>
            ))}
          </div>
        )}
        {range != null && (
          <div className="mt-2">
            <div className="relative h-1 rounded-full bg-linear-to-r from-rose-500/60 via-gray-600 to-emerald-500/60">
              <span className="absolute top-1/2 h-2.5 w-1 -translate-x-1/2 -translate-y-1/2 rounded-sm bg-white" style={{ left: `${range}%` }} />
            </div>
            <div className="mt-0.5 flex justify-between text-[10px] text-gray-500"><span>Day low</span>{q?.volume ? <span className="num">Vol {q.volume.toLocaleString("en-IN")}</span> : null}<span>Day high</span></div>
          </div>
        )}
      </section>

      <section className="surface min-w-0 overflow-hidden p-2 sm:p-4"><PriceChart symbol={symbol} /></section>

      {!isIndex && <AnalysisPanel symbol={symbol} />}

      {/* Order actions: fixed above the tab bar on a phone */}
      {isAdmin && !isIndex && (
        <div className="fixed inset-x-0 bottom-[calc(3.6rem+env(safe-area-inset-bottom))] z-20 grid grid-cols-2 gap-2 border-t border-white/6 bg-ink-950/95 p-2 backdrop-blur-sm lg:static lg:border-0 lg:bg-transparent lg:p-0">
          <Link href={`/control?symbol=${encodeURIComponent(symbol)}&side=BUY#order`}><Button variant="success" className="w-full">BUY</Button></Link>
          <Link href={`/control?symbol=${encodeURIComponent(symbol)}&side=SELL#order`}><Button variant="danger" className="w-full">SELL</Button></Link>
        </div>
      )}
    </div>
  );
}

function Market() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") || "").toUpperCase();
  const [input, setInput] = useState("");
  const watch = useApi<{ symbols: string[] }>("/api/dashboard/market/watchlist");
  const open = useMarketOpen();
  const every = open ? 10000 : 60000;
  const indices = useApi<{ indices: (Quote & { name: string })[] }>("/api/dashboard/market/indices", open ? 15000 : 120000);

  const go = (s: string) => {
    const v = s.trim().toUpperCase();
    if (v) router.push(`/market?symbol=${encodeURIComponent(v)}`);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(input);
    setInput("");
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <form onSubmit={submit} className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
          <input list="nifty50" value={input} onChange={(e) => setInput(e.target.value.toUpperCase())}
            placeholder="Search NSE: RELIANCE, TCS, ^NSEI…" className="field h-10 pl-9" />
          <datalist id="nifty50">{(watch.data?.symbols || []).map((s) => <option key={s} value={s} />)}</datalist>
        </form>
        <Link href="/scanner"><Button variant="ghost" icon={Radar} className="h-10">Scan</Button></Link>
      </div>

      <div className="no-scrollbar -mx-3 flex gap-2 overflow-x-auto px-3 sm:mx-0 sm:grid sm:grid-cols-3 sm:px-0">
        {(indices.data?.indices || []).map((i) => (
          <button key={i.name} onClick={() => go(i.symbol)}
            className={cx("surface min-w-[150px] shrink-0 p-2.5 text-left transition hover:border-brand-500/30", symbol === i.symbol && "ring-1 ring-brand-500/50")}>
            <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wide text-gray-500">
              <span>{i.name}</span>
              <span className={i.source === "indstocks" ? "text-emerald-400" : "text-amber-300/80"}>{i.source === "indstocks" ? "LIVE" : "DLY"}</span>
            </div>
            <div className="num mt-0.5 text-base font-bold text-white"><Flash value={i.ltp}>{i.ltp?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "—"}</Flash></div>
            <Change value={i.change_pct} size="xs" pill={false} />
          </button>
        ))}
        {indices.loading && !indices.data && [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 min-w-[150px]" />)}
      </div>

      {/* Phone: the list, or one stock. Desktop: both side by side */}
      <div className="grid gap-3 lg:grid-cols-[340px_1fr]">
        <div className={cx("min-w-0", symbol && "hidden lg:block")}><Watchlist go={go} every={every} /></div>
        {symbol ? <div className="min-w-0"><Detail symbol={symbol} every={every} back={() => router.push("/market")} /></div> : (
          <div className="surface hidden place-items-center p-10 text-center text-sm text-gray-500 lg:grid">Pick a stock from the watchlist for its live chart, indicators and order buttons.</div>
        )}
      </div>
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
