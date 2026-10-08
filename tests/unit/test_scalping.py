"""Scalping (skopaq/scalping/): strategies, charges, exit rules, the backtest, and the
engine end to end on the paper engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from skopaq.broker.models import ExecutionResult, Product, Quote
from skopaq.broker.paper_engine import PaperEngine
from skopaq.constants import SafetyRules
from skopaq.execution.executor import Executor
from skopaq.execution.order_router import OrderRouter
from skopaq.execution.safety_checker import SafetyChecker
from skopaq.market.candles import Candle
from skopaq.scalping.backtest import simulate
from skopaq.scalping.costs import round_trip_cost
from skopaq.scalping.engine import ScalpEngine
from skopaq.scalping.rules import ScalpPosition, exit_reason, update_trail
from skopaq.scalping.settings import ScalpSettings
from skopaq.scalping.strategies import (
    StrategyParams,
    ema_rsi,
    evaluate,
    opening_range,
    orb,
    range_reversal,
    vwap_pullback,
    vwap_series,
)

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 8, 9, 15, tzinfo=IST)
P = StrategyParams()


def mk(closes, spread=0.1, vol=1000, start=T0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(start + timedelta(minutes=i), prev, max(prev, c) + spread,
                          min(prev, c) - spread, c, vol))
        prev = c
    return out


ORB_FIRST = [100, 100.4, 100.8, 100.2, 99.9, 100.3, 100.6, 100.1, 100.0, 100.5, 100.7,
             100.2, 100.4, 100.6, 100.3]


# ── Strategies ───────────────────────────────────────────────────────────────


def test_ema_rsi_fires_on_the_cross_only():
    closes = [100 - 0.1 * i for i in range(25)] + [97.6 + 0.15 * i for i in range(1, 12)]
    candles = mk(closes, spread=0.3)
    hits = [k for k in range(24, len(candles) + 1) if ema_rsi(candles[:k], P)]
    assert hits and hits[0] == 34
    setup = ema_rsi(candles[:34], P)
    assert setup.stop < setup.entry < setup.target
    assert setup.target == pytest.approx(setup.entry + 1.5 * setup.risk, abs=0.01)
    assert ema_rsi(candles[:35], P) is None                 # the cross was one candle back


def test_vwap_pullback_needs_the_dip_and_the_close_back_above():
    trend = mk([100 + 0.2 * i for i in range(30)], spread=0.3)
    vw = vwap_series(trend)[-1]
    dip = Candle(T0 + timedelta(minutes=30), vw + 0.5, vw + 1.2, vw - 0.05, vw + 1.0, 1000)
    setup = vwap_pullback(trend + [dip], P)
    assert setup and "VWAP" in setup.reason and setup.stop < vw
    no_touch = Candle(dip.start, vw + 2, vw + 3, vw + 1.5, vw + 2.8, 1000)
    assert vwap_pullback(trend + [no_touch], P) is None
    red = Candle(dip.start, vw + 1.0, vw + 1.2, vw - 0.05, vw + 0.5, 1000)
    assert vwap_pullback(trend + [red], P) is None
    # No volume (the LTP feed): EMA 21 stands in for VWAP
    novol = [Candle(c.start, c.open, c.high, c.low, c.close, None) for c in trend + [dip]]
    fallback = vwap_pullback(novol, P)
    assert fallback is None or "EMA21" in fallback.reason


def test_orb_takes_the_first_breakout_of_the_day_only():
    candles = mk(ORB_FIRST + [100.5, 100.6, 100.7, 101.5], spread=0.2)
    assert opening_range(candles, 15) == (101.0, 99.7)
    setup = orb(candles, P)
    assert setup.strategy == "orb" and setup.target == pytest.approx(101.5 + 1.3)
    later = mk(ORB_FIRST + [100.5, 101.5, 100.7, 101.6], spread=0.2)
    assert orb(later, P) is None                           # a second close above
    assert orb(mk(ORB_FIRST[:10], spread=0.2), P) is None  # the range is not complete


def test_range_reversal_bounces_off_support_with_a_low_rsi():
    closes = [100, 100.5, 101, 101.5, 102, 101.5, 101, 100.5, 100, 99.5, 99, 98.5, 98,
              98.5, 99, 99.5, 100, 100.5, 101, 101.5, 102, 101.6, 101.2, 100.8, 100.4, 100,
              99.6, 99.2, 98.8, 98.4, 98.1]
    candles = mk(closes)
    support = min(c.low for c in candles[-31:])
    bounce = Candle(T0 + timedelta(minutes=len(candles)), 98.0, 98.5, support - 0.02, 98.45,
                    1000)
    setup = range_reversal(candles + [bounce], P)
    assert setup and setup.target == 100.0 and setup.stop < support
    red = Candle(bounce.start, 98.5, 98.6, support - 0.02, 98.2, 1000)
    assert range_reversal(candles + [red], P) is None


def test_evaluate_follows_the_priority_order():
    candles = mk(ORB_FIRST + [100.5, 100.6, 100.7, 101.5], spread=0.2)
    assert evaluate(candles, ["range_reversal", "orb"], P).strategy == "orb"
    assert evaluate(candles, ["range_reversal"], P) is None


# ── Charges and exit rules ───────────────────────────────────────────────────


def test_round_trip_cost():
    cost = round_trip_cost(1000.0, 1010.0, 50)            # ₹50,000 in, ₹50,500 out
    # 2 × ₹10 brokerage + GST, STT 0.025 % on the sell, exchange, stamp
    assert 35 < cost < 45
    assert round_trip_cost(100, 100, 0) == 0


def test_exit_rules_stop_target_breakeven_trail_and_time():
    pos = ScalpPosition("TCS", "orb", 10, 100.0, 99.0, 102.0, T0, atr=0.5,
                        cost_per_share=0.05)
    hold = timedelta(minutes=30)
    assert exit_reason(pos, 99.0, T0, hold).startswith("SCALP STOP")
    assert exit_reason(pos, 102.0, T0, hold).startswith("SCALP TARGET")
    update_trail(pos, 101.0)                               # 1 R: breakeven
    assert pos.breakeven and pos.stop == 100.5             # max(100.05, 101 − 0.5)
    update_trail(pos, 101.6)
    assert pos.stop == 101.1
    assert exit_reason(pos, 101.0, T0, hold).startswith("SCALP TRAIL")
    flat = ScalpPosition("TCS", "orb", 10, 100.0, 99.0, 102.0, T0, cost_per_share=0.05)
    assert exit_reason(flat, 100.0, T0 + hold, hold).startswith("SCALP TIME STOP")
    assert exit_reason(flat, 100.5, T0 + hold, hold) is None   # in profit: keep it


def test_settings_sanitise_bad_values():
    s = ScalpSettings.from_config(SimpleNamespace(
        scalp_symbols=" tcs , infy,tcs", scalp_strategies="orb,bogus",
        scalp_entry_start="15:00", scalp_entry_end="10:00", scalp_risk_per_trade_pct="x",
        scalp_max_open=0))
    assert s.symbols == ("TCS", "INFY") and s.strategies == ("orb",)
    assert (s.entry_start, s.entry_end) == (time(9, 30), time(14, 45))
    assert s.risk_per_trade_pct == 0.0025 and s.max_open == 1


# ── Backtest ─────────────────────────────────────────────────────────────────


def _orb_day(after):
    return mk(ORB_FIRST + after, spread=0.2)


def test_backtest_fills_at_the_next_open_and_pays_charges():
    s = ScalpSettings.from_config(SimpleNamespace(scalp_strategies="orb",
                                                  scalp_entry_start="09:15"))
    day = _orb_day([100.5, 100.6, 100.7, 101.5, 101.8, 102.3, 103.0, 103.5])
    result = simulate("TCS", [day], s)
    [t] = result.trades
    assert t.strategy == "orb" and t.entry == 101.5           # the next candle's open
    assert "TARGET" in t.reason and t.exit == pytest.approx(102.8)
    assert t.pnl < (t.exit - t.entry) * t.qty                 # charges paid
    stats = result.stats()
    assert stats["all"]["trades"] == 1 and stats["orb"]["win_rate_pct"] == 100.0


def test_backtest_counts_a_candle_touching_both_as_a_loss_and_caps_qty():
    s = ScalpSettings.from_config(SimpleNamespace(scalp_strategies="orb",
                                                  scalp_entry_start="09:15"))
    day = _orb_day([100.5, 100.6, 100.7, 101.5, 101.5])
    whipsaw = Candle(day[-1].start + timedelta(minutes=1), 101.5, 103.5, 99.0, 101.0, 1000)
    result = simulate("TCS", [day + [whipsaw]], s, max_qty=200)
    [t] = result.trades
    assert "STOP" in t.reason and t.qty == 200 and t.pnl < 0
    # 5 shares (the old share cap) cannot pay the charges: skipped
    assert simulate("TCS", [day + [whipsaw]], s, max_qty=5).skipped_cost == 1


# ── The engine on the paper engine ───────────────────────────────────────────


class Wall:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class Client:
    """History before now; REST quotes from a price path of the wall clock."""

    def __init__(self, wall, history, path):
        self.wall, self.history, self.path = wall, history, path

    async def get_historical(self, scrip, interval, start_time, end_time):
        return [SimpleNamespace(timestamp=c.start, open=c.open, high=c.high, low=c.low,
                                close=c.close, volume=c.volume) for c in self.history]

    async def get_quotes(self, codes, symbols=None):
        return [Quote(symbol=symbols[0], ltp=self.path(self.wall()))]


RULES = SafetyRules(market_hours_only=False, require_stop_loss=False,
                    max_lots_per_position=10_000, max_order_value_inr=10_000_000,
                    max_position_pct=1.0)


async def test_engine_takes_an_orb_scalp_to_its_target_on_paper(monkeypatch):
    async def scrip(client, symbol, exchange="NSE"):
        return "NSE_11536"

    monkeypatch.setattr("skopaq.broker.scrip_resolver.resolve_scrip_code", scrip)
    monkeypatch.setattr("skopaq.notifications.notify", AsyncMock())
    history = mk(ORB_FIRST + [100.5, 100.6, 100.7], spread=0.2)     # 09:15–09:32
    start = T0 + timedelta(minutes=18, seconds=5)                    # 09:33:05

    def path(now):
        minutes = (now - T0).total_seconds() / 60
        if minutes < 19:
            return 101.5                       # the 09:33 candle closes above the range
        return min(103.5, 101.5 + (minutes - 19) * 0.4)

    wall = Wall(start)
    config = SimpleNamespace(
        trading_mode="paper", scalp_symbols="TCS", scalp_strategies="orb",
        scalp_flatten_at="09:45", scalp_entry_start="09:30", scalp_entry_end="09:44",
        scalp_rest_poll_seconds=1, max_sector_concentration_pct=1.0)
    paper = PaperEngine(initial_capital=1_000_000)
    router = OrderRouter(config, paper)
    executor = Executor(router, SafetyChecker(rules=RULES))
    trades = []

    async def on_trade(signal, execution):
        trades.append((signal.action, signal.quantity, signal.product, signal.reasoning))

    engine = ScalpEngine(config, executor, Client(wall, history, path), router,
                         on_trade=on_trade, wall=wall, sleep=wall.sleep, halted=lambda: False)
    report = await asyncio.wait_for(engine.run(asyncio.Event()), 10)

    assert [t[0] for t in trades] == ["BUY", "SELL"]
    assert all(t[2] is Product.INTRADAY for t in trades)
    assert "SCALP orb" in trades[0][3] and "SCALP TARGET" in trades[1][3]
    [trade] = report.trades
    assert trade.strategy == "orb" and trade.pnl > 0
    assert trade.qty == int(50_000 // 101.5)                 # the position value cap
    assert not [p for p in paper.get_positions() if p.quantity > 0]
    assert report.by_strategy()["orb"]["wins"] == 1


async def test_engine_respects_the_daily_limits(monkeypatch):
    engine = ScalpEngine(SimpleNamespace(scalp_max_trades_per_day=1), AsyncMock(),
                         AsyncMock(), SimpleNamespace(mode="paper"), halted=lambda: False)
    now = T0.replace(hour=10)
    assert engine._can_enter(now) is None
    engine.report.entries = 1
    assert engine._can_enter(now) == "max trades today"
    engine.report.entries = 0
    assert engine._can_enter(T0.replace(hour=15)) == "outside the entry window"
    paused = ScalpEngine(SimpleNamespace(), AsyncMock(), AsyncMock(),
                         SimpleNamespace(mode="paper"), halted=lambda: True)
    assert "paused" in paused._can_enter(now)


async def test_a_refused_unconfirmed_buy_blocks_the_symbol(monkeypatch):
    executor = AsyncMock()
    executor.execute_signal = AsyncMock(return_value=ExecutionResult(
        success=False, mode="live", remaining_open=True, rejection_reason="may be working"))
    router = SimpleNamespace(mode="live", get_funds=AsyncMock(return_value=SimpleNamespace(
        total_collateral=1_000_000, available_cash=1_000_000)))
    engine = ScalpEngine(SimpleNamespace(), executor, AsyncMock(), router,
                         halted=lambda: False)
    engine.codes["TCS"] = "NSE_11536"
    from skopaq.market.candles import LiveSeries
    engine.series["TCS"] = LiveSeries()
    setup = orb(mk(ORB_FIRST + [100.5, 100.6, 100.7, 101.5], spread=0.2), P)
    await engine._enter("TCS", setup, T0.replace(hour=10))
    assert "TCS" in engine._blocked and engine.positions == {}
    signal = executor.execute_signal.await_args.args[0]
    assert signal.product is Product.INTRADAY and signal.quantity == Decimal(492)


async def test_dashboard_scalp_closes_and_status(tmp_path):
    from skopaq.execution.control import ControlChannel

    channel = ControlChannel(tmp_path / "control")
    executor = AsyncMock()
    executor.execute_signal = AsyncMock(return_value=ExecutionResult(
        success=True, mode="paper", fill_price=101.0))
    engine = ScalpEngine(SimpleNamespace(), executor, AsyncMock(), SimpleNamespace(mode="paper"),
                         control=channel, halted=lambda: False, wall=lambda: T0.replace(hour=10))
    engine.positions["TCS"] = ScalpPosition("TCS", "orb", 10, 100.0, 99.0, 102.0, T0)
    engine.prices["TCS"] = 101.0
    engine._publish()
    status = channel.read_status("scalper")
    assert status["positions"][0]["symbol"] == "TCS" and status["positions"][0]["pnl"] == 10.0

    monitor_cmd = channel.submit("close", {"symbol": "TCS"}, "a@x")          # not ours
    cmd = channel.submit("close_all", {}, "a@x", target="scalper")
    await engine._service_control()
    assert channel.result(cmd)["ok"] and engine.positions == {}
    assert channel.result(monitor_cmd) is None                              # left alone
    signal = executor.execute_signal.await_args.args[0]
    assert signal.action == "SELL" and "MANUAL CLOSE ALL" in signal.reasoning
    assert engine.report.trades[0].pnl < 10.0                              # after charges
