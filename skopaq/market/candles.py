"""Live candles and indicators built from ticks.

``CandleBuilder`` turns ticks (price, optional cumulative day volume, time) into OHLC
candles of a fixed interval aligned to the IST clock (09:15:00, 09:16:00, ... for 1
minute). ``Indicators`` keeps EMA, RSI (Wilder), ATR (Wilder) and VWAP up to date from
closed candles, one update per candle, so it costs nothing per tick.

Volume: the LTP feed sends none, so candles have ``volume`` None and VWAP stays None;
with a cumulative day volume (the quote feed) a candle's volume is the increase within it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class Candle:
    start: datetime          # IST, aligned to the interval
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None
    ticks: int = 1

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.ticks += 1

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume,
                "ticks": self.ticks}


def _align(ts: datetime, seconds: int) -> datetime:
    ts = ts.astimezone(_IST)
    day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = int((ts - day).total_seconds()) // seconds * seconds
    return day + timedelta(seconds=offset)


class CandleBuilder:
    """Candles of ``interval_s`` seconds; keeps the last ``keep`` closed ones.

    ``on_close(candle)`` is called when a candle closes (the first tick of a later
    interval closes it). Ticks older than the current candle are ignored.
    """

    def __init__(self, interval_s: int = 60, keep: int = 500,
                 on_close: Optional[Callable[[Candle], None]] = None) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.interval_s = int(interval_s)
        self.closed: deque[Candle] = deque(maxlen=keep)
        self.current: Optional[Candle] = None
        self._on_close = on_close
        self._last_cum_volume: Optional[int] = None
        self._open_cum_volume: Optional[int] = None

    def add(self, price: float, ts: datetime, cum_volume: Optional[int] = None
            ) -> Optional[Candle]:
        """Add a tick; returns the candle it closed, if any."""
        if price <= 0:
            return None
        start = _align(ts, self.interval_s)
        closed = None
        if self.current is not None and start < self.current.start:
            return None
        if self.current is None or start > self.current.start:
            if self.current is not None:
                closed = self._close()
            self.current = Candle(start, price, price, price, price)
            self._open_cum_volume = (self._last_cum_volume if self._last_cum_volume
                                     is not None else cum_volume)
        else:
            self.current.update(price)
        if cum_volume is not None:
            self._last_cum_volume = cum_volume
            if self._open_cum_volume is not None:
                self.current.volume = max(0, cum_volume - self._open_cum_volume)
        return closed

    def _close(self) -> Candle:
        candle = self.current
        self.closed.append(candle)
        if self._on_close is not None:
            self._on_close(candle)
        return candle


@dataclass
class Indicators:
    """EMA(fast, slow), RSI, ATR (Wilder) and session VWAP from closed candles."""

    ema_fast_period: int = 9
    ema_slow_period: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    rsi: Optional[float] = None
    atr: Optional[float] = None
    vwap: Optional[float] = None
    candles: int = 0
    _prev_close: Optional[float] = None
    _avg_gain: Optional[float] = None
    _avg_loss: Optional[float] = None
    _seed: list = field(default_factory=list)
    _tr_seed: list = field(default_factory=list)
    _pv: float = 0.0
    _vol: float = 0.0
    _vwap_day: Optional[object] = None

    @staticmethod
    def _ema(prev: Optional[float], price: float, period: int) -> float:
        if prev is None:
            return price
        k = 2.0 / (period + 1)
        return prev + k * (price - prev)

    def update(self, c: Candle) -> None:
        self.candles += 1
        self.ema_fast = self._ema(self.ema_fast, c.close, self.ema_fast_period)
        self.ema_slow = self._ema(self.ema_slow, c.close, self.ema_slow_period)

        if self._prev_close is not None:
            change = c.close - self._prev_close
            gain, loss = max(change, 0.0), max(-change, 0.0)
            tr = max(c.high - c.low, abs(c.high - self._prev_close),
                     abs(c.low - self._prev_close))
            if self._avg_gain is None:
                self._seed.append((gain, loss))
                if len(self._seed) == self.rsi_period:
                    self._avg_gain = sum(g for g, _ in self._seed) / self.rsi_period
                    self._avg_loss = sum(lo for _, lo in self._seed) / self.rsi_period
            else:
                n = self.rsi_period
                self._avg_gain = (self._avg_gain * (n - 1) + gain) / n
                self._avg_loss = (self._avg_loss * (n - 1) + loss) / n
            if self._avg_gain is not None:
                if self._avg_loss == 0:
                    self.rsi = 100.0 if self._avg_gain > 0 else 50.0
                else:
                    rs = self._avg_gain / self._avg_loss
                    self.rsi = 100.0 - 100.0 / (1.0 + rs)
        else:
            tr = c.high - c.low
        if self.atr is None:
            self._tr_seed.append(tr)
            if len(self._tr_seed) == self.atr_period:
                self.atr = sum(self._tr_seed) / self.atr_period
        else:
            self.atr = (self.atr * (self.atr_period - 1) + tr) / self.atr_period
        self._prev_close = c.close

        day = c.start.date()
        if self._vwap_day != day:
            self._vwap_day, self._pv, self._vol = day, 0.0, 0.0
        if c.volume:
            typical = (c.high + c.low + c.close) / 3.0
            self._pv += typical * c.volume
            self._vol += c.volume
            self.vwap = self._pv / self._vol
        elif self._vol == 0:
            self.vwap = None

    def as_dict(self) -> dict:
        def r(v):
            return round(v, 4) if isinstance(v, float) else v
        return {"candles": self.candles, "ema_fast": r(self.ema_fast),
                "ema_slow": r(self.ema_slow), "rsi": r(self.rsi), "atr": r(self.atr),
                "vwap": r(self.vwap)}


class LiveSeries:
    """Candles plus indicators for one instrument."""

    def __init__(self, interval_s: int = 60, keep: int = 500) -> None:
        self.indicators = Indicators()
        self.builder = CandleBuilder(interval_s, keep, on_close=self.indicators.update)

    def add(self, price: float, ts: datetime, cum_volume: Optional[int] = None
            ) -> Optional[Candle]:
        return self.builder.add(price, ts, cum_volume)

    def seed(self, candles: list[Candle]) -> None:
        """Closed candles from history (oldest first), before any tick: the indicators are
        ready at once. Only candles older than the current one are taken."""
        for c in candles:
            if self.builder.current is not None and c.start >= self.builder.current.start:
                break
            if self.builder.closed and c.start <= self.builder.closed[-1].start:
                continue
            self.builder.closed.append(c)
            self.indicators.update(c)

    @property
    def candles(self) -> list[Candle]:
        """The closed candles, oldest first."""
        return list(self.builder.closed)
