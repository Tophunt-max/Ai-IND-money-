"use client";

import { ChevronLeft, ChevronRight, Minus, Plus, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

export interface Candle {
  t: number; // unix seconds
  o: number | null;
  h: number | null;
  l: number | null;
  c: number;
  v?: number;
}

const UP = "#0ecb81";
const DOWN = "#f6465d";

export interface Overlay { t: number; ema9?: number | null; ema21?: number | null; rsi?: number | null }
export interface Marker { t: number; bias: string; names: string[] }
export interface Level { label: string; price: number; tone: "entry" | "stop" | "target" }

const LEVEL_COLOR: Record<Level["tone"], string> = { entry: "#9db8ff", stop: DOWN, target: UP };
const MIN_PX = 4; // narrowest candle slot before the chart zooms in by default

/**
 * Dependency-free SVG candlestick chart: volume, EMA overlays, pattern markers, signal
 * levels, an optional RSI pane, an OHLC read-out and a crosshair with its price.
 * Zoom with the buttons, the mouse wheel or a pinch; pan with the arrows or by dragging
 * with the mouse. A long history opens on its latest candles at a readable width.
 */
export default function CandleChart({
  candles,
  height = 320,
  format = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 }),
  timeLabel = (t: number) => new Date(t * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  live = false,
  overlays,
  markers,
  levels,
  rsi = false,
}: {
  candles: Candle[];
  height?: number;
  format?: (v: number) => string;
  timeLabel?: (t: number) => string;
  live?: boolean;
  /** EMA lines (and RSI) by candle time (from /market/analysis). */
  overlays?: Overlay[];
  /** Candle-pattern markers by candle time. */
  markers?: Marker[];
  /** Horizontal entry / stop / target lines of a signal. */
  levels?: Level[];
  /** Show an RSI(14) pane under the price (needs ``overlays`` with ``rsi``). */
  rsi?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const [count, setCount] = useState<number | null>(null);   // visible candles (null: fit)
  const [end, setEnd] = useState<number | null>(null);       // index after the last visible (null: latest)
  const drag = useRef<{ x: number; end: number } | null>(null);
  const pinch = useRef<{ d: number; count: number } | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Candles with a missing open/high/low use the close (defensive: a vendor may leave them empty)
  const all = useMemo(
    () => candles.map((k) => {
      const o = k.o ?? k.c;
      return { ...k, o, h: Math.max(k.h ?? k.c, o, k.c), l: Math.min(k.l ?? k.c, o, k.c), v: k.v || 0 };
    }),
    [candles],
  );

  const pad = { l: 4, r: 62, t: 8 };
  const plotW = width - pad.l - pad.r;
  const fit = Math.max(10, Math.floor(plotW / MIN_PX));
  const n = Math.min(all.length, Math.max(10, count ?? fit));
  const stop = Math.min(all.length, Math.max(n, end ?? all.length));
  const data = all.slice(stop - n, stop);
  const offset = stop - n;
  const zoomed = n < all.length;

  const rsiPts = useMemo(() => (overlays || []).filter((o) => o.rsi != null), [overlays]);
  const showRsi = rsi && rsiPts.length > 1;
  const rsiH = showRsi ? Math.round(height * 0.2) : 0;
  const volH = Math.round(height * 0.15);
  const priceH = height - volH - rsiH - (showRsi ? 14 : 6);

  const geo = useMemo(() => {
    if (data.length < 2) return null;
    let min = Math.min(...data.map((k) => k.l));
    let max = Math.max(...data.map((k) => k.h));
    for (const lv of levels || []) {
      // Keep a signal's levels on the chart when they sit near the visible range
      if (lv.price > 0 && lv.price > min * 0.9 && lv.price < max * 1.1) {
        min = Math.min(min, lv.price);
        max = Math.max(max, lv.price);
      }
    }
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const span = max - min;
    min -= span * 0.04;
    max += span * 0.04;
    const vmax = Math.max(1, ...data.map((k) => k.v));
    const step = plotW / data.length;
    const body = Math.max(1, Math.min(14, step * 0.7));
    const x = (i: number) => pad.l + step * (i + 0.5);
    const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (priceH - pad.t);
    const volBase = priceH + volH + 2;
    const vy = (v: number) => volBase - (v / vmax) * volH;
    const rsiTop = volBase + 12;
    const ry = (v: number) => rsiTop + (1 - v / 100) * rsiH;
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + (max - min) * (1 - f));
    return { x, y, vy, ry, body, step, ticks, min, max, volBase, rsiTop };
  }, [data, plotW, priceH, volH, rsiH, pad.l, pad.t, levels]);

  if (!geo) return <div className="py-8 text-center text-sm text-gray-500">Not enough data for a chart.</div>;

  const index = new Map(data.map((k, i) => [k.t, i]));
  const line = (key: "ema9" | "ema21") => (overlays || [])
    .filter((o) => o[key] != null && index.has(o.t))
    .map((o) => `${geo.x(index.get(o.t)!)},${geo.y(o[key] as number)}`).join(" ");
  const rsiPath = showRsi ? rsiPts.filter((o) => index.has(o.t))
    .map((o, i) => `${i ? "L" : "M"}${geo.x(index.get(o.t)!).toFixed(1)},${geo.ry(o.rsi as number).toFixed(1)}`).join(" ") : "";

  const last = all[all.length - 1];
  const prev = all.length > 1 ? all[all.length - 2].c : last.o;
  const lastUp = last.c >= prev;
  const lastVisible = stop === all.length;
  const h = hover !== null ? data[hover] : null;
  const shown = h || last;
  const change = shown.c - shown.o;
  const rsiNow = showRsi ? (rsiPts.find((o) => o.t === (h?.t ?? last.t))?.rsi ?? null) : null;

  const at = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    return r ? Math.floor((clientX - r.left - pad.l) / geo.step) : -1;
  };
  const move = (clientX: number) => {
    if (drag.current) {
      const shift = Math.round((clientX - drag.current.x) / geo.step);
      setEnd(Math.max(n, Math.min(all.length, drag.current.end - shift)));
      return;
    }
    const i = at(clientX);
    setHover(i >= 0 && i < data.length ? i : null);
  };
  const zoom = (factor: number) => {
    setCount(Math.max(10, Math.min(all.length, Math.round(n * factor))));
  };
  const pan = (dir: number) => setEnd(Math.max(n, Math.min(all.length, stop + dir * Math.max(1, Math.round(n / 4)))));
  const reset = () => { setCount(null); setEnd(null); };

  const xTicks = [0, Math.floor(data.length / 3), Math.floor((2 * data.length) / 3), data.length - 1];

  return (
    <div>
      {/* OHLC read-out and zoom controls */}
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <div className="num flex min-h-5 flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[11px] text-gray-500">
          <span className="text-gray-400">{h ? timeLabel(h.t) : live ? "● Live" : "Last"}</span>
          <span>O <b className="font-medium text-gray-200">{format(shown.o)}</b></span>
          <span>H <b className="font-medium text-gray-200">{format(shown.h)}</b></span>
          <span>L <b className="font-medium text-gray-200">{format(shown.l)}</b></span>
          <span>C <b className={`font-semibold ${change >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{format(shown.c)}</b></span>
          {shown.v > 0 && <span>V <b className="font-medium text-gray-300">{shown.v.toLocaleString("en-IN")}</b></span>}
          {rsiNow != null && <span>RSI <b className="font-medium text-violet-300">{rsiNow.toFixed(1)}</b></span>}
        </div>
        {all.length > 20 && (
          <div className="flex items-center gap-0.5 rounded-md border border-white/6 bg-ink-850 p-0.5 text-gray-400">
            <button type="button" aria-label="Zoom out" onClick={() => zoom(1.5)} className="rounded-sm p-1 hover:bg-white/6 hover:text-white"><Minus className="h-3.5 w-3.5" /></button>
            <button type="button" aria-label="Zoom in" onClick={() => zoom(1 / 1.5)} className="rounded-sm p-1 hover:bg-white/6 hover:text-white"><Plus className="h-3.5 w-3.5" /></button>
            {zoomed && <>
              <button type="button" aria-label="Earlier" onClick={() => pan(-1)} className="rounded-sm p-1 hover:bg-white/6 hover:text-white"><ChevronLeft className="h-3.5 w-3.5" /></button>
              <button type="button" aria-label="Later" onClick={() => pan(1)} disabled={lastVisible} className="rounded-sm p-1 hover:bg-white/6 hover:text-white disabled:opacity-30"><ChevronRight className="h-3.5 w-3.5" /></button>
            </>}
            {(count !== null || end !== null) && <button type="button" aria-label="Reset" onClick={reset} className="rounded-sm p-1 hover:bg-white/6 hover:text-white"><RotateCcw className="h-3.5 w-3.5" /></button>}
            <span className="num px-1 text-[10px] text-gray-500">{n}/{all.length}</span>
          </div>
        )}
      </div>

      <div ref={box} className="relative w-full min-w-0 touch-none select-none overflow-hidden"
        onMouseDown={(e) => { if (zoomed) drag.current = { x: e.clientX, end: stop }; }}
        onMouseUp={() => { drag.current = null; }}
        onMouseMove={(e) => move(e.clientX)} onMouseLeave={() => { drag.current = null; setHover(null); }}
        onWheel={(e) => { if (all.length > 20 && (e.ctrlKey || Math.abs(e.deltaY) > Math.abs(e.deltaX))) zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15); }}
        onTouchStart={(e) => {
          if (e.touches.length === 2) {
            pinch.current = { d: Math.abs(e.touches[0].clientX - e.touches[1].clientX), count: n };
            setHover(null);
          } else move(e.touches[0].clientX);
        }}
        onTouchMove={(e) => {
          if (e.touches.length === 2 && pinch.current) {
            const d = Math.abs(e.touches[0].clientX - e.touches[1].clientX);
            if (d > 10) setCount(Math.max(10, Math.min(all.length, Math.round(pinch.current.count * (pinch.current.d / d)))));
          } else if (!pinch.current) move(e.touches[0].clientX);
        }}
        onTouchEnd={() => { pinch.current = null; setHover(null); }}>
        <svg width={width} height={height} className="block">
          {geo.ticks.map((v, i) => (
            <line key={i} x1={pad.l} x2={width - pad.r} y1={geo.y(v)} y2={geo.y(v)} stroke="rgba(255,255,255,0.05)" />
          ))}
          {xTicks.map((i) => <line key={`x${i}`} x1={geo.x(i)} x2={geo.x(i)} y1={pad.t} y2={geo.volBase} stroke="rgba(255,255,255,0.03)" />)}
          <line x1={pad.l} x2={width - pad.r} y1={priceH + 3} y2={priceH + 3} stroke="rgba(255,255,255,0.06)" />

          {data.map((k, i) => {
            const up = k.c >= k.o;
            const col = up ? UP : DOWN;
            const top = geo.y(Math.max(k.o, k.c));
            const bh = Math.max(1, Math.abs(geo.y(k.o) - geo.y(k.c)));
            const cx = geo.x(i);
            return (
              <g key={k.t} opacity={hover === null || hover === i ? 1 : 0.6}>
                <rect x={cx - geo.body / 2} y={geo.vy(k.v)} width={geo.body} height={geo.volBase - geo.vy(k.v)} fill={col} opacity={0.25} />
                <line x1={cx} x2={cx} y1={geo.y(k.h)} y2={geo.y(k.l)} stroke={col} strokeWidth={1} />
                <rect x={cx - geo.body / 2} y={top} width={geo.body} height={bh} rx={geo.body > 4 ? 1 : 0} fill={col} />
              </g>
            );
          })}

          {/* Indicator overlays, pattern markers and signal levels */}
          {overlays && overlays.length > 0 && (
            <>
              <polyline points={line("ema9")} fill="none" stroke="#fbbf24" strokeWidth={1.2} strokeOpacity={0.9} />
              <polyline points={line("ema21")} fill="none" stroke="#a78bfa" strokeWidth={1.2} strokeOpacity={0.9} />
            </>
          )}
          {(markers || []).filter((m) => index.has(m.t)).map((m) => {
            const i = index.get(m.t)!;
            const k = data[i];
            const bull = m.bias === "bullish";
            const yy = bull ? geo.y(k.l) + 9 : geo.y(k.h) - 9;
            const col = bull ? UP : m.bias === "bearish" ? DOWN : "#fbbf24";
            const pts = bull ? `${geo.x(i)},${yy - 5} ${geo.x(i) - 4},${yy + 2} ${geo.x(i) + 4},${yy + 2}`
              : `${geo.x(i)},${yy + 5} ${geo.x(i) - 4},${yy - 2} ${geo.x(i) + 4},${yy - 2}`;
            return <polygon key={`m${m.t}`} points={pts} fill={col}><title>{m.names.join(", ")}</title></polygon>;
          })}
          {(levels || []).filter((lv) => lv.price > 0).map((lv) => (
            <g key={lv.label}>
              <line x1={pad.l} x2={width - pad.r} y1={geo.y(lv.price)} y2={geo.y(lv.price)}
                stroke={LEVEL_COLOR[lv.tone]} strokeDasharray="6 4" strokeOpacity={0.85} />
              <text x={pad.l + 4} y={geo.y(lv.price) - 3} fontSize={10} fill={LEVEL_COLOR[lv.tone]}>{lv.label} {format(lv.price)}</text>
            </g>
          ))}

          {/* RSI pane */}
          {showRsi && (
            <g>
              <rect x={pad.l} y={geo.ry(70)} width={plotW} height={geo.ry(30) - geo.ry(70)} fill="rgba(167,139,250,0.06)" />
              {[30, 70].map((lv) => <line key={lv} x1={pad.l} x2={width - pad.r} y1={geo.ry(lv)} y2={geo.ry(lv)} stroke="rgba(167,139,250,0.25)" strokeDasharray="3 3" />)}
              <path d={rsiPath} fill="none" stroke="#a78bfa" strokeWidth={1.2} />
              <text x={pad.l + 2} y={geo.rsiTop - 2} fontSize={9} fill="#6b7280">RSI 14</text>
            </g>
          )}

          {/* Last price line (only when the latest candle is on screen) */}
          {lastVisible && (
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(last.c)} y2={geo.y(last.c)}
              stroke={lastUp ? UP : DOWN} strokeDasharray="3 3" strokeOpacity={0.7} />
          )}

          {h && hover !== null && (
            <>
              <line x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={height} stroke="rgba(255,255,255,0.25)" strokeDasharray="2 3" />
              <line x1={pad.l} x2={width - pad.r} y1={geo.y(h.c)} y2={geo.y(h.c)} stroke="rgba(255,255,255,0.2)" strokeDasharray="2 3" />
            </>
          )}
        </svg>

        {/* Price axis */}
        {geo.ticks.map((v, i) => (
          <span key={i} className="num pointer-events-none absolute right-0 -translate-y-1/2 pr-1 text-[10px] text-gray-500"
            style={{ top: geo.y(v) }}>{format(v)}</span>
        ))}
        {lastVisible && (
          <span className={`num pointer-events-none absolute right-0 -translate-y-1/2 rounded-sm px-1 py-0.5 text-[10px] font-semibold text-ink-950 ${lastUp ? "bg-emerald-400" : "bg-rose-400"}`}
            style={{ top: geo.y(last.c) }}>
            {live && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-ink-950 align-middle" />}
            {format(last.c)}
          </span>
        )}
        {h && (
          <span className="num pointer-events-none absolute right-0 -translate-y-1/2 rounded-sm bg-gray-200 px-1 py-0.5 text-[10px] font-semibold text-ink-950"
            style={{ top: geo.y(h.c) }}>{format(h.c)}</span>
        )}
        {h && hover !== null && (
          <span className="num pointer-events-none absolute -translate-x-1/2 rounded-sm bg-ink-700 px-1.5 py-0.5 text-[10px] text-gray-200"
            style={{ left: geo.x(hover), top: geo.volBase - 14 }}>{timeLabel(h.t)}</span>
        )}
      </div>

      <div className="num mt-1 flex justify-between pr-16 text-[10px] text-gray-500">
        {Array.from(new Set(xTicks)).map((i) => <span key={i}>{timeLabel(data[i].t)}</span>)}
      </div>
      {offset > 0 && <span className="sr-only">{offset} earlier candles hidden</span>}
    </div>
  );
}
