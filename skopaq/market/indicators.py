"""Deterministic technical indicators over closed candles.

Every function takes the candles (or values) that were available at the decision time and
returns the value *as of the last one*: nothing looks ahead, and an incomplete history
returns ``None`` instead of a guess. Inputs are any objects with ``open``, ``high``,
``low``, ``close`` and (optional) ``volume`` attributes, e.g. ``skopaq.market.candles.Candle``.

Wilder smoothing is used for RSI, ATR and ADX (the definitions charting platforms use);
it matches ``skopaq.market.candles.Indicators`` and ``skopaq.scalping.strategies``.

Parameters are documented on each function. Indicators are evidence for a strategy, not a
signal: ``skopaq.strategy.signal.qualify`` decides whether a setup is traded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Optional, Sequence


def _closes(candles: Sequence[Any]) -> list[float]:
    return [float(c.close) for c in candles]


def sma(values: Sequence[float], period: int) -> Optional[float]:
    """Simple mean of the last ``period`` values."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """EMA of every value, seeded with the SMA of the first ``period`` values (the first
    ``period - 1`` entries are that running mean, so the list has the input's length)."""
    if period <= 0 or not values:
        return []
    k = 2.0 / (period + 1)
    out: list[float] = []
    running = 0.0
    for i, v in enumerate(values):
        v = float(v)
        if i < period:
            running += v
            out.append(running / (i + 1))
        else:
            out.append(out[-1] + k * (v - out[-1]))
    return out


def ema(values: Sequence[float], period: int) -> Optional[float]:
    """EMA as of the last value; ``None`` until ``period`` values exist."""
    if period <= 0 or len(values) < period:
        return None
    return ema_series(values, period)[-1]


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder RSI (0-100) of the last close; needs ``period + 1`` closes. A flat series
    is 50; only gains is 100."""
    if period <= 0 or len(closes) < period + 1:
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
        return 50.0 if avg_g == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def true_ranges(candles: Sequence[Any]) -> list[float]:
    """True range of each candle after the first (high-low widened by the prior close)."""
    out = []
    for prev, c in zip(candles, candles[1:]):
        out.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    return out


def atr(candles: Sequence[Any], period: int = 14) -> Optional[float]:
    """Wilder ATR of the last candle; needs ``period + 1`` candles."""
    trs = true_ranges(candles)
    if period <= 0 or len(trs) < period:
        return None
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


@dataclass(frozen=True)
class Macd:
    macd: float
    signal: float
    histogram: float


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Optional[Macd]:
    """MACD line (EMA fast - EMA slow), its EMA signal line and the histogram. Needs
    ``slow + signal - 1`` closes so the signal line is built from real MACD values."""
    if not (0 < fast < slow) or signal <= 0 or len(closes) < slow + signal - 1:
        return None
    fast_s, slow_s = ema_series(closes, fast), ema_series(closes, slow)
    line = [f - s for f, s in zip(fast_s, slow_s)][slow - 1:]
    sig = ema_series(line, signal)[-1]
    return Macd(macd=line[-1], signal=sig, histogram=line[-1] - sig)


@dataclass(frozen=True)
class Adx:
    adx: float
    plus_di: float
    minus_di: float


def adx(candles: Sequence[Any], period: int = 14) -> Optional[Adx]:
    """Wilder ADX with +DI/-DI. Needs ``2 * period + 1`` candles (period bars for the DI
    smoothing, then period DX values for the first ADX)."""
    if period <= 0 or len(candles) < 2 * period + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for prev, c in zip(candles, candles[1:]):
        up, down = c.high - prev.high, prev.low - c.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    tr_s, p_s, m_s = sum(trs[:period]), sum(plus_dm[:period]), sum(minus_dm[:period])

    def dx_of(tr_v: float, p_v: float, m_v: float) -> tuple[float, float, float]:
        if tr_v <= 0:
            return 0.0, 0.0, 0.0
        pdi, mdi = 100 * p_v / tr_v, 100 * m_v / tr_v
        total = pdi + mdi
        # Directional movement under 1 % of the true range is no direction: two
        # near-zero DIs would otherwise give a DX of 100
        return (100 * abs(pdi - mdi) / total if total >= 1.0 else 0.0), pdi, mdi

    dxs = []
    dx, pdi, mdi = dx_of(tr_s, p_s, m_s)
    dxs.append(dx)
    for i in range(period, len(trs)):
        tr_s = tr_s - tr_s / period + trs[i]
        p_s = p_s - p_s / period + plus_dm[i]
        m_s = m_s - m_s / period + minus_dm[i]
        dx, pdi, mdi = dx_of(tr_s, p_s, m_s)
        dxs.append(dx)
    if len(dxs) < period:
        return None
    value = sum(dxs[:period]) / period
    for d in dxs[period:]:
        value = (value * (period - 1) + d) / period
    return Adx(adx=value, plus_di=pdi, minus_di=mdi)


@dataclass(frozen=True)
class Bands:
    middle: float
    upper: float
    lower: float

    @property
    def width_pct(self) -> float:
        """Band width as a fraction of the middle band (volatility contraction/expansion)."""
        return (self.upper - self.lower) / self.middle if self.middle else 0.0

    def percent_b(self, price: float) -> Optional[float]:
        span = self.upper - self.lower
        return (price - self.lower) / span if span > 0 else None


def bollinger(closes: Sequence[float], period: int = 20, k: float = 2.0) -> Optional[Bands]:
    """Bollinger Bands: SMA ± k population standard deviations of the last ``period``."""
    if period <= 1 or len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    sd = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    return Bands(middle=mid, upper=mid + k * sd, lower=mid - k * sd)


def roc(closes: Sequence[float], period: int = 10) -> Optional[float]:
    """Rate of change (fraction) over ``period`` closes."""
    if period <= 0 or len(closes) <= period or closes[-period - 1] == 0:
        return None
    return closes[-1] / closes[-period - 1] - 1.0


def relative_volume(candles: Sequence[Any], period: int = 20) -> Optional[float]:
    """Last candle's volume over the mean of the ``period`` candles before it. ``None``
    when volume is missing or the baseline is zero."""
    if period <= 0 or len(candles) < period + 1:
        return None
    vols = [c.volume for c in candles[-period - 1:]]
    if any(v is None for v in vols):
        return None
    base = sum(vols[:-1]) / period
    return vols[-1] / base if base > 0 else None


def swing_points(candles: Sequence[Any], left: int = 2,
                 right: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed swing highs and lows as ``(index, price)``. A pivot needs ``right``
    later candles to confirm it, so the newest ``right`` candles can never be pivots:
    no pivot is ever reported before it was knowable."""
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        h, lo = candles[i].high, candles[i].low
        window = candles[i - left:i + right + 1]
        if all(h >= c.high for c in window) and sum(c.high == h for c in window) == 1:
            highs.append((i, h))
        if all(lo <= c.low for c in window) and sum(c.low == lo for c in window) == 1:
            lows.append((i, lo))
    return highs, lows


@dataclass(frozen=True)
class Levels:
    support: Optional[float]
    resistance: Optional[float]


def support_resistance(candles: Sequence[Any], price: Optional[float] = None,
                       left: int = 2, right: int = 2, tolerance_pct: float = 0.002) -> Levels:
    """Nearest confirmed swing low below and swing high above ``price`` (default: the last
    close). Pivots within ``tolerance_pct`` of the price count as at it."""
    if not candles:
        return Levels(None, None)
    price = float(candles[-1].close if price is None else price)
    highs, lows = swing_points(candles, left, right)
    tol = price * tolerance_pct
    below = [p for _, p in lows if p <= price + tol]
    above = [p for _, p in highs if p >= price - tol]
    return Levels(max(below) if below else None, min(above) if above else None)


def _day_of(c: Any) -> Optional[date]:
    start = getattr(c, "start", None)
    return start.date() if isinstance(start, datetime) else None


def previous_day_high_low(candles: Sequence[Any]) -> Optional[tuple[float, float]]:
    """High and low of the last full day before the last candle's day (candles carry
    ``start``, an IST datetime)."""
    if not candles:
        return None
    today = _day_of(candles[-1])
    if today is None:
        return None
    prior = [c for c in candles if (_day_of(c) or today) < today]
    if not prior:
        return None
    last_day = _day_of(prior[-1])
    day = [c for c in prior if _day_of(c) == last_day]
    return max(c.high for c in day), min(c.low for c in day)


def opening_range(candles: Sequence[Any], minutes: int = 15,
                  session_open: time = time(9, 15)) -> Optional[tuple[float, float]]:
    """High and low of today's first ``minutes`` minutes, once that window is complete
    (a candle at or after its end exists); ``None`` before then."""
    if not candles:
        return None
    today = _day_of(candles[-1])
    if today is None:
        return None
    start = datetime.combine(today, session_open, tzinfo=candles[-1].start.tzinfo)
    end = start.timestamp() + minutes * 60
    window = [c for c in candles if _day_of(c) == today
              and start.timestamp() <= c.start.timestamp() < end]
    if not window or candles[-1].start.timestamp() < end:
        return None
    return max(c.high for c in window), min(c.low for c in window)


@dataclass(frozen=True)
class Snapshot:
    """The indicator values of one decision, as stored on a signal (all floats or None)."""

    close: float
    ema_fast: Optional[float]
    ema_slow: Optional[float]
    sma_long: Optional[float]
    rsi: Optional[float]
    atr: Optional[float]
    atr_pct: Optional[float]
    adx: Optional[float]
    plus_di: Optional[float]
    minus_di: Optional[float]
    macd_hist: Optional[float]
    bb_width_pct: Optional[float]
    bb_percent_b: Optional[float]
    roc: Optional[float]
    rel_volume: Optional[float]
    support: Optional[float]
    resistance: Optional[float]

    def as_dict(self) -> dict[str, Optional[float]]:
        return {k: (None if v is None else round(float(v), 6))
                for k, v in self.__dict__.items()}


def snapshot(candles: Sequence[Any], *, fast: int = 9, slow: int = 21,
             long: int = 50) -> Optional[Snapshot]:
    """Every indicator of the last closed candle (``None`` with no candles)."""
    if not candles:
        return None
    closes = _closes(candles)
    a = atr(candles)
    d = adx(candles)
    m = macd(closes)
    bb = bollinger(closes)
    lv = support_resistance(candles)
    last = closes[-1]
    return Snapshot(
        close=last, ema_fast=ema(closes, fast), ema_slow=ema(closes, slow),
        sma_long=sma(closes, long), rsi=rsi(closes), atr=a,
        atr_pct=(a / last if a is not None and last else None),
        adx=d.adx if d else None, plus_di=d.plus_di if d else None,
        minus_di=d.minus_di if d else None, macd_hist=m.histogram if m else None,
        bb_width_pct=bb.width_pct if bb else None,
        bb_percent_b=bb.percent_b(last) if bb else None,
        roc=roc(closes), rel_volume=relative_volume(candles),
        support=lv.support, resistance=lv.resistance,
    )
