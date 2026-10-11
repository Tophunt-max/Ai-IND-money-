"use client";

import { useSyncExternalStore } from "react";

// Per-device preferences (Settings → Appearance / Layout / Watchlists), kept in
// localStorage and shared live between components and tabs.

export type Theme = "system" | "light" | "dark";
export type Density = "comfortable" | "compact";
export type IndicatorKey = "ema" | "sma" | "vwap" | "bb" | "rsi" | "macd" | "volume" | "patterns" | "levels";

export interface Watchlist {
  id: string;
  name: string;
  symbols: string[];
}

export interface Prefs {
  theme: Theme;
  density: Density;
  reduceMotion: boolean;
  landing: string;
  chartType: "candle" | "line";
  chartRange: string;
  indicators: IndicatorKey[];
  watchlists: Watchlist[];
  activeWatchlist: string;
  homeCards: string[];
  flashPrices: boolean;
}

export const LANDINGS = [
  { value: "/", label: "Home" }, { value: "/market", label: "Market" },
  { value: "/portfolio", label: "Portfolio" }, { value: "/ai", label: "AI insights" },
  { value: "/control", label: "Trade desk" },
] as const;

export const HOME_CARDS = [
  { value: "indices", label: "Indices" },
  { value: "portfolio", label: "Portfolio value & P&L" },
  { value: "watchlist", label: "Watchlist" },
  { value: "ideas", label: "AI swing ideas" },
  { value: "movers", label: "Top gainers & losers" },
  { value: "positions", label: "Open positions" },
  { value: "session", label: "Auto-trading session" },
  { value: "system", label: "System health" },
] as const;

export const DEFAULT_PREFS: Prefs = {
  theme: "system",
  density: "comfortable",
  reduceMotion: false,
  landing: "/",
  chartType: "candle",
  chartRange: "1d",
  indicators: ["ema", "volume", "levels"],
  watchlists: [
    { id: "main", name: "My watchlist", symbols: ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN"] },
  ],
  activeWatchlist: "main",
  homeCards: HOME_CARDS.map((c) => c.value),
  flashPrices: true,
};

const KEY = "aiind:prefs";
const LEGACY_LIST = "aiind:watchlist";
let cache: Prefs | null = null;
const listeners = new Set<() => void>();

function sanitize(raw: any): Prefs {
  const p: Prefs = { ...DEFAULT_PREFS, ...(raw && typeof raw === "object" ? raw : {}) };
  if (!["system", "light", "dark"].includes(p.theme)) p.theme = "system";
  if (!["comfortable", "compact"].includes(p.density)) p.density = "comfortable";
  if (!Array.isArray(p.indicators)) p.indicators = DEFAULT_PREFS.indicators;
  if (!Array.isArray(p.homeCards)) p.homeCards = DEFAULT_PREFS.homeCards;
  const lists = Array.isArray(p.watchlists) ? p.watchlists.filter((w) => w && typeof w.id === "string" && Array.isArray(w.symbols)) : [];
  p.watchlists = lists.length ? lists.map((w) => ({ id: w.id, name: String(w.name || "Watchlist").slice(0, 30), symbols: w.symbols.map(String).slice(0, 100) }))
    : DEFAULT_PREFS.watchlists;
  if (!p.watchlists.some((w) => w.id === p.activeWatchlist)) p.activeWatchlist = p.watchlists[0].id;
  return p;
}

export function readPrefs(): Prefs {
  if (cache) return cache;
  if (typeof window === "undefined") return DEFAULT_PREFS;
  let raw: any = null;
  try {
    raw = JSON.parse(localStorage.getItem(KEY) || "null");
  } catch {
    raw = null;
  }
  if (!raw) {
    // The old "My list" (one list) becomes the first watchlist
    try {
      const old = JSON.parse(localStorage.getItem(LEGACY_LIST) || "null");
      if (Array.isArray(old) && old.length) raw = { watchlists: [{ id: "main", name: "My watchlist", symbols: old }] };
    } catch { /* ignore */ }
  }
  cache = sanitize(raw);
  return cache;
}

export function setPrefs(patch: Partial<Prefs> | ((p: Prefs) => Partial<Prefs>)): Prefs {
  const cur = readPrefs();
  const next = sanitize({ ...cur, ...(typeof patch === "function" ? patch(cur) : patch) });
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch { /* private mode */ }
  listeners.forEach((l) => l());
  return next;
}

export function resetPrefs(): Prefs {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
  cache = null;
  cache = sanitize(null);
  listeners.forEach((l) => l());
  return cache;
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      cache = null;
      fn();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", onStorage);
  };
}

export function usePrefs(): [Prefs, typeof setPrefs] {
  const p = useSyncExternalStore(subscribe, readPrefs, () => DEFAULT_PREFS);
  return [p, setPrefs];
}

// ── Watchlists ───────────────────────────────────────────────────────────────

export function activeList(p: Prefs): Watchlist {
  return p.watchlists.find((w) => w.id === p.activeWatchlist) || p.watchlists[0];
}

export function toggleInList(symbol: string, listId?: string) {
  setPrefs((p) => ({
    watchlists: p.watchlists.map((w) => (w.id !== (listId || p.activeWatchlist) ? w
      : { ...w, symbols: w.symbols.includes(symbol) ? w.symbols.filter((s) => s !== symbol) : [...w.symbols, symbol].slice(0, 100) })),
  }));
}

export function inAnyList(p: Prefs, symbol: string): boolean {
  return p.watchlists.some((w) => w.symbols.includes(symbol));
}

export function moveInList(listId: string, symbol: string, dir: -1 | 1) {
  setPrefs((p) => ({
    watchlists: p.watchlists.map((w) => {
      if (w.id !== listId) return w;
      const i = w.symbols.indexOf(symbol);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= w.symbols.length) return w;
      const s = [...w.symbols];
      [s[i], s[j]] = [s[j], s[i]];
      return { ...w, symbols: s };
    }),
  }));
}

export function addList(name: string): string {
  const id = `wl-${Date.now().toString(36)}`;
  setPrefs((p) => ({ watchlists: [...p.watchlists, { id, name: name.trim().slice(0, 30) || "Watchlist", symbols: [] }].slice(0, 10), activeWatchlist: id }));
  return id;
}

export function renameList(id: string, name: string) {
  setPrefs((p) => ({ watchlists: p.watchlists.map((w) => (w.id === id ? { ...w, name: name.trim().slice(0, 30) || w.name } : w)) }));
}

export function removeList(id: string) {
  setPrefs((p) => ({ watchlists: p.watchlists.length > 1 ? p.watchlists.filter((w) => w.id !== id) : p.watchlists }));
}

/** The theme to paint: "light" or "dark" (system follows the OS). */
export function resolveTheme(t: Theme): "light" | "dark" {
  if (t !== "system") return t;
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Inline script for <head>: paint the right theme before React loads (no flash). */
export const THEME_BOOT = `(function(){try{var p=JSON.parse(localStorage.getItem("${KEY}")||"{}");var t=p.theme||"system";` +
  `if(t==="system")t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";` +
  `var c=document.documentElement.classList;if(t==="dark")c.add("dark");else c.remove("dark");` +
  `if(p.density==="compact")c.add("density-compact");}catch(e){}})();`;
