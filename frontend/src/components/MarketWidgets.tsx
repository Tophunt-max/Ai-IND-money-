"use client";

import { ArrowDownRight, ArrowUpRight, ChevronDown, ChevronUp, Flame, Plus, Search, Star, TrendingDown, TrendingUp, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useApi } from "@/lib/hooks";
import { useQuotes } from "@/lib/live";
import { useMarketOpen } from "@/lib/market";
import { activeList, addList, inAnyList, moveInList, removeList, setPrefs, toggleInList, usePrefs } from "@/lib/prefs";

import { useAuth } from "./AuthGate";
import { useOrderTicket } from "./OrderTicket";
import { Change, cx, Empty, Flash, Segmented, Skeleton, SymbolAvatar, Tabs } from "./ui";

export interface HeatStock { symbol: string; sector: string; ltp: number | null; change_pct: number | null; change: number | null; volume: number | null; turnover: number }
export interface Heatmap { stocks: HeatStock[]; sectors: { sector: string; change_pct: number; count: number }[]; advances: number; declines: number; source: string }

export const sectorName = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\bFmcg\b/, "FMCG").replace(/\bNbfc\b/, "NBFC").replace(/\bIt\b/, "IT").replace(/\bOil Gas\b/, "Oil & Gas");

export function useHeatmap() {
  const open = useMarketOpen();
  return useApi<Heatmap>("/api/dashboard/market/heatmap", open ? 60000 : 600000);
}

const fmt = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));

interface IndexQuote { name: string; symbol: string; ltp: number | null; change_pct: number | null; change?: number | null }

/** NIFTY 50 / Bank NIFTY / India VIX cards with live prices. */
export function IndexCards({ active }: { active?: string }) {
  const idx = useApi<{ indices: IndexQuote[] }>("/api/dashboard/market/indices", 300000);
  const rows = idx.data?.indices || [];
  const { quotes } = useQuotes(rows.map((r) => r.symbol));
  return (
    <div className="no-scrollbar -mx-3 flex gap-3 overflow-x-auto px-3 sm:mx-0 sm:grid sm:grid-cols-3 sm:px-0">
      {rows.map((i) => {
        const q = quotes[i.symbol];
        const ltp = q?.ltp ?? i.ltp;
        const pct = q?.change_pct ?? i.change_pct;
        return (
          <Link key={i.name} href={`/market?symbol=${encodeURIComponent(i.symbol)}`}
            className={cx("surface min-w-[168px] shrink-0 p-4 transition hover:-translate-y-0.5 hover:shadow-pop", active === i.symbol && "ring-2 ring-brand-500/40")}>
            <div className="text-xs font-medium text-gray-500">{i.name}</div>
            <div className="num mt-1 text-lg font-bold text-white"><Flash value={ltp}>{fmt(ltp)}</Flash></div>
            <Change value={pct} abs={q?.change ?? i.change ?? null} size="xs" pill={false} />
          </Link>
        );
      })}
      {idx.loading && !idx.data && [0, 1, 2].map((i) => <Skeleton key={i} className="h-24 min-w-[168px]" />)}
    </div>
  );
}

/** One stock row: avatar, name, live price and change; quick buy / sell. */
export function StockRow({ symbol, sub, quote, onClick, right }: {
  symbol: string; sub?: string; quote?: { ltp: number | null; change_pct?: number | null; change?: number | null }; onClick?: () => void; right?: React.ReactNode;
}) {
  const [p] = usePrefs();
  const content = (
    <>
      <SymbolAvatar symbol={symbol} size={40} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-white">{symbol.replace("^", "")}</div>
        <div className="truncate text-xs text-gray-500">{sub || "NSE"}</div>
      </div>
      <div className="num text-right">
        <div className="text-sm font-semibold text-white">{p.flashPrices ? <Flash value={quote?.ltp}>{quote?.ltp != null ? fmt(quote.ltp) : "…"}</Flash> : fmt(quote?.ltp)}</div>
        <Change value={quote?.change_pct ?? null} abs={quote?.change ?? null} size="xs" pill={false} />
      </div>
      {right}
    </>
  );
  return onClick ? (
    <button type="button" onClick={onClick} className="group flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-white/[0.03] active:bg-white/[0.05]">{content}</button>
  ) : <div className="group flex items-center gap-3 px-4 py-3">{content}</div>;
}

const SORTS = [{ value: "mine", label: "My order" }, { value: "gain", label: "Gainers" }, { value: "lose", label: "Losers" }, { value: "az", label: "A–Z" }] as const;

/** The watchlists (yours, kept on this device) and NIFTY 50, live. */
export function Watchlist({ go, compact = false }: { go: (s: string) => void; compact?: boolean }) {
  const [prefs] = usePrefs();
  const { isAdmin } = useAuth();
  const ticket = useOrderTicket();
  const nifty = useApi<{ symbols: string[] }>("/api/dashboard/market/watchlist");
  const [tab, setTab] = useState<string>(prefs.activeWatchlist);
  const [sort, setSort] = useState<(typeof SORTS)[number]["value"]>("mine");
  const [filter, setFilter] = useState("");
  const [edit, setEdit] = useState(false);
  const list = tab === "nifty" ? null : prefs.watchlists.find((w) => w.id === tab) || activeList(prefs);
  const symbols = useMemo(() => (list ? list.symbols : nifty.data?.symbols || []), [list, nifty.data]);
  const { quotes, state } = useQuotes(symbols);
  const rows = useMemo(() => {
    const r = symbols.filter((s) => !filter || s.includes(filter.toUpperCase()));
    if (sort === "gain") r.sort((a, b) => (quotes[b]?.change_pct ?? -9) - (quotes[a]?.change_pct ?? -9));
    else if (sort === "lose") r.sort((a, b) => (quotes[a]?.change_pct ?? 9) - (quotes[b]?.change_pct ?? 9));
    else if (sort === "az" || !list) r.sort((a, b) => a.localeCompare(b));
    return r;
  }, [symbols, quotes, sort, filter, list]);
  const [adding, setAdding] = useState("");

  const tabs = [...prefs.watchlists.map((w) => ({ value: w.id, label: w.name, count: w.symbols.length })), { value: "nifty", label: "NIFTY 50" }];
  return (
    <section className="surface overflow-hidden">
      <div className="px-4 pt-3">
        <div className="flex items-center justify-between gap-2">
          <Tabs options={tabs} value={tab} onChange={(v) => { setTab(v); if (v !== "nifty") setPrefs({ activeWatchlist: v }); }} className="min-w-0 flex-1 border-b-0" />
          <button type="button" aria-label="New watchlist" title="New watchlist" onClick={() => { const name = prompt("Watchlist name:"); if (name) setTab(addList(name)); }}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-gray-500 hover:bg-white/[0.06] hover:text-white"><Plus className="h-4 w-4" /></button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-y border-white/[0.06] px-4 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Search className="h-4 w-4 shrink-0 text-gray-500" />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter"
            className="w-full min-w-0 bg-transparent text-sm text-gray-100 outline-none placeholder:text-gray-500" />
        </div>
        {!compact && <Segmented size="sm" value={sort} onChange={setSort} options={list ? SORTS : SORTS.slice(1)} />}
        {list && <button type="button" onClick={() => setEdit(!edit)} className={cx("text-xs font-semibold", edit ? "text-brand-400" : "text-gray-500 hover:text-white")}>{edit ? "Done" : "Edit"}</button>}
      </div>
      {list && edit && (
        <form className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-2.5"
          onSubmit={(e) => { e.preventDefault(); const s = adding.trim().toUpperCase(); if (s && !list.symbols.includes(s)) toggleInList(s, list.id); setAdding(""); }}>
          <input value={adding} onChange={(e) => setAdding(e.target.value.toUpperCase())} placeholder="Add a symbol, e.g. ITC" className="field h-9" />
          <button type="submit" className="link shrink-0 text-sm font-semibold">Add</button>
          {prefs.watchlists.length > 1 && (
            <button type="button" className="shrink-0 text-xs font-semibold text-rose-400" onClick={() => { if (confirm(`Delete the list "${list.name}"?`)) { removeList(list.id); setTab(prefs.watchlists[0].id); } }}>Delete list</button>
          )}
        </form>
      )}
      {list && list.symbols.length === 0 ? (
        <Empty icon={Star} title="This list is empty">Tap the star on a stock, or Edit, then Add.</Empty>
      ) : (
        <ul className={cx("divide-y divide-white/[0.05]", compact && "max-h-[420px] overflow-y-auto")}>
          {rows.map((s) => (
            <li key={s} className="relative">
              <StockRow symbol={s} quote={quotes[s]} onClick={edit ? undefined : () => go(s)}
                right={edit && list ? (
                  <div className="flex items-center gap-0.5">
                    <button type="button" aria-label="Up" onClick={() => moveInList(list.id, s, -1)} className="rounded p-1 text-gray-500 hover:text-white"><ChevronUp className="h-4 w-4" /></button>
                    <button type="button" aria-label="Down" onClick={() => moveInList(list.id, s, 1)} className="rounded p-1 text-gray-500 hover:text-white"><ChevronDown className="h-4 w-4" /></button>
                    <button type="button" aria-label="Remove" onClick={() => toggleInList(s, list.id)} className="rounded p-1 text-rose-400"><X className="h-4 w-4" /></button>
                  </div>
                ) : isAdmin && !s.startsWith("^") ? (
                  <div className="absolute right-3 top-1/2 hidden -translate-y-1/2 gap-1 group-hover:flex">
                    <span role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); ticket({ symbol: s, side: "BUY" }); }} className="rounded-lg bg-emerald-500 px-2.5 py-1 text-xs font-bold text-onaccent">B</span>
                    <span role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); ticket({ symbol: s, side: "SELL" }); }} className="rounded-lg bg-rose-500 px-2.5 py-1 text-xs font-bold text-onaccent">S</span>
                  </div>
                ) : null} />
            </li>
          ))}
          {nifty.loading && !list && !rows.length && [0, 1, 2, 3].map((i) => <li key={i} className="p-4"><Skeleton className="h-10" /></li>)}
        </ul>
      )}
      <div className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2 text-[11px] text-gray-500">
        <span>{rows.length} stocks</span>
        <span className="flex items-center gap-1.5"><span className={cx("h-1.5 w-1.5 rounded-full", state === "live" ? "animate-pulse bg-emerald-400" : state === "polling" ? "bg-amber-400" : "bg-gray-500")} />
          {state === "live" ? "Streaming" : state === "polling" ? "Refreshing" : state === "offline" ? "Offline" : "Idle"}</span>
      </div>
    </section>
  );
}

export function StarButton({ symbol }: { symbol: string }) {
  const [p] = usePrefs();
  const on = inAnyList(p, symbol);
  return (
    <button type="button" onClick={() => toggleInList(symbol)} aria-label={on ? "Remove from watchlist" : "Add to watchlist"} title={on ? "In your watchlist" : "Add to watchlist"}
      className={cx("grid h-10 w-10 place-items-center rounded-xl transition", on ? "bg-amber-500/10 text-amber-400" : "text-gray-500 hover:bg-white/[0.06] hover:text-white")}>
      <Star className="h-5 w-5" fill={on ? "currentColor" : "none"} />
    </button>
  );
}

/** Top gainers, losers and most traded of NIFTY 50 (live). */
export function Movers({ go, limit = 5 }: { go: (s: string) => void; limit?: number }) {
  const heat = useHeatmap();
  const stocks = heat.data?.stocks || [];
  const { quotes } = useQuotes(stocks.map((s) => s.symbol));
  const [tab, setTab] = useState<"gain" | "lose" | "active">("gain");
  const merged = stocks.map((s) => ({ ...s, ...(quotes[s.symbol] ? { ltp: quotes[s.symbol].ltp, change_pct: quotes[s.symbol].change_pct ?? s.change_pct, change: quotes[s.symbol].change ?? s.change } : {}) }))
    .filter((s) => s.change_pct != null);
  const rows = [...merged].sort((a, b) => tab === "gain" ? (b.change_pct! - a.change_pct!) : tab === "lose" ? (a.change_pct! - b.change_pct!) : (b.turnover - a.turnover)).slice(0, limit);
  return (
    <section className="surface overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 pt-4">
        <h3 className="text-[15px] font-semibold text-white">Top movers <span className="text-xs font-normal text-gray-500">NIFTY 50</span></h3>
        {heat.data && <span className="num text-xs text-gray-500"><span className="text-emerald-400">{heat.data.advances} up</span> · <span className="text-rose-400">{heat.data.declines} down</span></span>}
      </div>
      <div className="px-4 pt-2">
        <Tabs value={tab} onChange={setTab} options={[
          { value: "gain", label: <span className="inline-flex items-center gap-1"><TrendingUp className="h-4 w-4" /> Gainers</span> },
          { value: "lose", label: <span className="inline-flex items-center gap-1"><TrendingDown className="h-4 w-4" /> Losers</span> },
          { value: "active", label: <span className="inline-flex items-center gap-1"><Flame className="h-4 w-4" /> Most traded</span> },
        ]} />
      </div>
      {heat.error ? <p className="p-4 text-sm text-rose-300">{heat.error}</p> : !heat.data ? (
        <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
      ) : rows.length === 0 ? <Empty>No prices yet.</Empty> : (
        <ul className="divide-y divide-white/[0.05]">
          {rows.map((s) => <li key={s.symbol}><StockRow symbol={s.symbol} sub={sectorName(s.sector)} quote={s} onClick={() => go(s.symbol)} /></li>)}
        </ul>
      )}
    </section>
  );
}

/** Sector averages as horizontal bars. */
export function SectorBars({ sectors }: { sectors: Heatmap["sectors"] }) {
  const max = Math.max(0.001, ...sectors.map((s) => Math.abs(s.change_pct)));
  return (
    <ul className="space-y-2">
      {sectors.map((s) => {
        const up = s.change_pct >= 0;
        const w = (Math.abs(s.change_pct) / max) * 50;
        return (
          <li key={s.sector} className="grid grid-cols-[110px_1fr_60px] items-center gap-2 text-xs sm:grid-cols-[140px_1fr_64px]">
            <span className="truncate text-gray-400">{sectorName(s.sector)}</span>
            <div className="relative h-2.5 rounded-full bg-white/[0.05]">
              <span className="absolute inset-y-0 left-1/2 w-px bg-white/[0.15]" />
              <span className={cx("absolute inset-y-0 rounded-full", up ? "bg-emerald-500" : "bg-rose-500")}
                style={up ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
            </div>
            <span className={cx("num inline-flex items-center justify-end gap-0.5 font-semibold", up ? "text-emerald-400" : "text-rose-400")}>
              {up ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}{(Math.abs(s.change_pct) * 100).toFixed(2)}%
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Heat colour of a day change (fraction): green up, red down, deeper with size. */
export function heatColor(pct: number | null | undefined): string {
  if (pct == null) return "rgb(148 155 168 / 0.25)";
  const a = Math.min(1, Math.abs(pct) / 0.03);
  const alpha = 0.18 + a * 0.72;
  return pct >= 0 ? `rgb(0 179 134 / ${alpha.toFixed(2)})` : `rgb(235 91 60 / ${alpha.toFixed(2)})`;
}
