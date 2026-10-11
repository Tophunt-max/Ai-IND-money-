"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, BACKEND_URL } from "./api";
import { marketOpen } from "./market";
import { accessToken } from "./supabase";

// Live prices for every symbol on screen through ONE server stream
// (GET /api/dashboard/market/stream, Server-Sent Events): components subscribe with
// useQuotes([...]); the provider streams the union (≤ 50 symbols) and falls back to polling
// /market/quotes while the stream is down. Price alerts the stream fires are handed to
// onAlert (toasts, the notification bell).

export interface Quote {
  symbol: string;
  ltp: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prev_close?: number | null;
  change?: number | null;
  change_pct?: number | null;
  volume?: number;
  as_of?: string;
  source?: "indstocks" | "yahoo";
}

export interface FiredAlert { id: string; symbol: string; op: string; value: number; triggered_price: number; note?: string }

export type LiveState = "live" | "polling" | "idle" | "offline";

interface Ctx {
  quotes: Record<string, Quote>;
  state: LiveState;
  updatedAt: number | null;
  subscribe: (symbols: string[]) => () => void;
}

const LiveContext = createContext<Ctx>({ quotes: {}, state: "idle", updatedAt: null, subscribe: () => () => {} });
export const MAX_LIVE = 50;

/** Parses an SSE byte stream into (event, data) frames. Exported for tests. */
export function parseFrames(buffer: string): { frames: { event: string; data: string }[]; rest: string } {
  const frames: { event: string; data: string }[] = [];
  let rest = buffer;
  let cut;
  while ((cut = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, cut);
    rest = rest.slice(cut + 2);
    let event = "message";
    const lines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
    }
    if (lines.length) frames.push({ event, data: lines.join("\n") });
  }
  return { frames, rest };
}

export function LiveQuotesProvider({ children, enabled, onAlert }: {
  children: ReactNode; enabled: boolean; onAlert?: (a: FiredAlert[]) => void;
}) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [state, setState] = useState<LiveState>("idle");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const counts = useRef(new Map<string, number>());
  const [key, setKey] = useState("");
  const onAlertRef = useRef(onAlert);
  onAlertRef.current = onAlert;
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recompute = useCallback(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      const syms = Array.from(counts.current.entries()).filter(([, n]) => n > 0).map(([s]) => s).sort().slice(0, MAX_LIVE);
      setKey(syms.join(","));
    }, 250);
  }, []);

  const subscribe = useCallback((symbols: string[]) => {
    const list = Array.from(new Set(symbols.filter(Boolean).map((s) => s.toUpperCase())));
    list.forEach((s) => counts.current.set(s, (counts.current.get(s) || 0) + 1));
    recompute();
    return () => {
      list.forEach((s) => counts.current.set(s, Math.max(0, (counts.current.get(s) || 1) - 1)));
      recompute();
    };
  }, [recompute]);

  const merge = useCallback((q: Record<string, Quote>) => {
    if (!q || !Object.keys(q).length) return;
    setQuotes((prev) => ({ ...prev, ...q }));
    setUpdatedAt(Date.now());
  }, []);

  useEffect(() => {
    if (!enabled || !key) {
      setState("idle");
      return;
    }
    let stopped = false;
    let controller: AbortController | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const syms = key.split(",");

    const pollOnce = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const chunks = [syms.slice(0, 25), syms.slice(25, 50)].filter((c) => c.length);
        const res = await Promise.all(chunks.map((c) => api<{ quotes: Record<string, Quote> }>(
          `/api/dashboard/market/quotes?symbols=${encodeURIComponent(c.join(","))}`)));
        res.forEach((r) => merge(r.quotes));
        if (!stopped) setState((s) => (s === "live" ? s : "polling"));
      } catch {
        if (!stopped) setState((s) => (s === "live" ? s : "offline"));
      }
    };
    const startPolling = () => {
      if (poll) return;
      pollOnce();
      poll = setInterval(pollOnce, marketOpen() ? 5000 : 60000);
    };
    const stopPolling = () => {
      if (poll) clearInterval(poll);
      poll = null;
    };

    const connect = async () => {
      if (stopped) return;
      controller = new AbortController();
      try {
        const token = await accessToken();
        const res = await fetch(`${BACKEND_URL}/api/dashboard/market/stream?symbols=${encodeURIComponent(key)}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        setState("live");
        stopPolling();
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseFrames(buffer);
          buffer = rest;
          for (const f of frames) {
            if (f.event !== "message") continue;
            try {
              const d = JSON.parse(f.data);
              merge(d.quotes || {});
              if (Array.isArray(d.alerts) && d.alerts.length) onAlertRef.current?.(d.alerts);
            } catch { /* partial frame */ }
          }
        }
      } catch (e: any) {
        if (stopped || e?.name === "AbortError") return;
      }
      if (!stopped) {
        setState("polling");
        startPolling();
        retry = setTimeout(connect, 5000);
      }
    };

    connect();
    return () => {
      stopped = true;
      controller?.abort();
      stopPolling();
      if (retry) clearTimeout(retry);
    };
  }, [enabled, key, merge]);

  const value = useMemo(() => ({ quotes, state, updatedAt, subscribe }), [quotes, state, updatedAt, subscribe]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

/** Live quotes for *symbols* (kept streaming while the component is mounted). */
export function useQuotes(symbols: string[]): { quotes: Record<string, Quote>; state: LiveState; updatedAt: number | null } {
  const ctx = useContext(LiveContext);
  const sig = symbols.map((s) => s.toUpperCase()).join(",");
  const { subscribe } = ctx;
  useEffect(() => (sig ? subscribe(sig.split(",")) : undefined), [sig, subscribe]);
  return { quotes: ctx.quotes, state: ctx.state, updatedAt: ctx.updatedAt };
}

export function useQuote(symbol: string | null | undefined): Quote | undefined {
  const { quotes } = useQuotes(symbol ? [symbol] : []);
  return symbol ? quotes[symbol.toUpperCase()] : undefined;
}

export function useLiveState() {
  const ctx = useContext(LiveContext);
  return { state: ctx.state, updatedAt: ctx.updatedAt };
}
