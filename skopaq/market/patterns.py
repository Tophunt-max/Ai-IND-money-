"""Deterministic candlestick-pattern recognition over closed candles.

``anatomy(c)`` measures one candle (body %, wick %, range); ``detect(candles)`` reports
the patterns the *last* closed candle completes, using only it and earlier candles. A
malformed candle (high below the open/close, non-positive or non-finite prices) is never
classified: ``validate_ohlc`` says why.

A pattern is evidence, not a trade signal: it carries no direction guarantee and is
weighed with trend, regime, costs and risk in ``skopaq.strategy.signal.qualify``. When
bullish and bearish patterns fire together, ``PatternResult.bias`` is ``"conflict"``.

Thresholds (``PatternParams``) are documented defaults, not optimised values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"


@dataclass(frozen=True)
class PatternParams:
    doji_body_pct: float = 0.10          # body ≤ 10 % of the range
    small_body_pct: float = 0.35         # hammer / star bodies
    long_wick_ratio: float = 2.0         # rejection wick ≥ 2 × body
    opposite_wick_pct: float = 0.15      # the other wick ≤ 15 % of the range
    strong_body_pct: float = 0.70        # momentum candle: body ≥ 70 % of range
    strong_range_atr: float = 1.2        # … and range ≥ 1.2 × the average range
    breakout_lookback: int = 20
    level_tolerance_pct: float = 0.002   # 0.2 % counts as "at" a level
    trend_lookback: int = 5              # bars that define the prior move


@dataclass(frozen=True)
class Anatomy:
    range: float
    body: float
    body_pct: float          # body / range (0 for a zero-range candle)
    upper_wick_pct: float
    lower_wick_pct: float
    bullish: bool            # close > open
    bearish: bool            # close < open


def validate_ohlc(c: Any) -> Optional[str]:
    """Why ``c`` is not a valid OHLC bar, or ``None`` when it is."""
    try:
        o, h, lo, cl = float(c.open), float(c.high), float(c.low), float(c.close)
    except (AttributeError, TypeError, ValueError):
        return "missing OHLC fields"
    if not all(math.isfinite(x) for x in (o, h, lo, cl)):
        return "non-finite price"
    if min(o, h, lo, cl) <= 0:
        return "non-positive price"
    if h < max(o, cl) or lo > min(o, cl) or h < lo:
        return "high/low do not contain open/close"
    vol = getattr(c, "volume", None)
    if vol is not None and (not isinstance(vol, (int, float)) or vol < 0):
        return "negative or invalid volume"
    return None


def anatomy(c: Any) -> Anatomy:
    rng = float(c.high) - float(c.low)
    body = abs(float(c.close) - float(c.open))
    if rng <= 0:
        return Anatomy(0.0, body, 0.0, 0.0, 0.0, c.close > c.open, c.close < c.open)
    upper = float(c.high) - max(float(c.open), float(c.close))
    lower = min(float(c.open), float(c.close)) - float(c.low)
    return Anatomy(rng, body, body / rng, upper / rng, lower / rng,
                   c.close > c.open, c.close < c.open)


@dataclass(frozen=True)
class Pattern:
    name: str
    direction: str           # bullish / bearish / neutral
    detail: str = ""


@dataclass
class PatternResult:
    patterns: list[Pattern] = field(default_factory=list)
    anatomy: Optional[Anatomy] = None
    invalid: Optional[str] = None     # why the last candle could not be classified

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.patterns]

    @property
    def bias(self) -> str:
        """bullish / bearish / neutral / conflict across the detected patterns."""
        dirs = {p.direction for p in self.patterns} - {NEUTRAL}
        if len(dirs) > 1:
            return "conflict"
        return dirs.pop() if dirs else NEUTRAL

    def as_dict(self) -> dict:
        a = self.anatomy
        return {
            "patterns": [{"name": p.name, "direction": p.direction, "detail": p.detail}
                         for p in self.patterns],
            "bias": self.bias, "invalid": self.invalid,
            "body_pct": round(a.body_pct, 4) if a else None,
            "upper_wick_pct": round(a.upper_wick_pct, 4) if a else None,
            "lower_wick_pct": round(a.lower_wick_pct, 4) if a else None,
            "range": round(a.range, 4) if a else None,
        }


def _avg_range(candles: Sequence[Any]) -> float:
    rngs = [c.high - c.low for c in candles if c.high > c.low]
    return sum(rngs) / len(rngs) if rngs else 0.0


def _prior_move(candles: Sequence[Any], n: int) -> float:
    """Close change over the ``n`` bars before the last one (sign gives the trend)."""
    if len(candles) < n + 2:
        return 0.0
    return candles[-2].close - candles[-2 - n].close


def detect(candles: Sequence[Any], params: PatternParams = PatternParams(), *,
           support: Optional[float] = None,
           resistance: Optional[float] = None) -> PatternResult:
    """Patterns completed by the last candle. ``support``/``resistance`` (e.g. from
    ``indicators.support_resistance`` on the candles *before* the last one) enable the
    rejection-at-level patterns."""
    p = params
    if not candles:
        return PatternResult(invalid="no candles")
    last = candles[-1]
    problem = validate_ohlc(last)
    if problem:
        return PatternResult(invalid=problem)
    a = anatomy(last)
    out = PatternResult(anatomy=a)
    if a.range <= 0:
        out.patterns.append(Pattern("zero_range", NEUTRAL, "high == low"))
        return out
    prev = candles[-2] if len(candles) >= 2 and validate_ohlc(candles[-2]) is None else None
    trend = _prior_move(candles, p.trend_lookback)
    add = out.patterns.append

    # Single-candle shapes
    if a.body_pct <= p.doji_body_pct:
        add(Pattern("doji", NEUTRAL, f"body {a.body_pct:.0%} of range"))
    small = a.body_pct <= p.small_body_pct
    long_lower = a.lower_wick_pct * a.range >= p.long_wick_ratio * max(a.body, 1e-12)
    long_upper = a.upper_wick_pct * a.range >= p.long_wick_ratio * max(a.body, 1e-12)
    if small and long_lower and a.upper_wick_pct <= p.opposite_wick_pct:
        if trend < 0:
            add(Pattern("hammer", BULLISH, "long lower wick after a decline"))
        add(Pattern("bullish_pin_bar", BULLISH, "lower-wick rejection"))
    if small and long_upper and a.lower_wick_pct <= p.opposite_wick_pct:
        if trend > 0:
            add(Pattern("shooting_star", BEARISH, "long upper wick after a rise"))
        elif trend < 0:
            add(Pattern("inverted_hammer", BULLISH, "long upper wick after a decline"))
        add(Pattern("bearish_pin_bar", BEARISH, "upper-wick rejection"))
    avg = _avg_range(candles[-p.breakout_lookback - 1:-1])
    if a.body_pct >= p.strong_body_pct and avg > 0 and a.range >= p.strong_range_atr * avg:
        add(Pattern("strong_bull_candle" if a.bullish else "strong_bear_candle",
                    BULLISH if a.bullish else BEARISH, f"range {a.range / avg:.1f}× average"))

    # Two-candle patterns
    if prev is not None:
        pa = anatomy(prev)
        p_lo, p_hi = min(prev.open, prev.close), max(prev.open, prev.close)
        lo, hi = min(last.open, last.close), max(last.open, last.close)
        if pa.bearish and a.bullish and lo <= p_lo and hi >= p_hi and a.body > pa.body:
            add(Pattern("bullish_engulfing", BULLISH, "body engulfs the prior red body"))
        if pa.bullish and a.bearish and lo <= p_lo and hi >= p_hi and a.body > pa.body:
            add(Pattern("bearish_engulfing", BEARISH, "body engulfs the prior green body"))
        if last.high <= prev.high and last.low >= prev.low and (
                last.high < prev.high or last.low > prev.low):
            add(Pattern("inside_bar", NEUTRAL, "range inside the prior bar"))
        if last.high > prev.high and last.low < prev.low:
            add(Pattern("outside_bar", BULLISH if a.bullish else BEARISH if a.bearish
                        else NEUTRAL, "range engulfs the prior bar"))

    # Three-candle stars
    if len(candles) >= 3 and all(validate_ohlc(c) is None for c in candles[-3:]):
        first, mid = candles[-3], candles[-2]
        fa, ma = anatomy(first), anatomy(mid)
        f_mid = (first.open + first.close) / 2
        if (fa.bearish and fa.body_pct >= 0.5 and ma.body_pct <= p.small_body_pct
                and a.bullish and last.close > f_mid):
            add(Pattern("morning_star", BULLISH, "red, small body, green past the midpoint"))
        if (fa.bullish and fa.body_pct >= 0.5 and ma.body_pct <= p.small_body_pct
                and a.bearish and last.close < f_mid):
            add(Pattern("evening_star", BEARISH, "green, small body, red past the midpoint"))

    # Structure: breakouts, failed breakouts and rejections at levels
    lookback = [c for c in candles[-p.breakout_lookback - 1:-1] if validate_ohlc(c) is None]
    if len(lookback) >= 5:
        hh, ll = max(c.high for c in lookback), min(c.low for c in lookback)
        if last.close > hh and a.bullish:
            add(Pattern("breakout_up", BULLISH, f"close above the {len(lookback)}-bar high"))
        elif last.close < ll and a.bearish:
            add(Pattern("breakout_down", BEARISH, f"close below the {len(lookback)}-bar low"))
        elif last.high > hh and last.close < hh:
            add(Pattern("failed_breakout_up", BEARISH, "traded above the high, closed back"))
        elif last.low < ll and last.close > ll:
            add(Pattern("failed_breakout_down", BULLISH, "traded below the low, closed back"))
        if prev is not None and len(candles) >= 3:
            older = [c for c in candles[-p.breakout_lookback - 2:-2]
                     if validate_ohlc(c) is None]
            if older:
                prior_high = max(c.high for c in older)
                if (prev.close > prior_high and last.low <= prior_high * (1 + p.level_tolerance_pct)
                        and last.close > prior_high and a.bullish):
                    add(Pattern("breakout_retest", BULLISH, "retested the broken high and held"))
    tol = last.close * p.level_tolerance_pct
    if support is not None and last.low <= support + tol and last.close > support and (
            a.lower_wick_pct >= 0.4):
        add(Pattern("support_rejection", BULLISH, f"rejected support {support:.2f}"))
    if resistance is not None and last.high >= resistance - tol and last.close < resistance and (
            a.upper_wick_pct >= 0.4):
        add(Pattern("resistance_rejection", BEARISH, f"rejected resistance {resistance:.2f}"))
    return out
