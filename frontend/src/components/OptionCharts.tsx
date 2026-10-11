"use client";

import { useEffect, useRef, useState } from "react";

import { inr } from "@/lib/api";
import type { PayoffSummary } from "@/lib/payoff";
import { useThemeColors } from "@/lib/themeColors";

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(320);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, w };
}

/** Implied volatility by strike for calls and puts, with the spot marked. */
export function IvSmile({ calls, puts, spot, height = 200 }: {
  calls: { strike: number; iv: number }[]; puts: { strike: number; iv: number }[]; spot: number; height?: number;
}) {
  const t = useThemeColors();
  const { ref, w } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const c = calls.filter((x) => x.iv > 0).sort((a, b) => a.strike - b.strike);
  const p = puts.filter((x) => x.iv > 0).sort((a, b) => a.strike - b.strike);
  const all = [...c, ...p];
  if (all.length < 3) return <div ref={ref} className="py-8 text-center text-sm text-gray-500">Not enough IV data.</div>;
  const pad = { l: 36, r: 8, t: 10, b: 22 };
  const xs = all.map((x) => x.strike);
  const ys = all.map((x) => x.iv);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys) * 0.95, y1 = Math.max(...ys) * 1.05;
  const x = (v: number) => pad.l + ((v - x0) / (x1 - x0 || 1)) * (w - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - y0) / (y1 - y0 || 1)) * (height - pad.t - pad.b);
  const line = (pts: { strike: number; iv: number }[]) => pts.map((q, i) => `${i ? "L" : "M"}${x(q.strike).toFixed(1)},${y(q.iv).toFixed(1)}`).join(" ");
  const strikes = Array.from(new Set(xs)).sort((a, b) => a - b);
  const hs = hover != null ? strikes[hover] : null;
  return (
    <div ref={ref} className="relative">
      <div className="num mb-1 flex min-h-5 gap-3 text-[11px] text-gray-500">
        {hs != null ? <>
          <span>Strike <b className="text-gray-200">{hs}</b></span>
          <span className="text-rose-400">CE {c.find((q) => q.strike === hs)?.iv.toFixed(1) ?? "—"}%</span>
          <span className="text-emerald-400">PE {p.find((q) => q.strike === hs)?.iv.toFixed(1) ?? "—"}%</span>
        </> : <><span className="inline-flex items-center gap-1"><span className="h-0.5 w-3 rounded bg-rose-400" /> Calls</span><span className="inline-flex items-center gap-1"><span className="h-0.5 w-3 rounded bg-emerald-400" /> Puts</span><span>IV %</span></>}
      </div>
      <svg width={w} height={height} className="block" role="img" aria-label="IV smile"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const v = x0 + ((e.clientX - r.left - pad.l) / (w - pad.l - pad.r)) * (x1 - x0);
          let best = 0;
          strikes.forEach((s, i) => { if (Math.abs(s - v) < Math.abs(strikes[best] - v)) best = i; });
          setHover(best);
        }} onMouseLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((f) => {
          const v = y0 + (y1 - y0) * f;
          return <g key={f}><line x1={pad.l} x2={w - pad.r} y1={y(v)} y2={y(v)} stroke={t.grid} /><text x={pad.l - 4} y={y(v) + 3} fontSize={10} textAnchor="end" fill={t.muted}>{v.toFixed(0)}</text></g>;
        })}
        <line x1={x(spot)} x2={x(spot)} y1={pad.t} y2={height - pad.b} stroke={t.accent} strokeDasharray="4 3" />
        <text x={x(spot) + 3} y={pad.t + 9} fontSize={10} fill={t.accent}>spot</text>
        <path d={line(c)} fill="none" stroke={t.down} strokeWidth={1.8} />
        <path d={line(p)} fill="none" stroke={t.up} strokeWidth={1.8} />
        {hs != null && <line x1={x(hs)} x2={x(hs)} y1={pad.t} y2={height - pad.b} stroke={t.axis} strokeDasharray="2 3" />}
        <text x={pad.l} y={height - 6} fontSize={10} fill={t.muted}>{x0}</text>
        <text x={w - pad.r} y={height - 6} fontSize={10} textAnchor="end" fill={t.muted}>{x1}</text>
      </svg>
    </div>
  );
}

/** Payoff at expiry: profit area green, loss red, spot and break-evens marked. */
export function PayoffChart({ s, spot, height = 240 }: { s: PayoffSummary; spot: number; height?: number }) {
  const t = useThemeColors();
  const { ref, w } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pts = s.points;
  if (pts.length < 2) return <div ref={ref} />;
  const pad = { l: 56, r: 10, t: 12, b: 22 };
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const x0 = xs[0], x1 = xs[xs.length - 1];
  let y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const span = y1 - y0;
  y0 -= span * 0.08; y1 += span * 0.08;
  const x = (v: number) => pad.l + ((v - x0) / (x1 - x0)) * (w - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - y0) / (y1 - y0)) * (height - pad.t - pad.b);
  const zero = y(0);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const area = `${line} L${x(x1)},${zero} L${x(x0)},${zero} Z`;
  const h = hover != null ? pts[hover] : null;
  return (
    <div ref={ref}>
      <div className="num mb-1 min-h-5 text-[11px] text-gray-500">
        {h ? <>At expiry {h.x.toFixed(0)}: <b className={h.y >= 0 ? "text-emerald-400" : "text-rose-400"}>{inr(h.y, 0)}</b></> : "Hover the curve for the P&L at an expiry price"}
      </div>
      <svg width={w} height={height} className="block" role="img" aria-label="payoff chart"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left - pad.l) / (w - pad.l - pad.r)) * (pts.length - 1));
          setHover(Math.max(0, Math.min(pts.length - 1, i)));
        }} onMouseLeave={() => setHover(null)}>
        <defs>
          <clipPath id="po-up"><rect x={0} y={0} width={w} height={zero} /></clipPath>
          <clipPath id="po-down"><rect x={0} y={zero} width={w} height={height} /></clipPath>
        </defs>
        {[y0 + span * 0.08, 0, y1 - span * 0.08].map((v, i) => (
          <g key={i}><line x1={pad.l} x2={w - pad.r} y1={y(v)} y2={y(v)} stroke={v === 0 ? t.axis : t.grid} /><text x={pad.l - 4} y={y(v) + 3} fontSize={10} textAnchor="end" fill={t.muted}>{inr(v, 0)}</text></g>
        ))}
        <path d={area} fill={t.up} opacity={0.18} clipPath="url(#po-up)" />
        <path d={area} fill={t.down} opacity={0.18} clipPath="url(#po-down)" />
        <path d={line} fill="none" stroke={t.text} strokeWidth={1.8} />
        <line x1={x(spot)} x2={x(spot)} y1={pad.t} y2={height - pad.b} stroke={t.accent} strokeDasharray="4 3" />
        <text x={x(spot) + 3} y={pad.t + 9} fontSize={10} fill={t.accent}>spot {spot.toFixed(0)}</text>
        {s.breakevens.map((b) => (
          <g key={b}><circle cx={x(b)} cy={zero} r={3.5} fill={t.amber} /><text x={x(b)} y={height - 6} fontSize={10} textAnchor="middle" fill={t.amber}>{b.toFixed(0)}</text></g>
        ))}
        {h && <><line x1={x(h.x)} x2={x(h.x)} y1={pad.t} y2={height - pad.b} stroke={t.axis} strokeDasharray="2 3" /><circle cx={x(h.x)} cy={y(h.y)} r={4} fill={h.y >= 0 ? t.up : t.down} /></>}
      </svg>
    </div>
  );
}
