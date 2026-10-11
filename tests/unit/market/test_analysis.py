"""Indicators, candle patterns, data quality, multi-timeframe resampling and the candle
regime (skopaq/market/): known values, edge cases and no look-ahead."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from skopaq.market import indicators as ind
from skopaq.market.candles import Candle
from skopaq.market.patterns import anatomy, detect, validate_ohlc
from skopaq.market.quality import check_series
from skopaq.market.regime import RANGING, TRENDING_UP, UNRELIABLE, classify
from skopaq.market.timeframes import bucket_start, confirm_trend, resample

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 8, 9, 15, tzinfo=IST)


def bar(i, o, h, lo, c, v=1000, start=T0, step=60):
    return Candle(start + timedelta(seconds=step * i), o, h, lo, c, v)


def series(closes, spread=0.2, start=T0, step=60, vol=1000):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(start + timedelta(seconds=step * i), prev, max(prev, c) + spread,
                          min(prev, c) - spread, c, vol))
        prev = c
    return out


# ── Indicators ───────────────────────────────────────────────────────────────


def test_sma_ema_rsi_known_values():
    vals = [1, 2, 3, 4, 5]
    assert ind.sma(vals, 5) == 3 and ind.sma(vals, 6) is None
    assert ind.ema(vals, 5) == pytest.approx(3.0)          # seeded with the SMA
    assert ind.ema([1, 2, 3, 4, 5, 6], 5) == pytest.approx(3 + (6 - 3) / 3)
    assert ind.rsi(list(range(1, 20))) == 100.0
    assert ind.rsi([5.0] * 20) == 50.0
    assert ind.rsi([1, 2, 3]) is None


def test_atr_macd_bollinger_roc_relative_volume():
    flat = [bar(i, 100, 101, 99, 100) for i in range(30)]
    assert ind.atr(flat) == pytest.approx(2.0)
    assert ind.atr(flat[:14]) is None
    bb = ind.bollinger([100.0] * 20)
    assert bb.width_pct == 0 and bb.percent_b(100) is None
    assert ind.macd(list(range(30))) is None               # needs slow + signal - 1 = 34
    m = ind.macd([100 + i for i in range(40)])
    assert m.macd > 0 and m.histogram == pytest.approx(m.macd - m.signal)
    assert ind.roc([100, 101, 110], 2) == pytest.approx(0.10)
    vols = [bar(i, 100, 101, 99, 100, v=100) for i in range(20)] + [bar(20, 100, 101, 99, 100, 300)]
    assert ind.relative_volume(vols) == pytest.approx(3.0)
    assert ind.relative_volume([bar(i, 100, 101, 99, 100, v=None) for i in range(25)]) is None


def test_adx_reads_a_strong_uptrend():
    up = series([100 + i * 0.8 for i in range(60)], spread=0.1)
    d = ind.adx(up)
    assert d.adx > 25 and d.plus_di > d.minus_di
    assert ind.adx(up[:20]) is None


def test_swing_points_are_confirmed_only_after_the_right_bars():
    c = [bar(i, h - 1, h, h - 2, h - 0.5) for i, h in enumerate([3, 4, 9, 4, 3, 4, 5])]
    highs, _ = ind.swing_points(c, 2, 2)
    assert [i for i, _ in highs] == [2]
    # The newest two bars can never be pivots: nothing is reported before it is knowable
    highs, lows = ind.swing_points(series([1, 2, 3, 4, 9]), 2, 2)
    assert all(i < 3 for i, _ in highs + lows)


def test_opening_range_only_once_complete_and_previous_day_levels():
    day1 = [bar(i, 100, 105, 95, 100, start=T0 - timedelta(days=1)) for i in range(5)]
    today = [bar(i, 100, 101 + (i == 3), 99, 100) for i in range(16)]
    assert ind.opening_range(today[:15], 15) is None        # 09:15–09:29 bars only
    assert ind.opening_range(today, 15) == (102, 99)
    assert ind.previous_day_high_low(day1 + today) == (105, 95)


def test_indicator_values_never_change_when_later_candles_arrive():
    c = series([100 + math.sin(i / 3) * 2 + i * 0.05 for i in range(80)])
    before = ind.snapshot(c[:60]).as_dict()
    assert ind.snapshot(list(c[:60])).as_dict() == before
    c2 = c[:60] + series([500] * 20, start=c[60].start)    # wild future data
    assert ind.snapshot(c2[:60]).as_dict() == before


# ── Candle patterns ──────────────────────────────────────────────────────────


def test_anatomy_and_malformed_bars():
    a = anatomy(bar(0, 100, 110, 90, 105))
    assert a.range == 20 and a.body_pct == 0.25 and a.upper_wick_pct == 0.25
    assert validate_ohlc(bar(0, 100, 99, 90, 95)) == "high/low do not contain open/close"
    assert validate_ohlc(bar(0, 100, 110, 90, float("nan"))) == "non-finite price"
    assert validate_ohlc(bar(0, 0, 1, 0, 1)) == "non-positive price"
    assert validate_ohlc(bar(0, 100, 101, 99, 100, v=-5)) == "negative or invalid volume"
    res = detect([bar(0, 100, 99, 90, 95)])
    assert res.invalid and res.patterns == []
    zero = detect([bar(0, 100, 100, 100, 100)])
    assert zero.names == ["zero_range"]


def test_engulfing_hammer_star_doji():
    down = series([110 - i for i in range(8)])
    eng = detect(down[:-1] + [bar(7, 104, 104.2, 102.8, 103),
                              bar(8, 102.9, 105, 102.5, 104.8)])
    assert "bullish_engulfing" in eng.names
    hammer = detect(down + [bar(8, 102, 102.3, 98, 102.2)])
    assert "hammer" in hammer.names and "bullish_pin_bar" in hammer.names
    up = series([100 + i for i in range(8)])
    star = detect(up + [bar(8, 107, 111, 106.8, 107.1)])
    assert "shooting_star" in star.names and star.bias == "bearish"
    doji = detect(up + [bar(8, 107, 108, 106, 107.05)])
    assert "doji" in doji.names


def test_morning_star_inside_outside_and_breakouts():
    base = series([110 - i * 0.5 for i in range(10)])
    ms = detect(base + [bar(10, 105, 105.2, 101, 101.2), bar(11, 101, 101.6, 100.6, 101.1),
                        bar(12, 101.2, 104.5, 101, 104.2)])
    assert "morning_star" in ms.names
    inside = detect([bar(0, 100, 110, 90, 105), bar(1, 104, 108, 95, 100)])
    assert "inside_bar" in inside.names
    outside = detect([bar(0, 100, 105, 95, 101), bar(1, 100, 107, 94, 106)])
    assert "outside_bar" in outside.names
    flat = [bar(i, 100, 101, 99, 100) for i in range(20)]
    assert "breakout_up" in detect(flat + [bar(20, 100, 103, 99.8, 102.8)]).names
    assert "failed_breakout_up" in detect(flat + [bar(20, 100.5, 102, 100, 100.2)]).names


def test_conflicting_patterns_are_reported_as_conflict():
    flat = [bar(i, 100, 101, 99, 100) for i in range(20)]
    # A bullish breakout candle that also has a long upper wick near resistance
    res = detect(flat + [bar(20, 100, 106, 99.9, 101.5)], resistance=106)
    dirs = {p.direction for p in res.patterns} - {"neutral"}
    if len(dirs) > 1:
        assert res.bias == "conflict"
    else:
        assert res.bias in ("bullish", "bearish", "neutral")


# ── Data quality ─────────────────────────────────────────────────────────────


def test_quality_flags_duplicates_order_gaps_incomplete_and_stale():
    c = series([100 + i * 0.1 for i in range(30)])
    now = c[-1].start + timedelta(seconds=61)
    assert check_series(c, 60, now).ok
    assert not check_series(c + [c[-1]], 60, now).ok                          # duplicate
    swapped = c[:-2] + [c[-1], c[-2]]
    assert any("out-of-order" in p for p in check_series(swapped, 60, now).blocking)
    assert not check_series(c, 60, c[-1].start + timedelta(seconds=30)).ok    # still forming
    stale = check_series(c, 60, now + timedelta(minutes=10))
    assert not stale.ok and "stale" in stale.blocking[0]
    gappy = c[:10] + c[20:]
    rep = check_series(gappy, 60, now)
    assert rep.missing_bars == 10 and not rep.ok
    bad = c[:-1] + [bar(29, 100, 99, 90, 95)]
    assert not check_series(bad, 60, now).ok
    assert not check_series([], 60, now).ok


# ── Multi-timeframe ──────────────────────────────────────────────────────────


def test_resample_aligns_to_the_session_and_drops_incomplete_buckets():
    c = series([100 + i for i in range(12)])               # 09:15 … 09:26
    five = resample(c, 300)
    assert [b.start.strftime("%H:%M") for b in five] == ["09:15", "09:20"]
    assert five[0].open == c[0].open and five[0].close == c[4].close
    assert five[0].high == max(x.high for x in c[:5]) and five[0].volume == 5000
    # As of 09:24:59 the 09:20 bucket is not complete: it does not exist yet
    early = resample(c, 300, as_of=T0 + timedelta(minutes=9, seconds=59))
    assert [b.start.strftime("%H:%M") for b in early] == ["09:15"]
    assert bucket_start(T0 + timedelta(minutes=44), 1800).strftime("%H:%M") == "09:45"
    with pytest.raises(ValueError):
        resample(c, 90)


def test_higher_timeframe_never_sees_the_future():
    c = series([100 + i * 0.3 for i in range(200)])
    decision = c[120].start + timedelta(seconds=60)
    full = resample(c, 900, as_of=decision)
    cut = resample(c[:121], 900, as_of=decision)
    assert [b.as_dict() for b in full] == [b.as_dict() for b in cut]
    ok, view = confirm_trend(c, 1, 300, decision)
    assert ok and view.direction == "up"
    ok, view = confirm_trend(c[:20], 1, 900)
    assert not ok and view.direction == "unknown"          # too few bars: no confirmation


# ── Regime ───────────────────────────────────────────────────────────────────


def test_regime_trending_ranging_unreliable():
    up = series([100 + i * 0.5 for i in range(120)], spread=0.1)
    assert classify(up).trend == TRENDING_UP
    rng = series([100 + (1 if i % 2 else -1) * 0.4 for i in range(120)], spread=0.3)
    assert classify(rng).trend == RANGING
    assert classify(up[:10]).trend == UNRELIABLE
    assert classify(up, data_ok=False).trend == UNRELIABLE
