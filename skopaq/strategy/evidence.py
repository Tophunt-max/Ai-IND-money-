"""Point-in-time evidence of one decision: data quality, indicators, candle patterns,
regime and the higher-timeframe trend, all from the closed candles given (never later).

``build_evidence`` is what a strategy attaches to its ``SignalCandidate``; the journal
stores it and the learning dataset reads it back, so training features are exactly what
the live decision saw.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from skopaq.market import indicators as ind
from skopaq.market.patterns import detect
from skopaq.market.quality import check_series
from skopaq.market.regime import classify
from skopaq.market.timeframes import confirm_trend


def build_evidence(candles: Sequence[Any], interval_s: int, now: Optional[datetime] = None,
                   *, direction: int = 1, htf_s: Optional[int] = None,
                   source: str = "live", stale_after_s: Optional[float] = None) -> dict:
    candles = list(candles)
    quality = check_series(candles, interval_s, now, stale_after_s=stale_after_s)
    snap = ind.snapshot(candles)
    prior = candles[:-1]
    levels = ind.support_resistance(prior) if len(prior) >= 5 else ind.Levels(None, None)
    pats = detect(candles, support=levels.support, resistance=levels.resistance)
    regime = classify(candles, data_ok=quality.ok)
    out: dict[str, Any] = {
        "source": source,
        "bars": len(candles),
        "last_bar": candles[-1].start.isoformat() if candles else None,
        "data_quality": quality.as_dict(),
        "indicators": snap.as_dict() if snap else {},
        "patterns": pats.as_dict(),
        "regime": regime.as_dict(),
    }
    if htf_s and candles and htf_s > interval_s and htf_s % interval_s == 0:
        ok, view = confirm_trend(candles, direction, htf_s, now, base_seconds=interval_s)
        out["htf"] = {**view.as_dict(), "confirmed": ok}
    return out
