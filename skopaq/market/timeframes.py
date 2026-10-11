"""Multi-timeframe candles without look-ahead.

``resample`` builds higher-timeframe bars from closed lower-timeframe bars, aligned to the
NSE session open (09:15 IST, like ``skopaq.market.candles``), and keeps only buckets that
were *complete* at ``as_of``: a 15-minute bar 09:15-09:30 exists for a decision at 09:30
or later, never at 09:29. ``confirm_trend`` is a documented higher-timeframe filter.

Timeframes are in seconds; ``TIMEFRAMES`` lists the supported ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from skopaq.market.candles import Candle
from skopaq.market.indicators import adx, ema

IST = timezone(timedelta(hours=5, minutes=30))
TIMEFRAMES = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600}
_SESSION_OPEN_MIN = 9 * 60 + 15


def bucket_start(ts: datetime, seconds: int) -> datetime:
    """Start of the ``seconds`` bucket holding ``ts``, counted from 09:15 IST (bars before
    the open are counted from midnight)."""
    ist = ts.astimezone(IST)
    midnight = ist.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = (ist - midnight).total_seconds()
    anchor = _SESSION_OPEN_MIN * 60 if offset >= _SESSION_OPEN_MIN * 60 else 0
    n = int((offset - anchor) // seconds)
    return midnight + timedelta(seconds=anchor + n * seconds)


def resample(candles: Sequence[Any], seconds: int, as_of: Optional[datetime] = None,
             base_seconds: int = 60) -> list[Candle]:
    """Higher-timeframe bars from closed ``base_seconds`` bars, only buckets complete at
    ``as_of`` (default: the end of the last base bar). Volume is summed (None if any part
    lacks it)."""
    if seconds < base_seconds or seconds % base_seconds:
        raise ValueError(f"{seconds}s is not a multiple of the {base_seconds}s base bars")
    if not candles:
        return []
    if as_of is None:
        as_of = candles[-1].start + timedelta(seconds=base_seconds)
    out: list[Candle] = []
    for c in candles:
        if c.start + timedelta(seconds=base_seconds) > as_of:
            break                        # a base bar not closed at as_of
        start = bucket_start(c.start, seconds)
        if out and out[-1].start == start:
            b = out[-1]
            b.high, b.low, b.close = max(b.high, c.high), min(b.low, c.low), c.close
            b.volume = None if b.volume is None or c.volume is None else b.volume + c.volume
            b.ticks += getattr(c, "ticks", 1)
        else:
            out.append(Candle(start=start, open=c.open, high=c.high, low=c.low,
                              close=c.close, volume=c.volume, ticks=getattr(c, "ticks", 1)))
    # Drop the last bucket if it was not complete at as_of
    if out and out[-1].start + timedelta(seconds=seconds) > as_of:
        out.pop()
    return out


@dataclass(frozen=True)
class TrendView:
    timeframe_s: int
    direction: str          # up / down / flat / unknown
    bars: int
    ema_fast: Optional[float]
    ema_slow: Optional[float]
    adx: Optional[float]

    def as_dict(self) -> dict:
        return {"timeframe_s": self.timeframe_s, "direction": self.direction,
                "bars": self.bars, "ema_fast": self.ema_fast, "ema_slow": self.ema_slow,
                "adx": self.adx}


def trend_of(bars: Sequence[Any], timeframe_s: int, fast: int = 5, slow: int = 13,
             min_adx: float = 0.0) -> TrendView:
    """EMA-fast vs EMA-slow direction of completed higher-timeframe bars (5/13 by
    default: a 15-minute EMA21 would need more than a session of bars)."""
    closes = [b.close for b in bars]
    f, s = ema(closes, fast), ema(closes, slow)
    d = adx(bars)
    if f is None or s is None:
        return TrendView(timeframe_s, "unknown", len(bars), f, s, d.adx if d else None)
    direction = "up" if f > s and closes[-1] > s else "down" if f < s and closes[-1] < s \
        else "flat"
    if min_adx and (d is None or d.adx < min_adx):
        direction = "flat"
    return TrendView(timeframe_s, direction, len(bars), f, s, d.adx if d else None)


def confirm_trend(candles: Sequence[Any], direction: int, timeframe_s: int,
                  as_of: Optional[datetime] = None,
                  base_seconds: int = 60) -> tuple[bool, TrendView]:
    """Whether the higher timeframe agrees with a long (+1) or short (-1) entry. Unknown
    (too few completed bars) does not confirm: the caller decides NO_TRADE."""
    view = trend_of(resample(candles, timeframe_s, as_of, base_seconds), timeframe_s)
    want = "up" if direction > 0 else "down"
    return view.direction == want, view
