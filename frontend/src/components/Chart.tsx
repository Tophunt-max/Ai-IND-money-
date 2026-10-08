"use client";

import { useMemo, useRef, useState } from "react";

export interface Point {
  t: number; // unix seconds, or any increasing number
  v: number;
  label?: string;
}

/** Dependency-free SVG line/area chart with a touch/mouse crosshair. */
export default function Chart({
  points,
  height = 220,
  format = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 }),
  timeLabel = (t: number) =>
    new Date(t * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  baseline,
}: {
  points: Point[];
  height?: number;
  format?: (v: number) => string;
  timeLabel?: (t: number) => string;
  baseline?: number; // colour above/below this (default: first point)
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 600;
  const H = height;
  const pad = { l: 4, r: 4, t: 10, b: 6 };

  const geo = useMemo(() => {
    if (points.length < 2) return null;
    const vs = points.map((p) => p.v);
    let min = Math.min(...vs);
    let max = Math.max(...vs);
    if (baseline !== undefined) {
      min = Math.min(min, baseline);
      max = Math.max(max, baseline);
    }
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const x = (i: number) => pad.l + (i / (points.length - 1)) * (W - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (H - pad.t - pad.b);
    const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    const area = `${line} L${x(points.length - 1).toFixed(1)},${H - pad.b} L${x(0).toFixed(1)},${H - pad.b} Z`;
    return { min, max, x, y, line, area };
  }, [points, baseline, H]);

  if (!geo) {
    return <div className="text-sm text-gray-500 py-8 text-center">Not enough data for a chart.</div>;
  }

  const base = baseline ?? points[0].v;
  const last = points[points.length - 1].v;
  const up = last >= base;
  const stroke = up ? "#34d399" : "#fb7185";
  const gid = up ? "chart-up" : "chart-down";

  const move = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const frac = (clientX - box.left) / box.width;
    const i = Math.round(((frac * W - pad.l) / (W - pad.l - pad.r)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const h = hover !== null ? points[hover] : null;
  const ticks = [0, Math.floor((points.length - 1) / 2), points.length - 1];

  return (
    <div>
      <div className="num mb-2 flex h-5 items-baseline justify-between text-xs text-gray-500">
        {h ? (
          <>
            <span>{h.label || timeLabel(h.t)}</span>
            <span className="font-semibold text-white">{format(h.v)}</span>
          </>
        ) : (
          <>
            <span>Low {format(Math.min(...points.map((p) => p.v)))}</span>
            <span>High {format(Math.max(...points.map((p) => p.v)))}</span>
          </>
        )}
      </div>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full touch-none select-none"
        style={{ height }}
        preserveAspectRatio="none"
        onMouseMove={(e) => move(e.clientX)}
        onMouseLeave={() => setHover(null)}
        onTouchStart={(e) => move(e.touches[0].clientX)}
        onTouchMove={(e) => move(e.touches[0].clientX)}
        onTouchEnd={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={pad.l} x2={W - pad.r} y1={pad.t + f * (H - pad.t - pad.b)} y2={pad.t + f * (H - pad.t - pad.b)}
            stroke="rgba(255,255,255,0.04)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ))}
        {baseline !== undefined && (
          <line
            x1={pad.l} x2={W - pad.r} y1={geo.y(baseline)} y2={geo.y(baseline)}
            stroke="rgba(255,255,255,0.18)" strokeDasharray="4 4" strokeWidth="1" vectorEffect="non-scaling-stroke"
          />
        )}
        <path d={geo.area} fill={`url(#${gid})`} />
        <path d={geo.line} fill="none" stroke={stroke} strokeWidth="2.25" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {h && hover !== null && (
          <>
            <line
              x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={H - pad.b}
              stroke="rgba(255,255,255,0.25)" strokeWidth="1" vectorEffect="non-scaling-stroke"
            />
            <circle cx={geo.x(hover)} cy={geo.y(h.v)} r="4.5" fill={stroke} stroke="#05070d" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>
      <div className="num mt-1 flex justify-between text-[11px] text-gray-500">
        {ticks.map((i, k) => <span key={k}>{points[i].label || timeLabel(points[i].t)}</span>)}
      </div>
    </div>
  );
}
