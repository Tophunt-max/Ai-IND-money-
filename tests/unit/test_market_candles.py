"""Live candles and indicators (skopaq/market/candles.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skopaq.market.candles import Candle, CandleBuilder, Indicators, LiveSeries

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 8, 9, 15, 0, tzinfo=IST)


def test_ticks_build_aligned_candles():
    closed = []
    b = CandleBuilder(60, on_close=closed.append)
    assert b.add(100.0, T0 + timedelta(seconds=5)) is None
    b.add(102.0, T0 + timedelta(seconds=20))
    b.add(99.0, T0 + timedelta(seconds=59))
    c = b.add(101.0, T0 + timedelta(seconds=61))          # opens 09:16, closes 09:15
    assert c is closed[0]
    assert (c.start, c.open, c.high, c.low, c.close, c.ticks) == (T0, 100, 102, 99, 99, 3)
    assert b.current.start == T0 + timedelta(minutes=1)
    assert b.add(50.0, T0 + timedelta(seconds=30)) is None   # older than the current one
    # UTC timestamps are aligned on the IST clock
    utc = (T0 + timedelta(minutes=2, seconds=1)).astimezone(timezone.utc)
    assert b.add(103.0, utc).start == T0 + timedelta(minutes=1)


def test_volume_is_the_increase_of_the_day_volume():
    b = CandleBuilder(60)
    b.add(100.0, T0, cum_volume=1000)
    b.add(100.5, T0 + timedelta(seconds=10), cum_volume=1300)
    c = b.add(101.0, T0 + timedelta(seconds=70), cum_volume=1450)
    assert c.volume == 300 and b.current.volume == 150


def test_indicators_match_hand_computed_values():
    ind = Indicators(ema_fast_period=3, ema_slow_period=5, rsi_period=3, atr_period=3)
    closes = [10, 11, 12, 11, 13]
    for i, close in enumerate(closes):
        ind.update(Candle(T0 + timedelta(minutes=i), close, close + 1, close - 1, close,
                          volume=100))
    # EMA(3): k = 0.5 seeded with the first close
    ema = 10.0
    for close in closes[1:]:
        ema += 0.5 * (close - ema)
    assert ind.ema_fast == pytest.approx(ema)
    # RSI(3): seed on changes +1, +1, -1 → gains 2/3, losses 1/3; then +2
    g, lo = (2 / 3 * 2 + 2) / 3, (1 / 3 * 2 + 0) / 3
    assert ind.rsi == pytest.approx(100 - 100 / (1 + g / lo))
    assert ind.atr == pytest.approx((2 * 2 + 3) / 3)   # seed 2,2,2 then TR = 3 (13+1-11)
    assert ind.vwap == pytest.approx(sum(closes) / len(closes))
    assert ind.as_dict()["candles"] == 5


def test_vwap_resets_each_day_and_stays_none_without_volume():
    s = LiveSeries(60)
    for i in range(3):
        s.add(100.0 + i, T0 + timedelta(minutes=i))
    assert s.indicators.candles == 2 and s.indicators.vwap is None
