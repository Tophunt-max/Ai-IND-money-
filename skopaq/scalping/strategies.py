"""Scalping entry strategies (long only), evaluated on each closed candle.

Each strategy looks at the day's closed candles of one instrument and returns a
``Setup`` (entry reference, stop, target, why) or None. They are pure functions of the
candles, so the live engine and the backtest run exactly the same code.

- ``vwap_pullback``: uptrend (EMA 9 > EMA 21, close above VWAP) and a candle that dips to
  VWAP and closes back above it, green. Without volume (the LTP feed) EMA 21 stands in
  for VWAP.
- ``ema_rsi``: EMA 9 crosses above EMA 21 on this candle with RSI(14) between 50 and 70
  and the close above EMA 21.
- ``orb``: opening range breakout — the first close above the high of the first
  ``orb_minutes`` (09:15 on), with a range between 0.3 % and 2.5 % of the price; once a
  day per instrument.
- ``range_reversal``: a green candle that tags the low of the last ``range_lookback``
  candles with RSI(14) below 40; the target is the middle of that range.

Stops sit below the setup's structure (with a quarter-ATR buffer), never closer than
0.4 ATR nor further than 2.5 ATR; targets are ``rr`` × risk unless the setup has its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable, Optional, Sequence

from skopaq.market.candles import Candle

SESSION_OPEN = time(9, 15)
STRATEGY_NAMES = ("vwap_pullback", "ema_rsi", "orb", "range_reversal")


@dataclass(frozen=True)
class Setup:
    strategy: str
    entry: float
    stop: float
    target: float
    reason: str

    @property
    def risk(self) -> float:
        return self.entry - self.stop

    @property
    def reward(self) -> float:
        return self.target - self.entry


@dataclass(frozen=True)
class StrategyParams:
    rr: float = 1.5
    orb_minutes: int = 15
    range_lookback: int = 30
    vwap_band_pct: float = 0.0015


# ── Indicator series (from the candles, so previous values are known) ────────


def ema_series(values: Sequence[float], period: int) -> list[float]:
    out: list[float] = []
    k = 2.0 / (period + 1)
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def rsi_last(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI of the last close; None until ``period`` changes are known."""
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def atr_last(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for prev, c in zip(candles, candles[1:]):
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def vwap_series(candles: Sequence[Candle]) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    pv = vol = 0.0
    for c in candles:
        if c.volume:
            pv += (c.high + c.low + c.close) / 3.0 * c.volume
            vol += c.volume
        out.append(pv / vol if vol else None)
    return out


# ── Helpers ──────────────────────────────────────────────────────────────────


def _bounded_stop(entry: float, structure_stop: float, atr: float) -> Optional[float]:
    """The structure's stop, kept 0.4–2.5 ATR below the entry; None when it is above."""
    if atr <= 0:
        return None
    stop = min(structure_stop, entry - 0.4 * atr)
    stop = max(stop, entry - 2.5 * atr)
    return round(stop, 2) if stop < entry else None


def _setup(name: str, entry: float, stop: Optional[float], target: Optional[float],
           params: StrategyParams, reason: str) -> Optional[Setup]:
    if stop is None or stop >= entry:
        return None
    if target is None:
        target = entry + params.rr * (entry - stop)
    target = round(target, 2)
    if target <= entry:
        return None
    return Setup(name, round(entry, 2), stop, target, reason)


# ── Strategies ───────────────────────────────────────────────────────────────


def vwap_pullback(candles: Sequence[Candle], p: StrategyParams) -> Optional[Setup]:
    if len(candles) < 22:
        return None
    closes = [c.close for c in candles]
    fast, slow = ema_series(closes, 9), ema_series(closes, 21)
    vwap = vwap_series(candles)
    last, prev = candles[-1], candles[-2]
    ref = vwap[-1] if vwap[-1] is not None else slow[-1]
    ref_prev = vwap[-2] if vwap[-2] is not None else slow[-2]
    label = "VWAP" if vwap[-1] is not None else "EMA21"
    atr = atr_last(candles)
    if atr is None or not (fast[-1] > slow[-1] and prev.close > ref_prev):
        return None
    touched = last.low <= ref * (1 + p.vwap_band_pct)
    if not (touched and last.close > ref and last.close > last.open):
        return None
    stop = _bounded_stop(last.close, min(last.low, ref) - 0.25 * atr, atr)
    return _setup("vwap_pullback", last.close, stop, None, p,
                  f"pullback to {label} {ref:.2f} in an uptrend, closed back above")


def ema_rsi(candles: Sequence[Candle], p: StrategyParams) -> Optional[Setup]:
    if len(candles) < 23:
        return None
    closes = [c.close for c in candles]
    fast, slow = ema_series(closes, 9), ema_series(closes, 21)
    if not (fast[-2] <= slow[-2] and fast[-1] > slow[-1]):
        return None
    rsi = rsi_last(closes)
    last = candles[-1]
    atr = atr_last(candles)
    if rsi is None or atr is None or not (50 <= rsi <= 70) or last.close <= slow[-1]:
        return None
    swing_low = min(c.low for c in candles[-5:])
    stop = _bounded_stop(last.close, swing_low - 0.25 * atr, atr)
    return _setup("ema_rsi", last.close, stop, None, p,
                  f"EMA9 crossed above EMA21, RSI {rsi:.0f}")


def _session_day(candles: Sequence[Candle]) -> list[Candle]:
    day = candles[-1].start.date()
    return [c for c in candles if c.start.date() == day]


def opening_range(candles: Sequence[Candle], minutes: int) -> Optional[tuple[float, float]]:
    """(high, low) of the first ``minutes`` of the session, once they are complete."""
    today = _session_day(candles)
    if not today:
        return None
    open_at = datetime.combine(today[-1].start.date(), SESSION_OPEN,
                               tzinfo=today[-1].start.tzinfo)
    end = open_at + timedelta(minutes=minutes)
    first = [c for c in today if open_at <= c.start < end]
    if not first or today[-1].start < end:
        return None
    return max(c.high for c in first), min(c.low for c in first)


def orb(candles: Sequence[Candle], p: StrategyParams) -> Optional[Setup]:
    rng = opening_range(candles, p.orb_minutes) if candles else None
    if rng is None or len(candles) < 2:
        return None
    high, low = rng
    today = _session_day(candles)
    if len(today) < 2:
        return None
    last, prev = today[-1], today[-2]
    open_end = datetime.combine(last.start.date(), SESSION_OPEN,
                                tzinfo=last.start.tzinfo) + timedelta(minutes=p.orb_minutes)
    if prev.start < open_end:
        prev_close_above = False   # the first candle after the range
    else:
        prev_close_above = prev.close > high
    width = high - low
    if not (last.close > high and not prev_close_above):
        return None
    if not (0.003 <= width / last.close <= 0.025):
        return None
    # Only the first breakout of the day: no earlier close above the range
    after = [c for c in today if c.start >= open_end]
    if any(c.close > high for c in after[:-1]):
        return None
    atr = atr_last(candles) or width / 2
    mid = (high + low) / 2
    stop = _bounded_stop(last.close, mid, atr)
    return _setup("orb", last.close, stop, last.close + width, p,
                  f"broke the {p.orb_minutes}-min opening range {low:.2f}–{high:.2f}")


def range_reversal(candles: Sequence[Candle], p: StrategyParams) -> Optional[Setup]:
    n = p.range_lookback
    if len(candles) < n + 2:
        return None
    window = candles[-n - 1:-1]
    support = min(c.low for c in window)
    resistance = max(c.high for c in window)
    last = candles[-1]
    rsi = rsi_last([c.close for c in candles])
    atr = atr_last(candles)
    if rsi is None or atr is None:
        return None
    if not (last.low <= support * 1.001 and last.close > last.open and rsi < 40):
        return None
    target = (support + resistance) / 2
    stop = _bounded_stop(last.close, min(last.low, support) - 0.25 * atr, atr)
    if stop is None or target - last.close < 1.2 * (last.close - stop):
        return None
    return _setup("range_reversal", last.close, stop, target, p,
                  f"bounced off the {n}-candle support {support:.2f}, RSI {rsi:.0f}")


STRATEGIES: dict[str, Callable[[Sequence[Candle], StrategyParams], Optional[Setup]]] = {
    "vwap_pullback": vwap_pullback,
    "ema_rsi": ema_rsi,
    "orb": orb,
    "range_reversal": range_reversal,
}


def evaluate(candles: Sequence[Candle], names: Sequence[str],
             params: StrategyParams) -> Optional[Setup]:
    """The first strategy of ``names`` (in that order) that has a setup."""
    for name in names:
        fn = STRATEGIES.get(name)
        if fn is None:
            continue
        setup = fn(candles, params)
        if setup is not None:
            return setup
    return None
