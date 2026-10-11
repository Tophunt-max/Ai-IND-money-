// Option payoff at expiry for a set of legs (the Options page's strategy builder).

export interface Leg {
  id: string;
  type: "CE" | "PE";
  side: "BUY" | "SELL";
  strike: number;
  premium: number;
  lots: number;
}

/** P&L of one leg at expiry for an underlying price, per lot of *lotSize*. */
export function legPnl(leg: Leg, spot: number, lotSize: number): number {
  const intrinsic = leg.type === "CE" ? Math.max(0, spot - leg.strike) : Math.max(0, leg.strike - spot);
  const perUnit = leg.side === "BUY" ? intrinsic - leg.premium : leg.premium - intrinsic;
  return perUnit * lotSize * leg.lots;
}

export function payoff(legs: Leg[], spot: number, lotSize: number): number {
  return legs.reduce((s, l) => s + legPnl(l, spot, lotSize), 0);
}

export interface PayoffSummary {
  points: { x: number; y: number }[];
  maxProfit: number | null; // null: unlimited
  maxLoss: number | null;   // null: unlimited
  breakevens: number[];
  netPremium: number;       // + received, − paid
}

/** The payoff curve over ±*range* around *spot*, its extremes and break-evens. */
export function summarize(legs: Leg[], spot: number, lotSize: number, range = 0.12, steps = 120): PayoffSummary {
  const lo = spot * (1 - range);
  const hi = spot * (1 + range);
  const points: { x: number; y: number }[] = [];
  for (let i = 0; i <= steps; i++) {
    const x = lo + ((hi - lo) * i) / steps;
    points.push({ x, y: payoff(legs, x, lotSize) });
  }
  // Slopes beyond the strikes decide whether profit / loss is unlimited
  const strikes = legs.map((l) => l.strike);
  const far = Math.max(hi, ...strikes) * 2;
  const right = payoff(legs, far, lotSize) - payoff(legs, far / 1.5, lotSize);
  const ys = points.map((p) => p.y);
  const left0 = payoff(legs, 0, lotSize);
  const maxProfit = right > 1e-6 ? null : Math.max(...ys, left0);
  const maxLoss = right < -1e-6 ? null : Math.min(...ys, left0);
  const breakevens: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if ((a.y <= 0 && b.y > 0) || (a.y >= 0 && b.y < 0)) {
      breakevens.push(a.x + ((0 - a.y) * (b.x - a.x)) / (b.y - a.y));
    }
  }
  const netPremium = legs.reduce((s, l) => s + (l.side === "SELL" ? 1 : -1) * l.premium * l.lots * lotSize, 0);
  return { points, maxProfit, maxLoss, breakevens, netPremium };
}
