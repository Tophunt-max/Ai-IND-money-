"use client";

import { ChevronLeft, ChevronRight, Minus, MousePointer2, Plus, RotateCcw, Slash, SeparatorHorizontal, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { bollinger, ema, macd as macdOf, rsi as rsiOf, sma, vwap as vwapOf } from "@/lib/indicators";
import type { IndicatorKey } from "@/lib/prefs";
import { useThemeColors } from "@/lib/themeColors";

import { cx } from "./ui";

export interface Candle {
  t: number; // unix seconds
  o: number | null;
  h: number | null;
  l: number | null;
  c: number;
  v?: number;
}

export interface Overlay { t: number; ema9?: number | null; ema21?: number | null; rsi?: number | null }
export interface Marker { t: number; bias: string; names: string[] }
export interface Level { label: string; price: number; tone: "entry" | "stop" | "target" | "alert" | "avg" }

/** A user drawing in price / time coordinates (kept per symbol in this browser). */
export interface Drawing { id: string; kind: "hline" | "trend"; p1: number; t1?: number; p2?: number; t2?: number }

const MIN_PX = 5;

function loadDrawings(key?: string): Drawing[] {
  if (!key || typeof localStorage === "undefined") return [];
  try {
    const d = JSON.parse(localStorage.getItem(`aiind:draw:${key}`) || "[]");
    return Array.isArray(d) ? d.slice(0, 50) : [];
  } catch {
    return [];
  }
}

function saveDrawings(key: string | undefined, d: Drawing[]) {
  if (!key) return;
  try { localStorage.setItem(`aiind:draw:${key}`, JSON.stringify(d.slice(0, 50))); } catch { /* ignore */ }
}

/**
 * Dependency-free SVG candlestick chart: volume, EMA / SMA / Bollinger / VWAP overlays,
 * RSI and MACD panes, pattern markers, signal / position / alert levels, user drawings
 * (horizontal lines, trend lines), an OHLC read-out and a crosshair. Zoom with the buttons,
 * the wheel or a pinch; pan with the arrows or by dragging.
 */
export default function CandleChart({
  candles,
  height = 360,
  format = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 }),
  timeLabel = (t: number) => new Date(t * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  live = false,
  studies = ["ema", "volume", "levels"],
  intraday = false,
  overlays,
  markers,
  levels,
  drawKey,
  rsi: legacyRsi = false,
}: {
  candles: Candle[];
  height?: number;
  format?: (v: number) => string;
  timeLabel?: (t: number) => string;
  live?: boolean;
  /** Which studies to draw (Settings remembers the choice). */
  studies?: IndicatorKey[];
  /** Intraday candles (VWAP resets per session; only shown then). */
  intraday?: boolean;
  /** Server-side overlays (kept for the RSI the server computed); optional. */
  overlays?: Overlay[];
  markers?: Marker[];
  levels?: Level[];
  /** Where to keep drawings (the symbol); none: no drawing tools. */
  drawKey?: string;
  /** Old prop: show the RSI pane. */
  rsi?: boolean;
}) {
  const t = useThemeColors();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const [hoverY, setHoverY] = useState<number | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [end, setEnd] = useState<number | null>(null);
  const [tool, setTool] = useState<"none" | "hline" | "trend">("none");
  const [draft, setDraft] = useState<{ p: number; t: number } | null>(null);
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const drag = useRef<{ x: number; end: number } | null>(null);
  const pinch = useRef<{ d: number; count: number } | null>(null);
  const has = (k: IndicatorKey) => studies.includes(k);
  const showRsi = has("rsi") || legacyRsi;
  const showMacd = has("macd");

  useEffect(() => setDrawings(loadDrawings(drawKey)), [drawKey]);
  const updateDrawings = (d: Drawing[]) => {
    setDrawings(d);
    saveDrawings(drawKey, d);
  };

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const all = useMemo(
    () => candles.map((k) => {
      const o = k.o ?? k.c;
      return { t: k.t, o, h: Math.max(k.h ?? k.c, o, k.c), l: Math.min(k.l ?? k.c, o, k.c), c: k.c, v: k.v || 0 };
    }),
    [candles],
  );

  // Studies over the whole history (so the first visible values are warmed up)
  const calc = useMemo(() => {
    const closes = all.map((k) => k.c);
    return {
      ema9: has("ema") ? ema(closes, 9) : null,
      ema21: has("ema") ? ema(closes, 21) : null,
      sma50: has("sma") ? sma(closes, 50) : null,
      sma200: has("sma") ? sma(closes, 200) : null,
      bb: has("bb") ? bollinger(closes, 20, 2) : null,
      vwap: has("vwap") && intraday ? vwapOf(all) : null,
      rsi: showRsi ? rsiOf(closes, 14) : null,
      macd: showMacd ? macdOf(closes) : null,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all, studies.join(","), intraday, showRsi, showMacd]);

  const pad = { l: 4, r: 64, t: 10 };
  const plotW = width - pad.l - pad.r;
  const fit = Math.max(10, Math.floor(plotW / MIN_PX));
  const n = Math.min(all.length, Math.max(10, count ?? fit));
  const stop = Math.min(all.length, Math.max(n, end ?? all.length));
  const offset = stop - n;
  const data = all.slice(offset, stop);
  const zoomed = n < all.length;

  const lowerH = Math.round(height * 0.18);
  const rsiH = showRsi ? lowerH : 0;
  const macdH = showMacd ? lowerH : 0;
  const volH = has("volume") ? Math.round(height * 0.14) : 0;
  const priceH = height - volH - rsiH - macdH - (showRsi ? 16 : 0) - (showMacd ? 16 : 0) - 6;

  const geo = useMemo(() => {
    if (data.length < 2) return null;
    let min = Math.min(...data.map((k) => k.l));
    let max = Math.max(...data.map((k) => k.h));
    const extra: number[] = [];
    for (const lv of levels || []) extra.push(lv.price);
    if (calc.bb) {
      for (let i = offset; i < stop; i++) {
        if (calc.bb.upper[i] != null) extra.push(calc.bb.upper[i] as number);
        if (calc.bb.lower[i] != null) extra.push(calc.bb.lower[i] as number);
      }
    }
    for (const p of extra) {
      if (p > 0 && p > min * 0.9 && p < max * 1.1) {
        min = Math.min(min, p);
        max = Math.max(max, p);
      }
    }
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    min -= span * 0.05;
    max += span * 0.05;
    const vmax = Math.max(1, ...data.map((k) => k.v));
    const step = plotW / data.length;
    const body = Math.max(1, Math.min(14, step * 0.68));
    const x = (i: number) => pad.l + step * (i + 0.5);
    const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (priceH - pad.t);
    const price = (py: number) => min + (1 - (py - pad.t) / (priceH - pad.t)) * (max - min);
    const volBase = priceH + volH + 2;
    const vy = (v: number) => volBase - (v / vmax) * volH;
    const rsiTop = volBase + 16;
    const ry = (v: number) => rsiTop + (1 - v / 100) * rsiH;
    const macdTop = rsiTop + rsiH + (showRsi ? 16 : 0);
    let mmax = 1e-9;
    if (calc.macd) {
      for (let i = offset; i < stop; i++) {
        for (const v of [calc.macd.line[i], calc.macd.signal[i], calc.macd.hist[i]]) if (v != null) mmax = Math.max(mmax, Math.abs(v));
      }
    }
    const my = (v: number) => macdTop + macdH / 2 - (v / mmax) * (macdH / 2 - 2);
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + (max - min) * (1 - f));
    return { x, y, price, vy, ry, my, body, step, ticks, min, max, volBase, rsiTop, macdTop };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, plotW, priceH, volH, rsiH, macdH, levels, calc, offset, stop]);

  if (!geo) return <div className="py-10 text-center text-sm text-gray-500">Not enough data for a chart.</div>;

  const index = new Map(data.map((k, i) => [k.t, i]));
  const path = (vals: (number | null)[] | null | undefined, yf: (v: number) => number) => {
    if (!vals) return "";
    let d = "";
    let pen = false;
    for (let i = 0; i < data.length; i++) {
      const v = vals[offset + i];
      if (v == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${geo.x(i).toFixed(1)},${yf(v).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  const xOfT = (tt: number) => {
    // interpolate by time between visible candles (drawings may sit off-screen)
    const first = data[0].t;
    const last = data[data.length - 1].t;
    if (data.length < 2 || last === first) return geo.x(0);
    const i = index.get(tt);
    if (i != null) return geo.x(i);
    return geo.x(0) + ((tt - first) / (last - first)) * (geo.x(data.length - 1) - geo.x(0));
  };

  const last = all[all.length - 1];
  const prevClose = all.length > 1 ? all[all.length - 2].c : last.o;
  const lastUp = last.c >= prevClose;
  const lastVisible = stop === all.length;
  const h = hover !== null ? data[hover] : null;
  const shownI = hover !== null ? offset + hover : all.length - 1;
  const shown = h || last;
  const change = shown.c - shown.o;
  const val = (arr: (number | null)[] | null | undefined) => (arr ? arr[shownI] : null);

  const at = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    return r ? Math.floor((clientX - r.left - pad.l) / geo.step) : -1;
  };
  const yAt = (clientY: number) => {
    const r = box.current?.getBoundingClientRect();
    return r ? clientY - r.top : null;
  };
  const move = (clientX: number, clientY?: number) => {
    if (drag.current) {
      const shift = Math.round((clientX - drag.current.x) / geo.step);
      setEnd(Math.max(n, Math.min(all.length, drag.current.end - shift)));
      return;
    }
    const i = at(clientX);
    setHover(i >= 0 && i < data.length ? i : null);
    if (clientY != null) setHoverY(yAt(clientY));
  };
  const zoom = (factor: number) => setCount(Math.max(10, Math.min(all.length, Math.round(n * factor))));
  const pan = (dir: number) => setEnd(Math.max(n, Math.min(all.length, stop + dir * Math.max(1, Math.round(n / 4)))));
  const reset = () => { setCount(null); setEnd(null); };

  const place = (clientX: number, clientY: number) => {
    const py = yAt(clientY);
    const i = at(clientX);
    if (py == null || py > priceH || i < 0 || i >= data.length) return;
    const p = Math.round(geo.price(py) * 100) / 100;
    const tt = data[i].t;
    if (tool === "hline") {
      updateDrawings([...drawings, { id: `${Date.now()}`, kind: "hline", p1: p }]);
      setTool("none");
    } else if (tool === "trend") {
      if (!draft) setDraft({ p, t: tt });
      else {
        updateDrawings([...drawings, { id: `${Date.now()}`, kind: "trend", p1: draft.p, t1: draft.t, p2: p, t2: tt }]);
        setDraft(null);
        setTool("none");
      }
    }
  };

  const LEVEL: Record<Level["tone"], string> = { entry: t.accent, stop: t.down, target: t.up, alert: t.amber, avg: t.sky };
  const xTicks = [0, Math.floor(data.length / 3), Math.floor((2 * data.length) / 3), data.length - 1];
  const hoverPrice = hoverY != null && hoverY <= priceH && hoverY >= pad.t ? geo.price(hoverY) : null;

  const legend: [string, string, number | null][] = [
    ...(calc.ema9 ? [["EMA 9", t.amber, val(calc.ema9)], ["EMA 21", t.violet, val(calc.ema21)]] as [string, string, number | null][] : []),
    ...(calc.sma50 ? [["SMA 50", t.sky, val(calc.sma50)], ["SMA 200", "#ec4899", val(calc.sma200)]] as [string, string, number | null][] : []),
    ...(calc.bb ? [["BB", t.muted, val(calc.bb.mid)]] as [string, string, number | null][] : []),
    ...(calc.vwap ? [["VWAP", "#06b6d4", val(calc.vwap)]] as [string, string, number | null][] : []),
  ];

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="num flex min-h-5 flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
          <span className="font-medium text-gray-400">{h ? timeLabel(h.t) : live ? "● Live" : "Last"}</span>
          <span>O <b className="font-semibold text-gray-200">{format(shown.o)}</b></span>
          <span>H <b className="font-semibold text-gray-200">{format(shown.h)}</b></span>
          <span>L <b className="font-semibold text-gray-200">{format(shown.l)}</b></span>
          <span>C <b className={change >= 0 ? "font-semibold text-emerald-400" : "font-semibold text-rose-400"}>{format(shown.c)}</b></span>
          {shown.v > 0 && <span>Vol <b className="font-semibold text-gray-300">{shown.v.toLocaleString("en-IN")}</b></span>}
          {legend.map(([name, col, v]) => v != null && (
            <span key={name} className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-3 rounded" style={{ background: col }} />{name} <b className="font-semibold text-gray-300">{format(v)}</b></span>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {drawKey && (
            <div className="flex items-center gap-0.5 rounded-xl bg-ink-850 p-0.5 ring-1 ring-inset ring-white/[0.06]">
              {([["none", MousePointer2, "Pointer"], ["hline", SeparatorHorizontal, "Horizontal line"], ["trend", Slash, "Trend line (2 clicks)"]] as const).map(([k, Icon, label]) => (
                <button key={k} type="button" aria-label={label} title={label} onClick={() => { setTool(k); setDraft(null); }}
                  className={cx("rounded-lg p-1.5 transition", tool === k ? "bg-ink-900 text-brand-400 shadow-card" : "text-gray-500 hover:text-white")}>
                  <Icon className="h-3.5 w-3.5" />
                </button>
              ))}
              {drawings.length > 0 && (
                <button type="button" aria-label="Clear drawings" title={`Clear ${drawings.length} drawing(s)`} onClick={() => updateDrawings([])}
                  className="rounded-lg p-1.5 text-gray-500 hover:text-rose-400"><Trash2 className="h-3.5 w-3.5" /></button>
              )}
            </div>
          )}
          {all.length > 20 && (
            <div className="flex items-center gap-0.5 rounded-xl bg-ink-850 p-0.5 text-gray-500 ring-1 ring-inset ring-white/[0.06]">
              <button type="button" aria-label="Zoom out" onClick={() => zoom(1.5)} className="rounded-lg p-1.5 hover:text-white"><Minus className="h-3.5 w-3.5" /></button>
              <button type="button" aria-label="Zoom in" onClick={() => zoom(1 / 1.5)} className="rounded-lg p-1.5 hover:text-white"><Plus className="h-3.5 w-3.5" /></button>
              {zoomed && <>
                <button type="button" aria-label="Earlier" onClick={() => pan(-1)} className="rounded-lg p-1.5 hover:text-white"><ChevronLeft className="h-3.5 w-3.5" /></button>
                <button type="button" aria-label="Later" onClick={() => pan(1)} disabled={lastVisible} className="rounded-lg p-1.5 hover:text-white disabled:opacity-30"><ChevronRight className="h-3.5 w-3.5" /></button>
              </>}
              {(count !== null || end !== null) && <button type="button" aria-label="Reset" onClick={reset} className="rounded-lg p-1.5 hover:text-white"><RotateCcw className="h-3.5 w-3.5" /></button>}
              <span className="num px-1 text-[10px]">{n}/{all.length}</span>
            </div>
          )}
        </div>
      </div>
      {tool !== "none" && (
        <p className="mb-1 text-[11px] font-medium text-brand-400">
          {tool === "hline" ? "Tap the chart to place a horizontal line." : draft ? "Tap the second point of the trend line." : "Tap the first point of the trend line."}
        </p>
      )}

      <div ref={box} className={cx("relative w-full min-w-0 touch-none select-none overflow-hidden", tool !== "none" && "cursor-crosshair")}
        onMouseDown={(e) => { if (tool === "none" && zoomed) drag.current = { x: e.clientX, end: stop }; }}
        onMouseUp={() => { drag.current = null; }}
        onClick={(e) => tool !== "none" && place(e.clientX, e.clientY)}
        onMouseMove={(e) => move(e.clientX, e.clientY)} onMouseLeave={() => { drag.current = null; setHover(null); setHoverY(null); }}
        onWheel={(e) => { if (all.length > 20 && (e.ctrlKey || Math.abs(e.deltaY) > Math.abs(e.deltaX))) zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15); }}
        onTouchStart={(e) => {
          if (e.touches.length === 2) {
            pinch.current = { d: Math.abs(e.touches[0].clientX - e.touches[1].clientX), count: n };
            setHover(null);
          } else move(e.touches[0].clientX, e.touches[0].clientY);
        }}
        onTouchMove={(e) => {
          if (e.touches.length === 2 && pinch.current) {
            const d = Math.abs(e.touches[0].clientX - e.touches[1].clientX);
            if (d > 10) setCount(Math.max(10, Math.min(all.length, Math.round(pinch.current.count * (pinch.current.d / d)))));
          } else if (!pinch.current) move(e.touches[0].clientX, e.touches[0].clientY);
        }}
        onTouchEnd={() => { pinch.current = null; setHover(null); setHoverY(null); }}>
        <svg width={width} height={height} className="block" role="img" aria-label="candlestick chart">
          {geo.ticks.map((v, i) => <line key={i} x1={pad.l} x2={width - pad.r} y1={geo.y(v)} y2={geo.y(v)} stroke={t.grid} />)}
          {xTicks.map((i) => <line key={`x${i}`} x1={geo.x(i)} x2={geo.x(i)} y1={pad.t} y2={priceH} stroke={t.gridSoft} />)}

          {/* Bollinger band */}
          {calc.bb && (
            <>
              <path data-study="bb" d={path(calc.bb.upper, geo.y)} fill="none" stroke={t.muted} strokeWidth={1} strokeOpacity={0.6} />
              <path d={path(calc.bb.lower, geo.y)} fill="none" stroke={t.muted} strokeWidth={1} strokeOpacity={0.6} />
              <path d={path(calc.bb.mid, geo.y)} fill="none" stroke={t.muted} strokeWidth={1} strokeDasharray="3 3" strokeOpacity={0.6} />
            </>
          )}

          {/* Candles and volume */}
          {data.map((k, i) => {
            const up = k.c >= k.o;
            const col = up ? t.up : t.down;
            const top = geo.y(Math.max(k.o, k.c));
            const bh = Math.max(1, Math.abs(geo.y(k.o) - geo.y(k.c)));
            const x = geo.x(i);
            return (
              <g key={k.t} opacity={hover === null || hover === i ? 1 : 0.7}>
                {volH > 0 && <rect x={x - geo.body / 2} y={geo.vy(k.v)} width={geo.body} height={geo.volBase - geo.vy(k.v)} fill={col} opacity={0.22} />}
                <line x1={x} x2={x} y1={geo.y(k.h)} y2={geo.y(k.l)} stroke={col} strokeWidth={1} />
                <rect x={x - geo.body / 2} y={top} width={geo.body} height={bh} rx={geo.body > 5 ? 1.5 : 0} fill={col} />
              </g>
            );
          })}

          {/* Moving averages, VWAP */}
          {calc.ema9 && <path data-study="ema9" d={path(calc.ema9, geo.y)} fill="none" stroke={t.amber} strokeWidth={1.3} />}
          {calc.ema21 && <path data-study="ema21" d={path(calc.ema21, geo.y)} fill="none" stroke={t.violet} strokeWidth={1.3} />}
          {calc.sma50 && <path data-study="sma50" d={path(calc.sma50, geo.y)} fill="none" stroke={t.sky} strokeWidth={1.3} />}
          {calc.sma200 && <path data-study="sma200" d={path(calc.sma200, geo.y)} fill="none" stroke="#ec4899" strokeWidth={1.3} />}
          {calc.vwap && <path data-study="vwap" d={path(calc.vwap, geo.y)} fill="none" stroke="#06b6d4" strokeWidth={1.4} strokeDasharray="5 3" />}

          {/* Candle-pattern markers */}
          {has("patterns") && (markers || []).filter((m) => index.has(m.t)).map((m) => {
            const i = index.get(m.t)!;
            const k = data[i];
            const bull = m.bias === "bullish";
            const yy = bull ? geo.y(k.l) + 9 : geo.y(k.h) - 9;
            const col = bull ? t.up : m.bias === "bearish" ? t.down : t.amber;
            const x = geo.x(i);
            const pts = bull ? `${x},${yy - 5} ${x - 4},${yy + 2} ${x + 4},${yy + 2}` : `${x},${yy + 5} ${x - 4},${yy - 2} ${x + 4},${yy - 2}`;
            return <polygon data-marker key={`m${m.t}`} points={pts} fill={col}><title>{m.names.join(", ")}</title></polygon>;
          })}

          {/* Levels (signal, position, alerts) */}
          {has("levels") && (levels || []).filter((lv) => lv.price > geo.min && lv.price < geo.max).map((lv) => (
            <g key={`${lv.label}-${lv.price}`}>
              <line x1={pad.l} x2={width - pad.r} y1={geo.y(lv.price)} y2={geo.y(lv.price)} stroke={LEVEL[lv.tone]} strokeDasharray="6 4" strokeOpacity={0.9} />
              <text x={pad.l + 4} y={geo.y(lv.price) - 4} fontSize={10} fontWeight={600} fill={LEVEL[lv.tone]}>{lv.label} {format(lv.price)}</text>
            </g>
          ))}

          {/* User drawings */}
          {drawings.map((d) => d.kind === "hline" ? (
            <g key={d.id}>
              <line x1={pad.l} x2={width - pad.r} y1={geo.y(d.p1)} y2={geo.y(d.p1)} stroke={t.accent} strokeWidth={1.4} />
              <text x={width - pad.r - 4} y={geo.y(d.p1) - 4} fontSize={10} textAnchor="end" fill={t.accent}>{format(d.p1)}</text>
            </g>
          ) : d.t1 != null && d.t2 != null && d.p2 != null ? (
            <line key={d.id} x1={xOfT(d.t1)} y1={geo.y(d.p1)} x2={xOfT(d.t2)} y2={geo.y(d.p2)} stroke={t.accent} strokeWidth={1.6} />
          ) : null)}
          {draft && <circle cx={xOfT(draft.t)} cy={geo.y(draft.p)} r={4} fill={t.accent} />}

          {/* RSI pane */}
          {calc.rsi && (
            <g>
              <text x={pad.l + 2} y={geo.rsiTop - 4} fontSize={10} fill={t.muted}>RSI 14{val(calc.rsi) != null ? ` · ${(val(calc.rsi) as number).toFixed(1)}` : ""}</text>
              <rect x={pad.l} y={geo.ry(70)} width={plotW} height={geo.ry(30) - geo.ry(70)} fill={t.violet} opacity={0.07} />
              {[30, 70].map((lv) => <line key={lv} x1={pad.l} x2={width - pad.r} y1={geo.ry(lv)} y2={geo.ry(lv)} stroke={t.violet} strokeOpacity={0.35} strokeDasharray="3 3" />)}
              <path data-study="rsi" d={path(calc.rsi, geo.ry)} fill="none" stroke={t.violet} strokeWidth={1.3} />
            </g>
          )}

          {/* MACD pane */}
          {calc.macd && (
            <g>
              <text x={pad.l + 2} y={geo.macdTop - 4} fontSize={10} fill={t.muted}>
                MACD 12,26,9{val(calc.macd.line) != null ? ` · ${(val(calc.macd.line) as number).toFixed(2)} / ${(val(calc.macd.signal) as number | null)?.toFixed(2) ?? "—"}` : ""}
              </text>
              <line x1={pad.l} x2={width - pad.r} y1={geo.my(0)} y2={geo.my(0)} stroke={t.grid} />
              {data.map((k, i) => {
                const v = calc.macd!.hist[offset + i];
                if (v == null) return null;
                return <rect key={`h${k.t}`} x={geo.x(i) - geo.body / 2} y={Math.min(geo.my(v), geo.my(0))} width={geo.body}
                  height={Math.max(1, Math.abs(geo.my(v) - geo.my(0)))} fill={v >= 0 ? t.up : t.down} opacity={0.5} />;
              })}
              <path data-study="macd" d={path(calc.macd.line, geo.my)} fill="none" stroke={t.accent} strokeWidth={1.3} />
              <path d={path(calc.macd.signal, geo.my)} fill="none" stroke={t.amber} strokeWidth={1.3} />
            </g>
          )}

          {lastVisible && (
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(last.c)} y2={geo.y(last.c)} stroke={lastUp ? t.up : t.down} strokeDasharray="3 3" strokeOpacity={0.8} />
          )}

          {h && hover !== null && (
            <>
              <line x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={height} stroke={t.axis} strokeDasharray="2 3" />
              {hoverPrice != null && <line x1={pad.l} x2={width - pad.r} y1={hoverY!} y2={hoverY!} stroke={t.axis} strokeDasharray="2 3" />}
            </>
          )}
        </svg>

        {/* Price axis */}
        {geo.ticks.map((v, i) => (
          <span key={i} className="num pointer-events-none absolute right-0 -translate-y-1/2 pr-1 text-[10px] text-gray-500" style={{ top: geo.y(v) }}>{format(v)}</span>
        ))}
        {lastVisible && (
          <span className="num pointer-events-none absolute right-0 -translate-y-1/2 rounded-md px-1.5 py-0.5 text-[10px] font-bold text-onaccent"
            style={{ top: geo.y(last.c), background: lastUp ? t.up : t.down }}>
            {live && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-onaccent align-middle" />}
            {format(last.c)}
          </span>
        )}
        {hoverPrice != null && (
          <span className="num pointer-events-none absolute right-0 -translate-y-1/2 rounded-md bg-gray-200 px-1.5 py-0.5 text-[10px] font-bold text-ink-900"
            style={{ top: hoverY! }}>{format(hoverPrice)}</span>
        )}
        {h && hover !== null && (
          <span className="num pointer-events-none absolute -translate-x-1/2 rounded-md bg-gray-200 px-1.5 py-0.5 text-[10px] font-semibold text-ink-900"
            style={{ left: geo.x(hover), top: Math.max(0, priceH - 18) }}>{timeLabel(h.t)}</span>
        )}
      </div>

      <div className="num mt-1 flex justify-between pr-16 text-[10px] text-gray-500">
        {Array.from(new Set(xTicks)).map((i) => <span key={i}>{timeLabel(data[i].t)}</span>)}
      </div>
      {overlays && overlays.length === 0 && <span className="sr-only">no server overlays</span>}
    </div>
  );
}
