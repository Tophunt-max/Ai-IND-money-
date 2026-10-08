"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, BACKEND_URL } from "./api";
import { accessToken } from "./supabase";

/**
 * Server-Sent Events over fetch (EventSource cannot send the Authorization header).
 * Reconnects after an error or when the server ends the stream; while the stream is
 * down it polls `pollPath` every 5 s so the page never goes stale.
 */
export function useEventStream<T = any>(streamPath: string | null, pollPath: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const stopped = useRef(false);

  const poll = useCallback(async () => {
    if (!pollPath) return;
    try {
      setData(await api<T>(pollPath));
      setUpdatedAt(Date.now());
      setError(null);
    } catch (e: any) {
      setError(e.message || String(e));
    }
  }, [pollPath]);

  useEffect(() => {
    if (!streamPath) return;
    stopped.current = false;
    let controller: AbortController | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const startPolling = () => {
      if (pollTimer) return;
      poll();
      pollTimer = setInterval(() => !document.hidden && poll(), 5000);
    };
    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    };

    const connect = async () => {
      if (stopped.current) return;
      controller = new AbortController();
      try {
        const token = await accessToken();
        const res = await fetch(`${BACKEND_URL}${streamPath}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          if (res.status === 401) window.dispatchEvent(new Event("aiind:unauthorized"));
          throw new Error(res.status === 404 ? "The server does not have live control yet "
            + "(backend update pending)" : `Stream: HTTP ${res.status}`);
        }
        setLive(true);
        stopPolling();
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let cut;
          while ((cut = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            let event = "message";
            const lines: string[] = [];
            for (const line of block.split("\n")) {
              if (line.startsWith("event:")) event = line.slice(6).trim();
              else if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
            }
            if (!lines.length) continue;
            try {
              const parsed = JSON.parse(lines.join("\n"));
              if (event === "error") setError(parsed.error || "stream error");
              else {
                setData(parsed);
                setUpdatedAt(Date.now());
                setError(null);
              }
            } catch {
              /* a partial or non-JSON frame: ignore */
            }
          }
        }
      } catch (e: any) {
        if (stopped.current || e?.name === "AbortError") return;
        setError(e.message || String(e));
      }
      setLive(false);
      if (!stopped.current) {
        startPolling();
        retry = setTimeout(connect, 5000);
      }
    };

    connect();
    return () => {
      stopped.current = true;
      controller?.abort();
      stopPolling();
      if (retry) clearTimeout(retry);
    };
  }, [streamPath, poll]);

  return { data, error, live, updatedAt, reload: poll, setData };
}
