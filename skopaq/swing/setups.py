"""Swing setups on daily candles, their point-in-time features, and the trade simulator
the swing model learns from.

A setup is a rule on closed daily bars that proposes a long (``Setup``: entry at the last
close, an ATR stop, an R-multiple target). The Adaptive AI then decides which of these to
take (``skopaq.swing.model``). Every value at bar ``i`` uses bars ``≤ i`` only.

``simulate`` replays one setup the way the live swing book trades it: buy at the next
open with slippage, then each day the stop first (a gap below fills at the open), then
the target, and a time exit after ``max_hold_days``; the P&L is net of the delivery
charges (``finance.charges``, product ``equity_delivery``; a CNC bought and sold the same
day costs less, so this is conservative).

``max_hold_days`` is 1: the live book does not carry positions overnight (the position
monitor and the daemon's CLOSING sell everything from 15:20 IST), so the model learns the
outcome the live book can actually get — entry at the open, stop / target during the day,
else out at the close. Training on longer holds would teach it trades it never makes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np

from skopaq.finance.charges import EQUITY_DELIVERY, round_trip

SETUPS = ("trend_pullback", "breakout", "oversold_bounce")
WARMUP = 200  # bars before the first decision (SMA 200)


@dataclass(frozen=True)
class SwingParams:
    atr_mult: float = 2.0  # stop distance in ATRs (the position sizer's multiple)
    rr: float = 2.0  # target as a multiple of the risk
    max_hold_days: int = 1  # = the live exit policy (sold the same day): see above
    slippage_pct: float = 0.001
    notional_inr: float = 100_000.0


@dataclass(frozen=True)
class Setup:
    name: str
    entry: float
    stop: float
    target: float
    reason: str

    @property
    def risk(self) -> float:
        return self.entry - self.stop


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.empty_like(x)
    k = 2.0 / (n + 1)
    for i, v in enumerate(x):
        out[i] = v if i == 0 else out[i - 1] + k * (v - out[i - 1])
    return out


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if len(x) < n:
        return out
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


class Series:
    """Indicator arrays of one symbol's daily candles (computed once)."""

    def __init__(self, candles: Sequence[Any]) -> None:
        self.candles = list(candles)
        self.o = np.array([c.open for c in candles], float)
        self.h = np.array([c.high for c in candles], float)
        self.l = np.array([c.low for c in candles], float)
        self.c = np.array([c.close for c in candles], float)
        self.v = np.array([float(c.volume or 0) for c in candles], float)
        n = len(self.c)
        self.ema20 = _ema(self.c, 20)
        self.sma20, self.sma50, self.sma200 = _sma(self.c, 20), _sma(self.c, 50), _sma(self.c, 200)
        prev = np.concatenate([[self.c[0]], self.c[:-1]]) if n else self.c
        tr = np.maximum(self.h - self.l, np.maximum(np.abs(self.h - prev), np.abs(self.l - prev)))
        self.atr = np.full(n, np.nan)
        if n > 1:
            self.atr[1:] = _wilder(tr[1:], 14)
        d = np.diff(self.c, prepend=self.c[0]) if n else self.c
        up, dn = _wilder(np.maximum(d, 0), 14), _wilder(np.maximum(-d, 0), 14)
        with np.errstate(divide="ignore", invalid="ignore"):
            self.rsi = np.where(dn > 0, 100 - 100 / (1 + up / dn), np.where(up > 0, 100.0, 50.0))
        self.rsi[np.isnan(up)] = np.nan
        self.vol20 = _sma(self.v, 20)

    def __len__(self) -> int:
        return len(self.c)

    def features(self, i: int) -> dict[str, float]:
        """Point-in-time features of bar ``i`` (closed)."""
        c = self.c

        def ret(k: int) -> float:
            return float(c[i] / c[i - k] - 1) if i >= k and c[i - k] > 0 else math.nan

        lo20, hi20 = self.l[max(0, i - 19) : i + 1].min(), self.h[max(0, i - 19) : i + 1].max()
        rng = self.h[i] - self.l[i]
        return {
            "ret_1": ret(1),
            "ret_5": ret(5),
            "ret_20": ret(20),
            "ret_60": ret(60),
            "rsi": float(self.rsi[i]),
            "atr_pct": float(self.atr[i] / c[i]),
            "dist_ema20": float(c[i] / self.ema20[i] - 1),
            "dist_sma50": float(c[i] / self.sma50[i] - 1),
            "dist_sma200": float(c[i] / self.sma200[i] - 1),
            "sma50_slope": float(self.sma50[i] / self.sma50[i - 10] - 1) if i >= 10 else math.nan,
            "vol_ratio": float(self.v[i] / self.vol20[i]) if self.vol20[i] else math.nan,
            "range_pos20": float((c[i] - lo20) / (hi20 - lo20)) if hi20 > lo20 else 0.5,
            "close_pos": float((c[i] - self.l[i]) / rng) if rng > 0 else 0.5,
            "gap": float(self.o[i] / c[i - 1] - 1) if i >= 1 else 0.0,
        }

    # ── setups ──────────────────────────────────────────────────────────
    def setup(self, i: int, p: SwingParams = SwingParams()) -> Optional[Setup]:
        """The first setup that fires on bar ``i`` (in ``SETUPS`` order), or None."""
        if i < WARMUP or i >= len(self) or not np.isfinite(self.atr[i]) or self.atr[i] <= 0:
            return None
        c, h, lo, o = self.c, self.h, self.l, self.o
        name, why = None, ""
        if (
            c[i] > self.sma50[i] > self.sma200[i]
            and self.ema20[i] > self.sma50[i]
            and min(lo[i], lo[i - 1]) <= self.ema20[i] * 1.01
            and c[i] > self.ema20[i]
            and c[i] > o[i]
            and 40 <= self.rsi[i] <= 65
        ):
            name, why = "trend_pullback", f"uptrend pullback to EMA20, RSI {self.rsi[i]:.0f}"
        elif (
            c[i] > h[i - 20 : i].max()
            and self.vol20[i]
            and self.v[i] > 1.5 * self.vol20[i]
            and (c[i] - lo[i]) >= 0.7 * (h[i] - lo[i])
            and c[i] > self.sma50[i]
        ):
            name, why = "breakout", f"20-day high on {self.v[i] / self.vol20[i]:.1f}x volume"
        elif c[i] > self.sma200[i] and self.rsi[i - 1] < 35 and c[i] > h[i - 1]:
            name, why = "oversold_bounce", f"bounce from RSI {self.rsi[i - 1]:.0f} above SMA200"
        if name is None:
            return None
        entry = float(c[i])
        stop = round(entry - p.atr_mult * float(self.atr[i]), 2)
        if stop <= 0 or stop >= entry:
            return None
        return Setup(name, round(entry, 2), stop, round(entry + p.rr * (entry - stop), 2), why)


def simulate(s: Series, i: int, setup: Setup, p: SwingParams = SwingParams()) -> Optional[dict]:
    """Net outcome of taking ``setup`` decided on bar ``i`` (None: no next bar)."""
    if i + 1 >= len(s):
        return None
    fill = s.o[i + 1] * (1 + p.slippage_pct)
    shift = fill - setup.entry  # the levels keep their distance
    stop, target = setup.stop + shift, setup.target + shift
    qty = max(1, int(p.notional_inr // fill))
    exit_px, why, held = None, "time", 0
    for j in range(i + 1, min(len(s), i + 1 + p.max_hold_days)):
        held = j - i
        if s.l[j] <= stop:
            exit_px, why = min(s.o[j], stop) * (1 - p.slippage_pct), "stop"
            break
        if s.h[j] >= target:
            exit_px, why = max(s.o[j], target) * (1 - p.slippage_pct), "target"
            break
        exit_px = s.c[j] * (1 - p.slippage_pct)
    if exit_px is None:
        return None
    if why == "time" and held < p.max_hold_days and i + 1 + p.max_hold_days > len(s):
        return None  # not finished in the data: unknown
    gross = (exit_px - fill) * qty
    charges = round_trip(EQUITY_DELIVERY, fill, exit_px, qty).total
    return {
        "net": float(gross - charges),
        "gross": float(gross),
        "charges": float(charges),
        "exit": why,
        "held_days": held,
        "qty": qty,
        "fill": float(fill),
    }
