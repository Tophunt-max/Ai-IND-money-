"use client";

import { useMemo, useState } from "react";

import { inr } from "@/lib/api";

import { cx } from "./ui";

export interface DayCell { date: string; pnl: number | null; weekday: number }

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Weeks (Mon–Fri columns) ending today with each day's realized P&L (null: no trades). */
export function buildCalendar(points: { date: string; pnl: number }[], weeks = 26, today = new Date()): DayCell[][] {
  const by = new Map(points.map((p) => [p.date, p.pnl]));
  const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const dow = (end.getUTCDay() + 6) % 7; // Monday = 0
  const start = new Date(end);
  start.setUTCDate(end.getUTCDate() - dow - 7 * (weeks - 1));
  const cols: DayCell[][] = [];
  for (let w = 0; w < weeks; w++) {
    const col: DayCell[] = [];
    for (let d = 0; d < 5; d++) {
      const day = new Date(start);
      day.setUTCDate(start.getUTCDate() + w * 7 + d);
      if (day > end) break;
      const k = iso(day);
      col.push({ date: k, pnl: by.has(k) ? (by.get(k) as number) : null, weekday: d });
    }
    cols.push(col);
  }
  return cols;
}

function color(pnl: number | null, scale: number): string {
  if (pnl == null) return "";
  if (pnl === 0) return "rgb(148 155 168 / 0.35)";
  const a = 0.25 + Math.min(1, Math.abs(pnl) / scale) * 0.75;
  return pnl > 0 ? `rgb(0 179 134 / ${a.toFixed(2)})` : `rgb(235 91 60 / ${a.toFixed(2)})`;
}

/** A GitHub-style calendar of daily realized P&L. */
export default function PnlCalendar({ points, weeks = 26 }: { points: { date: string; pnl: number }[]; weeks?: number }) {
  const cols = useMemo(() => buildCalendar(points, weeks), [points, weeks]);
  const scale = Math.max(1, ...points.map((p) => Math.abs(p.pnl)));
  const [hover, setHover] = useState<DayCell | null>(null);
  const months = cols.map((c, i) => {
    const m = c[0] ? new Date(c[0].date).toLocaleDateString("en-IN", { month: "short" }) : "";
    const prev = i > 0 && cols[i - 1][0] ? new Date(cols[i - 1][0].date).toLocaleDateString("en-IN", { month: "short" }) : "";
    return m !== prev ? m : "";
  });
  return (
    <div>
      <div className="num mb-2 min-h-5 text-xs text-gray-500">
        {hover ? <>{new Date(hover.date).toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}: {hover.pnl == null ? "no closed trades" : <b className={hover.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>{inr(hover.pnl)}</b>}</> : "Tap or hover a day"}
      </div>
      <div className="no-scrollbar overflow-x-auto">
        <div className="inline-flex gap-1">
          <div className="mr-1 flex flex-col gap-1 pt-4 text-[10px] text-gray-500">
            {["M", "T", "W", "T", "F"].map((d, i) => <span key={i} className="flex h-3.5 items-center">{d}</span>)}
          </div>
          {cols.map((c, i) => (
            <div key={i} className="flex flex-col gap-1">
              <span className="h-3 text-[10px] text-gray-500">{months[i]}</span>
              {c.map((d) => (
                <button key={d.date} type="button" aria-label={d.date} onMouseEnter={() => setHover(d)} onClick={() => setHover(d)}
                  className={cx("h-3.5 w-3.5 rounded-[4px] transition hover:ring-2 hover:ring-brand-500/50", d.pnl == null && "bg-white/[0.06]")}
                  style={d.pnl != null ? { background: color(d.pnl, scale) } : undefined} />
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 flex items-center justify-end gap-1.5 text-[10px] text-gray-500">
        Loss <span className="h-3 w-3 rounded-[3px]" style={{ background: color(-scale, scale) }} /><span className="h-3 w-3 rounded-[3px]" style={{ background: color(-scale / 3, scale) }} />
        <span className="h-3 w-3 rounded-[3px] bg-white/[0.06]" /><span className="h-3 w-3 rounded-[3px]" style={{ background: color(scale / 3, scale) }} /><span className="h-3 w-3 rounded-[3px]" style={{ background: color(scale, scale) }} /> Profit
      </div>
    </div>
  );
}
