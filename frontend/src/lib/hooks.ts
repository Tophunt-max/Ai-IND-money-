"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";

/** GET a backend path; `reload()` fetches again. `intervalMs` refreshes in the background. */
export function useApi<T = any>(path: string | null, intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const inFlight = useRef<string | null>(null);  // the path being fetched
  const current = useRef(path);
  current.current = path;

  const reload = useCallback(async () => {
    if (!path || inFlight.current === path) return;
    inFlight.current = path;
    setLoading(true);
    try {
      const result = await api<T>(path);
      if (current.current !== path) return;  // an answer for a path we left
      setData(result);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e: any) {
      if (current.current === path) setError(e.message || String(e));
    } finally {
      if (inFlight.current === path) inFlight.current = null;
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    reload();
    if (!intervalMs) return;
    // Background refresh only while the tab is visible; catch up when it comes back
    const tick = () => {
      if (typeof document === "undefined" || !document.hidden) reload();
    };
    const id = setInterval(tick, intervalMs);
    const onVisible = () => !document.hidden && reload();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload, intervalMs]);

  return { data, error, loading, reload, setData, updatedAt };
}

export interface Job {
  id: string;
  kind: "analyze" | "trade" | "scan" | "backtest" | "montecarlo" | "settle";
  symbol: string;
  by?: string;
  params?: { days: number; stop_loss_pct: number; target_pct: number; simulations: number };
  status: "queued" | "running" | "done" | "failed";
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  result: any;
  error: string | null;
}

/** Start an analyze/scan job and poll it every 3 s until it finishes. */
export function useJob() {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  };

  const watch = useCallback((id: string) => {
    stop();
    timer.current = setInterval(async () => {
      try {
        const j = await api<Job>(`/api/dashboard/jobs/${id}`);
        setJob(j);
        if (j.status === "done" || j.status === "failed") stop();
      } catch (e: any) {
        setError(e.message);
        stop();
      }
    }, 3000);
  }, []);

  const start = useCallback(
    async (body: Record<string, unknown>) => {
      setError(null);
      try {
        const j = await api<Job>("/api/dashboard/jobs", { method: "POST", json: body });
        setJob(j);
        watch(j.id);
      } catch (e: any) {
        setError(e.message);
      }
    },
    [watch],
  );

  const load = useCallback(
    async (id: string) => {
      try {
        const j = await api<Job>(`/api/dashboard/jobs/${id}`);
        setJob(j);
        if (j.status === "queued" || j.status === "running") watch(id);
      } catch (e: any) {
        setError(e.message);
      }
    },
    [watch],
  );

  useEffect(() => stop, []);

  const running = !!job && (job.status === "queued" || job.status === "running");
  return { job, error, running, start, load };
}
