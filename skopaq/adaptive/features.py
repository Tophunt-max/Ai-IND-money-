"""FreqAI-style automatic feature expansion, point in time.

``FeatureExpander.expand`` turns the closed candles known at a decision (and those of
correlated instruments, e.g. NIFTY or sector leaders) into a wide feature vector:

    for each timeframe   (base bars and higher ones resampled from them, complete only)
      for each period    RSI, ROC, ATR %, Bollinger width, distance from the SMA,
                         relative volume
      once per timeframe ADX, EMA 9/21 gap
    shifted copies       RSI / ROC as they were 1..n bars earlier
    correlated symbols   the same base-timeframe RSI / ROC / ATR % of each one
    time                 minute of the session, day of the week

Names look like ``rsi_14@300s``, ``roc_10@60s_s2``, ``NIFTY:roc_10@60s``. Every value is
computed from bars that had closed at ``as_of``; a missing input is NaN, never a guess.
The configuration is stored with each trained model, so live inference builds exactly
the training features.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from skopaq.market import indicators as ind
from skopaq.market.timeframes import resample

NAN = math.nan


@dataclass(frozen=True)
class FeatureConfig:
    base_seconds: int = 60
    timeframes: tuple[int, ...] = (60, 300, 900)
    periods: tuple[int, ...] = (10, 20)
    shifts: tuple[int, ...] = (1, 2, 3)
    correlated: tuple[str, ...] = ()
    window: int = 60                        # bars per timeframe used for the indicators

    def as_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FeatureConfig":
        return cls(base_seconds=int(d.get("base_seconds", 60)),
                   timeframes=tuple(int(x) for x in d.get("timeframes", (60, 300, 900))),
                   periods=tuple(int(x) for x in d.get("periods", (10, 20))),
                   shifts=tuple(int(x) for x in d.get("shifts", (1, 2, 3))),
                   correlated=tuple(str(x) for x in d.get("correlated", ())),
                   window=int(d.get("window", 60)))


def _f(v: Optional[float]) -> float:
    if v is None:
        return NAN
    try:
        x = float(v)
    except (TypeError, ValueError):
        return NAN
    return x if math.isfinite(x) else NAN


def _block(bars: Sequence[Any], periods: Sequence[int], tag: str, out: dict[str, float],
           *, full: bool = True) -> None:
    closes = [b.close for b in bars]
    last = closes[-1] if closes else NAN
    for p in periods:
        out[f"rsi_{p}@{tag}"] = _f(ind.rsi(closes, p))
        out[f"roc_{p}@{tag}"] = _f(ind.roc(closes, p))
        a = ind.atr(bars, p)
        out[f"atrpct_{p}@{tag}"] = _f(a / last) if a is not None and last else NAN
        if not full:
            continue
        bb = ind.bollinger(closes, p)
        out[f"bbw_{p}@{tag}"] = _f(bb.width_pct) if bb else NAN
        s = ind.sma(closes, p)
        out[f"smadist_{p}@{tag}"] = _f(last / s - 1) if s else NAN
        out[f"relvol_{p}@{tag}"] = _f(ind.relative_volume(bars, p))
    if full:
        d = ind.adx(bars)
        out[f"adx@{tag}"] = _f(d.adx) if d else NAN
        out[f"di@{tag}"] = _f(d.plus_di - d.minus_di) if d else NAN
        f9, s21 = ind.ema(closes, 9), ind.ema(closes, 21)
        out[f"emagap@{tag}"] = _f((f9 - s21) / last) if f9 is not None and s21 is not None \
            and last else NAN


@dataclass
class FeatureExpander:
    config: FeatureConfig = field(default_factory=FeatureConfig)

    def _bars(self, candles: Sequence[Any], tf: int, as_of: Optional[datetime]) -> list:
        c = self.config
        if tf == c.base_seconds:
            return list(candles[-c.window:])
        if tf > c.base_seconds and tf % c.base_seconds == 0:
            # Only the base bars the last ``window`` higher bars can contain (+1 bucket)
            need = (c.window + 1) * (tf // c.base_seconds)
            return resample(candles[-need:], tf, as_of, c.base_seconds)[-c.window:]
        return []

    def expand(self, candles: Sequence[Any], as_of: Optional[datetime] = None,
               correlated: Optional[Mapping[str, Sequence[Any]]] = None) -> dict[str, float]:
        c = self.config
        out: dict[str, float] = {}
        if as_of is not None:
            # Whatever the caller passed, only bars that had closed at ``as_of`` are used
            cutoff = as_of.timestamp()
            candles = [b for b in candles
                       if b.start.timestamp() + c.base_seconds <= cutoff]
        for tf in c.timeframes:
            bars = self._bars(candles, tf, as_of)
            tag = f"{tf}s"
            # The same names whatever the data (missing → NaN): models align by name
            _block(bars, c.periods, tag, out)
            for s in c.shifts:
                earlier = bars[:-s] if len(bars) > s else []
                tmp: dict[str, float] = {}
                _block(earlier, c.periods, tag, tmp, full=False)
                for k, v in tmp.items():
                    if k.startswith(("rsi_", "roc_")):
                        out[f"{k}_s{s}"] = v
        for sym in c.correlated:
            bars = list((correlated or {}).get(sym) or [])[-c.window:]
            if as_of is not None:
                bars = [b for b in bars
                        if b.start.timestamp() + c.base_seconds <= as_of.timestamp()]
            tmp = {}
            _block(bars, c.periods, f"{c.base_seconds}s", tmp, full=False)
            for k, v in tmp.items():
                out[f"{sym}:{k}"] = v
        when = as_of or (candles[-1].start if candles else None)
        if when is not None:
            out["minute_of_session"] = float(when.hour * 60 + when.minute - (9 * 60 + 15))
            out["day_of_week"] = float(when.weekday())
        else:
            out["minute_of_session"] = out["day_of_week"] = NAN
        return out

    def names(self) -> list[str]:
        """The feature names this configuration produces, in a stable order."""
        from datetime import timedelta, timezone

        from skopaq.market.candles import Candle

        t0 = datetime(2026, 1, 5, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        dummy = [Candle(t0, 1, 1, 1, 1, 1)]
        corr = {s: dummy for s in self.config.correlated}
        return sorted(self.expand(dummy, None, corr))
