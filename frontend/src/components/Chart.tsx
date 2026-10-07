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
  const pad = { l: 8, r: 8, t: 12, b: 22 };

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
  const stroke = up ? "#4ade80" : "#f87171";
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
      <div className="flex items-baseline justify-between text-xs text-gray-400 mb-1 h-5">
        {h ? (
          <>
            <span>{h.label || timeLabel(h.t)}</span>
            <span className="text-gray-100 font-medium">{format(h.v)}</span>
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
            <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {baseline !== undefined && (
          <line
            x1={pad.l} x2={W - pad.r} y1={geo.y(baseline)} y2={geo.y(baseline)}
            stroke="#4b5563" strokeDasharray="4 4" strokeWidth="1" vectorEffect="non-scaling-stroke"
          />
        )}
        <path d={geo.area} fill={`url(#${gid})`} />
        <path d={geo.line} fill="none" stroke={stroke} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {h && hover !== null && (
          <>
            <line
              x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={H - pad.b}
              stroke="#9ca3af" strokeWidth="1" vectorEffect="non-scaling-stroke"
            />
            <circle cx={geo.x(hover)} cy={geo.y(h.v)} r="4" fill={stroke} />
          </>
        )}
        {ticks.map((i, k) => (
          <text
            key={k}
            x={geo.x(i)}
            y={H - 6}
            fontSize="11"
            fill="#6b7280"
            textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}
          >
            {points[i].label || timeLabel(points[i].t)}
          </text>
        ))}
      </svg>
    </div>
  );
}
