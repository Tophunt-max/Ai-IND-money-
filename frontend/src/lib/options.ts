/** Put-call ratio of open interest, and the max-pain strike (where option buyers lose most). */
export function chainStats(calls: { strike: number; oi: number }[], puts: { strike: number; oi: number }[]) {
  const callOi = calls.reduce((s, c) => s + (c.oi || 0), 0);
  const putOi = puts.reduce((s, p) => s + (p.oi || 0), 0);
  const strikes = Array.from(new Set([...calls, ...puts].map((x) => x.strike))).sort((a, b) => a - b);
  let maxPain: number | null = null;
  let best = Infinity;
  for (const s of strikes) {
    const pain = calls.reduce((t, c) => t + (c.oi || 0) * Math.max(0, s - c.strike), 0)
      + puts.reduce((t, p) => t + (p.oi || 0) * Math.max(0, p.strike - s), 0);
    if (pain < best) { best = pain; maxPain = s; }
  }
  return { pcr: callOi ? putOi / callOi : null, callOi, putOi, maxPain };
}
