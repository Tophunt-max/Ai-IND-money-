"""Out-of-sample evaluation of a model gate on net trade outcomes, and feature drift.

``evaluate_gate`` compares, on the same unseen examples: the baseline (take every
QUALIFIED setup, i.e. the rule strategy alone) and the model gate (take a setup only when
the predicted probability ≥ threshold). Metrics are on *net* P&L after charges and the
backtest's slippage: trades, net, expectancy with a bootstrap 90 % interval, profit factor,
max drawdown, win rate, Brier score vs the base rate, calibration.

The threshold is chosen on the validation set (``choose_threshold``), never on test.
``psi`` measures drift between training and recent feature distributions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from skopaq.learning.dataset import Example
from skopaq.learning.model import LogisticModel, brier, calibration_bins


@dataclass
class TradeStats:
    trades: int
    net: float
    expectancy: Optional[float]
    expectancy_ci90: Optional[tuple[float, float]]
    profit_factor: Optional[float]
    max_drawdown: float
    win_rate: Optional[float]

    def as_dict(self) -> dict:
        return {"trades": self.trades, "net": round(self.net, 2),
                "expectancy": None if self.expectancy is None else round(self.expectancy, 2),
                "expectancy_ci90": None if self.expectancy_ci90 is None
                else [round(x, 2) for x in self.expectancy_ci90],
                "profit_factor": None if self.profit_factor is None
                else round(self.profit_factor, 3),
                "max_drawdown": round(self.max_drawdown, 2),
                "win_rate": None if self.win_rate is None else round(self.win_rate, 4)}


def trade_stats(pnls: Sequence[float], *, boot: int = 1000, seed: int = 7) -> TradeStats:
    pnls = [float(p) for p in pnls]
    n = len(pnls)
    if not n:
        return TradeStats(0, 0.0, None, None, None, 0.0, None)
    eq = peak = dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p <= 0)
    ci = None
    if n >= 10:
        rng = np.random.default_rng(seed)          # fixed seed: reproducible reports
        arr = np.asarray(pnls)
        means = arr[rng.integers(0, n, size=(boot, n))].mean(axis=1)
        ci = (float(np.percentile(means, 5)), float(np.percentile(means, 95)))
    return TradeStats(n, sum(pnls), sum(pnls) / n, ci,
                      wins / losses if losses > 0 else None, dd,
                      sum(p > 0 for p in pnls) / n)


def choose_threshold(model: LogisticModel, val: Sequence[Example],
                     grid: Sequence[float] = (0.4, 0.45, 0.5, 0.55, 0.6, 0.65),
                     min_trades: int = 20) -> float:
    """The validation threshold with the best net P&L that keeps ≥ ``min_trades``."""
    if not val:
        return 0.5
    probs = model.predict_proba(val)
    best, best_net = 0.5, -math.inf
    for t in grid:
        taken = [e.net_pnl for e, p in zip(val, probs) if p >= t and e.net_pnl is not None]
        if len(taken) >= min_trades and sum(taken) > best_net:
            best, best_net = t, sum(taken)
    return best


def evaluate_gate(model: Optional[LogisticModel], test: Sequence[Example],
                  threshold: float) -> dict:
    labels = [int(e.label) for e in test if e.label is not None]
    base = trade_stats([e.net_pnl for e in test if e.net_pnl is not None])
    out = {"samples": len(test), "threshold": threshold, "baseline": base.as_dict()}
    rate = sum(labels) / len(labels) if labels else None
    out["base_rate"] = rate
    out["brier_base_rate"] = brier([rate] * len(labels), labels) if labels else None
    if model is None or not test:
        return out
    probs = model.predict_proba(test)
    gated = trade_stats([e.net_pnl for e, p in zip(test, probs)
                         if p >= threshold and e.net_pnl is not None])
    out.update(model=gated.as_dict(), brier=brier(probs, labels),
               calibration=calibration_bins(probs, labels))
    return out


def psi(expected: Sequence[float], actual: Sequence[float], bins: int = 10) -> Optional[float]:
    """Population stability index between two samples of one feature (NaN dropped).
    < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 significant drift."""
    e = np.asarray([x for x in expected if x == x], float)
    a = np.asarray([x for x in actual if x == x], float)
    if len(e) < 20 or len(a) < 20:
        return None
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges)[0] / len(e)
    pa = np.histogram(a, edges)[0] / len(a)
    pe, pa = np.clip(pe, 1e-4, None), np.clip(pa, 1e-4, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def drift_report(train: Sequence[Example], recent: Sequence[Example], names: Sequence[str],
                 alert_at: float = 0.25) -> dict:
    out = {}
    for n in names:
        v = psi([e.features.get(n, math.nan) for e in train],
                [e.features.get(n, math.nan) for e in recent])
        if v is not None:
            out[n] = round(v, 4)
    alerts = sorted(k for k, v in out.items() if v > alert_at)
    return {"psi": out, "alerts": alerts, "recent_samples": len(recent)}
