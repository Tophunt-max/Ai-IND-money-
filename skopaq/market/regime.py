"""Candle-based market regime of one instrument (complements ``skopaq.risk.regime``,
which classifies the index from India VIX and the 200-day SMA).

``classify(candles)`` → ``RegimeView`` with a trend label and a volatility label:

* trend: ``trending_up`` / ``trending_down`` when ADX ≥ ``trend_adx`` and the DI and EMA
  stack agree; ``ranging`` when ADX < ``range_adx``; otherwise ``transition``.
* volatility: ATR % of price over its own recent median: ``high_volatility`` above
  ``high_vol_ratio``, ``low_volatility`` below ``low_vol_ratio``, else ``normal``.
* ``unreliable`` when there are too few bars or the data-quality check blocks.

Thresholds are documented defaults; which strategy suits which regime is a research
question answered by out-of-sample backtests, not by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Optional, Sequence

from skopaq.market.indicators import adx, atr, ema
from skopaq.market.patterns import validate_ohlc

TRENDING_UP, TRENDING_DOWN, RANGING = "trending_up", "trending_down", "ranging"
TRANSITION, UNRELIABLE = "transition", "unreliable"
HIGH_VOL, LOW_VOL, NORMAL_VOL = "high_volatility", "low_volatility", "normal"


@dataclass(frozen=True)
class RegimeParams:
    trend_adx: float = 25.0
    range_adx: float = 20.0
    min_di_spread: float = 5.0            # +DI and -DI must differ by this to call a trend
    adx_period: int = 14
    atr_period: int = 14
    vol_lookback: int = 60
    high_vol_ratio: float = 1.5
    low_vol_ratio: float = 0.6
    min_bars: int = 40


@dataclass(frozen=True)
class RegimeView:
    trend: str
    volatility: str
    adx: Optional[float] = None
    atr_pct: Optional[float] = None
    vol_ratio: Optional[float] = None
    reason: str = ""

    @property
    def label(self) -> str:
        return UNRELIABLE if self.trend == UNRELIABLE else f"{self.trend}/{self.volatility}"

    @property
    def reliable(self) -> bool:
        return self.trend != UNRELIABLE

    def as_dict(self) -> dict:
        return {"trend": self.trend, "volatility": self.volatility, "label": self.label,
                "adx": None if self.adx is None else round(self.adx, 2),
                "atr_pct": None if self.atr_pct is None else round(self.atr_pct, 6),
                "vol_ratio": None if self.vol_ratio is None else round(self.vol_ratio, 3),
                "reason": self.reason}


def _atr_pct_series(candles: Sequence[Any], period: int, lookback: int) -> list[float]:
    out = []
    start = max(period + 1, len(candles) - lookback)
    for end in range(start, len(candles) + 1):
        window = candles[:end]
        a = atr(window[-(period * 3 + 1):], period)
        if a is not None and window[-1].close > 0:
            out.append(a / window[-1].close)
    return out


def classify(candles: Sequence[Any], params: RegimeParams = RegimeParams(),
             data_ok: bool = True) -> RegimeView:
    p = params
    if not data_ok:
        return RegimeView(UNRELIABLE, NORMAL_VOL, reason="data-quality check failed")
    if len(candles) < p.min_bars:
        return RegimeView(UNRELIABLE, NORMAL_VOL,
                          reason=f"{len(candles)} bars < {p.min_bars} needed")
    if any(validate_ohlc(c) for c in candles[-p.min_bars:]):
        return RegimeView(UNRELIABLE, NORMAL_VOL, reason="invalid bars")
    d = adx(candles, p.adx_period)
    closes = [c.close for c in candles]
    fast, slow = ema(closes, 9), ema(closes, 21)
    series = _atr_pct_series(candles, p.atr_period, p.vol_lookback)
    atr_pct = series[-1] if series else None
    base = median(series) if len(series) >= 5 else None
    ratio = atr_pct / base if atr_pct is not None and base else None
    vol = NORMAL_VOL
    if ratio is not None:
        vol = HIGH_VOL if ratio >= p.high_vol_ratio else LOW_VOL if ratio <= p.low_vol_ratio \
            else NORMAL_VOL
    if d is None or fast is None or slow is None:
        return RegimeView(UNRELIABLE, vol, atr_pct=atr_pct, vol_ratio=ratio,
                          reason="indicators not ready")
    spread = d.plus_di - d.minus_di
    if d.adx >= p.trend_adx and spread >= p.min_di_spread and fast > slow:
        trend = TRENDING_UP
    elif d.adx >= p.trend_adx and -spread >= p.min_di_spread and fast < slow:
        trend = TRENDING_DOWN
    elif d.adx < p.range_adx:
        trend = RANGING
    else:
        trend = TRANSITION
    return RegimeView(trend, vol, adx=d.adx, atr_pct=atr_pct, vol_ratio=ratio,
                      reason=f"ADX {d.adx:.1f}, +DI {d.plus_di:.1f}, -DI {d.minus_di:.1f}")
