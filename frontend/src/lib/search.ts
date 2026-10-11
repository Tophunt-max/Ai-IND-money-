"use client";

import { useEffect, useState } from "react";

import { api } from "./api";

export interface SearchResult { symbol: string; name: string; kind: "stock" | "index" }

const cache = new Map<string, SearchResult[]>();

/** NSE symbol search (debounced, cached per query). */
export function useSymbolSearch(q: string, enabled = true, limit = 8): { results: SearchResult[]; loading: boolean } {
  const key = `${q.trim().toUpperCase()}|${limit}`;
  const [results, setResults] = useState<SearchResult[]>(cache.get(key) || []);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const hit = cache.get(key);
    if (hit) {
      setResults(hit);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const id = setTimeout(async () => {
      try {
        const r = await api<{ results: SearchResult[] }>(`/api/dashboard/market/search?q=${encodeURIComponent(q.trim())}&limit=${limit}`);
        cache.set(key, r.results);
        if (!cancelled) setResults(r.results);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
  }, [key, q, enabled, limit]);
  return { results, loading };
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
