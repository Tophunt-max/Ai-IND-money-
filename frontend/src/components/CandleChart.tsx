"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface Candle {
  t: number; // unix seconds
  o: number | null;
  h: number | null;
  l: number | null;
  c: number;
  v?: number;
}

const UP = "#34d399";
const DOWN = "#fb7185";

/** Dependency-free candlestick chart with volume, a last-price line and an OHLC crosshair. */
export default function CandleChart({
  candles,
  height = 320,
  format = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 }),
  timeLabel = (t: number) => new Date(t * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  live = false,
}: {
  candles: Candle[];
  height?: number;
  format?: (v: number) => string;
  timeLabel?: (t: number) => string;
  live?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Candles with a missing open/high/low use the close (Yahoo sometimes leaves them empty)
  const data = useMemo(
    () => candles.map((k) => {
      const o = k.o ?? k.c;
      return { ...k, o, h: Math.max(k.h ?? k.c, o, k.c), l: Math.min(k.l ?? k.c, o, k.c), v: k.v || 0 };
    }),
    [candles],
  );

  const pad = { l: 6, r: 64, t: 10 };
  const volH = Math.round(height * 0.18);
  const priceH = height - volH - 8;

  const geo = useMemo(() => {
    if (data.length < 2) return null;
    let min = Math.min(...data.map((k) => k.l));
    let max = Math.max(...data.map((k) => k.h));
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const span = max - min;
    min -= span * 0.04;
    max += span * 0.04;
    const vmax = Math.max(1, ...data.map((k) => k.v));
    const plotW = width - pad.l - pad.r;
    const step = plotW / data.length;
    const body = Math.max(1, Math.min(14, step * 0.68));
    const x = (i: number) => pad.l + step * (i + 0.5);
    const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (priceH - pad.t);
    const vy = (v: number) => height - (v / vmax) * volH;
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + (max - min) * (1 - f));
    return { x, y, vy, body, step, ticks, plotW };
  }, [data, width, height, priceH, volH, pad.l, pad.r, pad.t]);

  if (!geo) return <div className="py-8 text-center text-sm text-gray-500">Not enough data for a chart.</div>;

  const last = data[data.length - 1];
  const prev = data.length > 1 ? data[data.length - 2].c : last.o;
  const lastUp = last.c >= prev;
  const h = hover !== null ? data[hover] : null;
  const shown = h || last;
  const change = shown.c - shown.o;

  const move = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    const i = Math.floor((clientX - r.left - pad.l) / geo.step);
    setHover(i >= 0 && i < data.length ? i : null);
  };

  const xTicks = [0, Math.floor(data.length / 3), Math.floor((2 * data.length) / 3), data.length - 1];

  return (
    <div>
      {/* OHLC read-out */}
      <div className="num mb-2 flex min-h-5 flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-gray-500">
        <span className="text-gray-400">{h ? timeLabel(h.t) : live ? "Live" : "Last"}</span>
        <span>O <b className="font-medium text-gray-200">{format(shown.o)}</b></span>
        <span>H <b className="font-medium text-gray-200">{format(shown.h)}</b></span>
        <span>L <b className="font-medium text-gray-200">{format(shown.l)}</b></span>
        <span>C <b className={`font-semibold ${change >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{format(shown.c)}</b></span>
        {shown.v > 0 && <span>Vol <b className="font-medium text-gray-300">{shown.v.toLocaleString("en-IN")}</b></span>}
      </div>

      <div ref={box} className="relative w-full touch-none select-none"
        onMouseMove={(e) => move(e.clientX)} onMouseLeave={() => setHover(null)}
        onTouchStart={(e) => move(e.touches[0].clientX)} onTouchMove={(e) => move(e.touches[0].clientX)}
        onTouchEnd={() => setHover(null)}>
        <svg width={width} height={height} className="block">
          {geo.ticks.map((v, i) => (
            <line key={i} x1={pad.l} x2={width - pad.r} y1={geo.y(v)} y2={geo.y(v)} stroke="rgba(255,255,255,0.05)" />
          ))}
          <line x1={pad.l} x2={width - pad.r} y1={priceH + 4} y2={priceH + 4} stroke="rgba(255,255,255,0.06)" />

          {data.map((k, i) => {
            const up = k.c >= k.o;
            const col = up ? UP : DOWN;
            const top = geo.y(Math.max(k.o, k.c));
            const bh = Math.max(1, Math.abs(geo.y(k.o) - geo.y(k.c)));
            const cx = geo.x(i);
            return (
              <g key={k.t} opacity={hover === null || hover === i ? 1 : 0.55}>
                <rect x={cx - geo.body / 2} y={geo.vy(k.v)} width={geo.body} height={height - geo.vy(k.v)} fill={col} opacity={0.22} />
                <line x1={cx} x2={cx} y1={geo.y(k.h)} y2={geo.y(k.l)} stroke={col} strokeWidth={1} />
                <rect x={cx - geo.body / 2} y={top} width={geo.body} height={bh} rx={geo.body > 4 ? 1 : 0}
                  fill={up ? col : col} fillOpacity={up ? 0.9 : 0.95} />
              </g>
            );
          })}

          {/* Last price line */}
          <line x1={pad.l} x2={width - pad.r} y1={geo.y(last.c)} y2={geo.y(last.c)}
            stroke={lastUp ? UP : DOWN} strokeDasharray="3 3" strokeOpacity={0.7} />

          {h && hover !== null && (
            <>
              <line x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={height} stroke="rgba(255,255,255,0.22)" />
              <line x1={pad.l} x2={width - pad.r} y1={geo.y(h.c)} y2={geo.y(h.c)} stroke="rgba(255,255,255,0.15)" />
            </>
          )}
        </svg>

        {/* Price axis */}
        {geo.ticks.map((v, i) => (
          <span key={i} className="num pointer-events-none absolute right-0 -translate-y-1/2 pr-1 text-[10px] text-gray-500"
            style={{ top: geo.y(v) }}>{format(v)}</span>
        ))}
        <span className={`num pointer-events-none absolute right-0 -translate-y-1/2 rounded-md px-1.5 py-0.5 text-[10px] font-semibold text-ink-950 ${lastUp ? "bg-emerald-400" : "bg-rose-400"}`}
          style={{ top: geo.y(last.c) }}>
          {live && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-ink-950 align-middle" />}
          {format(last.c)}
        </span>
      </div>

      <div className="num mt-1 flex justify-between pr-16 text-[11px] text-gray-500">
        {Array.from(new Set(xTicks)).map((i) => <span key={i}>{timeLabel(data[i].t)}</span>)}
      </div>
    </div>
  );
}
