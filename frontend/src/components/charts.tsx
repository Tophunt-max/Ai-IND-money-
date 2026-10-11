"use client";

/**
 * Dependency-free SVG charts in the app theme (light or dark). All are responsive (they measure their
 * box), work with touch (tap / drag shows the value) and with a mouse.
 *
 * - ``EquityChart``: an equity or cumulative-P&L line with its drawdown pane underneath.
 * - ``BarChart``: signed bars (daily P&L, returns) or a histogram.
 * - ``Donut``: shares of a whole (signal statuses, wins / losses).
 * - ``Sparkline``: a tiny trend line for list rows.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useThemeColors } from "@/lib/themeColors";

// Static fallbacks (tests, legends); the charts themselves use the theme's colours
export const UP = "#00b386";
export const DOWN = "#eb5b3c";
export const ACCENT = "#5367ff";

function useWidth(min = 240) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(320);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  return { ref, w };
}

const fmtDefault = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 });

function niceTicks(min: number, max: number, n = 4): number[] {
  if (!(max > min)) return [min];
  return Array.from({ length: n + 1 }, (_, i) => min + ((max - min) * i) / n);
}

// ── Equity with drawdown ─────────────────────────────────────────────────────

export interface SeriesPoint { label: string; v: number }

/** Running drawdown of a series (≤ 0), absolute and % of the peak. */
export function drawdowns(values: number[], base = 0): { abs: number[]; pct: number[]; max: number; maxPct: number } {
  let peak = -Infinity;
  const abs: number[] = [];
  const pct: number[] = [];
  for (const v of values) {
    peak = Math.max(peak, v);
    abs.push(v - peak);
    const ref = peak + base;
    pct.push(ref > 0 ? (v - peak) / ref : 0);
  }
  return { abs, pct, max: Math.min(0, ...abs), maxPct: Math.min(0, ...pct) };
}

export function EquityChart({
  points, height = 220, format = fmtDefault, baseline, drawdown = true, base = 0, title,
}: {
  points: SeriesPoint[];
  height?: number;
  format?: (v: number) => string;
  /** a dashed reference line (0 for cumulative P&L, the start for equity) */
  baseline?: number;
  /** show the drawdown pane */
  drawdown?: boolean;
  /** capital the series is measured on, for drawdown % of cumulative P&L */
  base?: number;
  title?: ReactNode;
}) {
  const { ref, w } = useWidth();
  const t = useThemeColors();
  const GRID = t.grid;
  const [hover, setHover] = useState<number | null>(null);
  const ddH = drawdown ? Math.round(height * 0.28) : 0;
  const mainH = height - ddH - (drawdown ? 10 : 0);
  const pad = { l: 4, r: 58, t: 8, b: 4 };
  const vals = points.map((p) => p.v);
  const dd = useMemo(() => drawdowns(vals, base), [vals.join(","), base]); // eslint-disable-line react-hooks/exhaustive-deps

  if (points.length < 2) return <div className="py-8 text-center text-sm text-gray-500">Not enough data for a chart.</div>;

  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (baseline !== undefined) { min = Math.min(min, baseline); max = Math.max(max, baseline); }
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  min -= span * 0.05; max += span * 0.05;
  const pw = w - pad.l - pad.r;
  const x = (i: number) => pad.l + (i / (points.length - 1)) * pw;
  const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (mainH - pad.t - pad.b);
  const ddMin = Math.min(-1e-9, dd.max);
  const ddTop = mainH + 10;
  const yd = (v: number) => ddTop + (v / ddMin) * (ddH - 2);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const ref0 = baseline ?? points[0].v;
  const last = points[points.length - 1].v;
  const col = last >= ref0 ? t.up : t.down;
  const area = `${line} L${x(points.length - 1).toFixed(1)},${mainH - pad.b} L${x(0).toFixed(1)},${mainH - pad.b} Z`;
  const ddArea = `M${x(0)},${ddTop} ` + dd.abs.map((v, i) => `L${x(i).toFixed(1)},${yd(v).toFixed(1)}`).join(" ")
    + ` L${x(points.length - 1)},${ddTop} Z`;
  const gid = `eq-${last >= ref0 ? "up" : "down"}-${t.dark ? "d" : "l"}`;

  const move = (clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const i = Math.round(((clientX - r.left - pad.l) / pw) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };
  const h = hover !== null ? hover : null;
  const shown = h ?? points.length - 1;
  const xt = [0, Math.floor((points.length - 1) / 2), points.length - 1];

  return (
    <div>
      <div className="num mb-1.5 flex min-h-5 flex-wrap items-baseline justify-between gap-x-3 text-[11px] text-gray-500">
        <span>{title ?? points[shown].label}{title ? ` · ${points[shown].label}` : ""}</span>
        <span className="flex gap-3">
          <span>Value <b className="font-semibold text-gray-100">{format(points[shown].v)}</b></span>
          {drawdown && <span>DD <b className="font-semibold text-rose-300">{format(dd.abs[shown])}</b>{base > 0 || dd.pct[shown] ? ` (${(dd.pct[shown] * 100).toFixed(1)}%)` : ""}</span>}
        </span>
      </div>
      <div ref={ref} className="relative w-full min-w-0 touch-pan-y select-none overflow-hidden"
        onMouseMove={(e) => move(e.clientX)} onMouseLeave={() => setHover(null)}
        onTouchStart={(e) => move(e.touches[0].clientX)} onTouchMove={(e) => move(e.touches[0].clientX)}
        onTouchEnd={() => setHover(null)}>
        <svg width={w} height={height} className="block" role="img" aria-label="equity chart">
          <defs>
            <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity="0.25" />
              <stop offset="100%" stopColor={col} stopOpacity="0" />
            </linearGradient>
          </defs>
          {niceTicks(min, max).map((v, i) => (
            <line key={i} x1={pad.l} x2={w - pad.r} y1={y(v)} y2={y(v)} stroke={GRID} />
          ))}
          {baseline !== undefined && (
            <line x1={pad.l} x2={w - pad.r} y1={y(baseline)} y2={y(baseline)} stroke={t.axis} strokeDasharray="4 4" />
          )}
          <path d={area} fill={`url(#${gid})`} />
          <path d={line} fill="none" stroke={col} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {drawdown && (
            <>
              <line x1={pad.l} x2={w - pad.r} y1={ddTop} y2={ddTop} stroke={GRID} />
              <path d={ddArea} fill={t.down} fillOpacity={0.22} stroke={t.down} strokeOpacity={0.7} strokeWidth={1} />
            </>
          )}
          {h !== null && (
            <>
              <line x1={x(h)} x2={x(h)} y1={pad.t} y2={height} stroke={t.axis} strokeDasharray="3 3" />
              <circle cx={x(h)} cy={y(points[h].v)} r={4} fill={col} stroke={t.surface} strokeWidth={2} />
            </>
          )}
        </svg>
        {niceTicks(min, max, 3).map((v, i) => (
          <span key={i} className="num pointer-events-none absolute right-0 -translate-y-1/2 pr-1 text-[10px] text-gray-500" style={{ top: y(v) }}>{format(v)}</span>
        ))}
        {drawdown && (
          <span className="num pointer-events-none absolute right-0 pr-1 text-[10px] text-rose-300/80" style={{ top: ddTop + ddH - 14 }}>
            {format(dd.max)}
          </span>
        )}
      </div>
      <div className="num mt-1 flex justify-between pr-14 text-[10px] text-gray-500">
        {Array.from(new Set(xt)).map((i) => <span key={i}>{points[i].label}</span>)}
      </div>
    </div>
  );
}

// ── Bars ─────────────────────────────────────────────────────────────────────

export interface Bar { label: string; v: number; tone?: "up" | "down" | "accent"; hint?: string }

export function BarChart({
  bars, height = 160, format = fmtDefault, labelEvery, zeroLine = true,
}: {
  bars: Bar[];
  height?: number;
  format?: (v: number) => string;
  /** show every n-th x label (default: about 6 labels) */
  labelEvery?: number;
  zeroLine?: boolean;
}) {
  const { ref, w } = useWidth();
  const t = useThemeColors();
  const GRID = t.grid;
  const [hover, setHover] = useState<number | null>(null);
  if (!bars.length) return <div className="py-8 text-center text-sm text-gray-500">No data yet.</div>;
  const pad = { l: 2, r: 52, t: 6, b: 2 };
  const vs = bars.map((b) => b.v);
  const max = Math.max(0, ...vs);
  const min = Math.min(0, ...vs);
  const span = max - min || 1;
  const pw = w - pad.l - pad.r;
  const step = pw / bars.length;
  const bw = Math.max(1, Math.min(28, step * 0.72));
  const y = (v: number) => pad.t + (1 - (v - min) / span) * (height - pad.t - pad.b);
  const zero = y(0);
  const every = labelEvery ?? Math.max(1, Math.ceil(bars.length / 6));
  const move = (clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const i = Math.floor((clientX - r.left - pad.l) / step);
    setHover(i >= 0 && i < bars.length ? i : null);
  };
  const h = hover !== null ? bars[hover] : null;
  const total = vs.reduce((a, b) => a + b, 0);
  return (
    <div>
      <div className="num mb-1.5 flex min-h-5 items-baseline justify-between text-[11px] text-gray-500">
        <span>{h ? h.hint || h.label : `${bars.length} bars`}</span>
        <b className={`font-semibold ${(h ? h.v : total) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
          {h ? format(h.v) : `Σ ${format(total)}`}
        </b>
      </div>
      <div ref={ref} className="relative w-full min-w-0 touch-pan-y select-none overflow-hidden"
        onMouseMove={(e) => move(e.clientX)} onMouseLeave={() => setHover(null)}
        onTouchStart={(e) => move(e.touches[0].clientX)} onTouchMove={(e) => move(e.touches[0].clientX)}
        onTouchEnd={() => setHover(null)}>
        <svg width={w} height={height} className="block" role="img" aria-label="bar chart">
          {niceTicks(min, max, 3).map((v, i) => <line key={i} x1={pad.l} x2={w - pad.r} y1={y(v)} y2={y(v)} stroke={GRID} />)}
          {bars.map((b, i) => {
            const col = b.tone === "accent" ? t.accent : b.tone === "down" || (!b.tone && b.v < 0) ? t.down : t.up;
            const top = Math.min(y(b.v), zero);
            const bh = Math.max(b.v === 0 ? 0 : 1.5, Math.abs(y(b.v) - zero));
            return <rect key={i} data-bar x={pad.l + step * i + (step - bw) / 2} y={top} width={bw} height={bh} rx={bw > 4 ? 2.5 : 0}
              fill={col} opacity={hover === null || hover === i ? 0.9 : 0.45} />;
          })}
          {zeroLine && <line x1={pad.l} x2={w - pad.r} y1={zero} y2={zero} stroke={t.axis} />}
        </svg>
        {niceTicks(min, max, 2).map((v, i) => (
          <span key={i} className="num pointer-events-none absolute right-0 -translate-y-1/2 pr-1 text-[10px] text-gray-500" style={{ top: y(v) }}>{format(v)}</span>
        ))}
      </div>
      <div className="num relative mt-1 h-3.5 text-[10px] text-gray-500" style={{ marginRight: pad.r }}>
        {bars.map((b, i) => (i % every === 0 ? (
          <span key={i} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${((i + 0.5) / bars.length) * 100}%` }}>{b.label}</span>
        ) : null))}
      </div>
    </div>
  );
}

// ── Donut ────────────────────────────────────────────────────────────────────

export interface Slice { label: string; v: number; color: string }

export function Donut({ slices, size = 120, center }: { slices: Slice[]; size?: number; center?: ReactNode }) {
  const t = useThemeColors();
  const total = slices.reduce((s, x) => s + Math.max(0, x.v), 0);
  const r = size / 2 - 8;
  const c = 2 * Math.PI * r;
  let off = 0;
  return (
    <div className="flex items-center gap-4">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" role="img" aria-label="donut chart">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={t.grid} strokeWidth={12} />
          {total > 0 && slices.filter((s) => s.v > 0).map((s) => {
            const len = (s.v / total) * c;
            const el = <circle key={s.label} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.color} strokeWidth={12} strokeLinecap="butt"
              strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-off} />;
            off += len;
            return el;
          })}
        </svg>
        <div className="absolute inset-0 grid place-items-center text-center">{center ?? <span className="num text-lg font-semibold text-white">{total}</span>}</div>
      </div>
      <ul className="min-w-0 space-y-1 text-xs">
        {slices.map((s) => (
          <li key={s.label} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: s.color }} />
            <span className="truncate text-gray-400">{s.label}</span>
            <span className="num ml-auto pl-2 font-semibold text-gray-100">{s.v}</span>
            <span className="num w-10 text-right text-gray-500">{total ? `${Math.round((s.v / total) * 100)}%` : ""}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Sparkline ────────────────────────────────────────────────────────────────

export function Sparkline({ values, width = 64, height = 22, color }: { values: number[]; width?: number; height?: number; color?: string }) {
  const t = useThemeColors();
  if (values.length < 2) return <svg width={width} height={height} />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * width).toFixed(1)},${(height - 1 - ((v - min) / span) * (height - 2)).toFixed(1)}`).join(" ");
  const col = color ?? (values[values.length - 1] >= values[0] ? t.up : t.down);
  return (
    <svg width={width} height={height} className="block" aria-hidden>
      <path d={`M${pts.split(" ").join(" L")}`} fill="none" stroke={col} strokeWidth={1.4} />
    </svg>
  );
}
