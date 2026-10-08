"use client";

import { useEffect, useState } from "react";

// NSE session timing for the dashboard's auto refresh (holidays are not known here: on a
// holiday the page just refreshes faster than it needs to).

function istNow(): { day: number; minutes: number } {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value || "";
  const day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday"));
  return { day, minutes: Number(get("hour")) * 60 + Number(get("minute")) };
}

/** NSE cash market open: Mon–Fri 09:15–15:30 IST. */
export function marketOpen(): boolean {
  const { day, minutes } = istNow();
  return day >= 1 && day <= 5 && minutes >= 9 * 60 + 15 && minutes < 15 * 60 + 30;
}

/** Refresh period: *live* ms while NSE is open, *closed* ms otherwise. */
export function refreshEvery(live = 15000, closed = 60000): number {
  return marketOpen() ? live : closed;
}

/** Whether NSE is open, re-checked every 30 s (so refresh speeds up at 09:15). */
export function useMarketOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setOpen(marketOpen());
    const id = setInterval(() => setOpen(marketOpen()), 30000);
    return () => clearInterval(id);
  }, []);
  return open;
}

/** "5s ago", ticking every second. */
export function useAgo(at: number | null): string {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  if (!at) return "—";
  const s = Math.max(0, Math.round((Date.now() - at) / 1000));
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ago`;
}
