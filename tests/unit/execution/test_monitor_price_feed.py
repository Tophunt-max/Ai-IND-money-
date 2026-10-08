"""The position monitor with the INDstocks price feed: ticks first, REST as a throttled
fallback, a faster loop with the AI tier and the resync keeping their pace."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from skopaq.broker.models import Position
from skopaq.execution.exit_plan import ExitPlanner
from skopaq.execution.position_monitor import MonitoredPosition, PositionMonitor
from tests.unit.execution._fakes import BASE_WALL

TCS = "NSE_11536"


class FakeFeed:
    def __init__(self):
        self.t = 0.0
        self.prices: dict[str, float] = {}
        self.subscribed: list[str] = []

    def clock(self):
        return self.t

    def ltp(self, code, max_age_s):
        return self.prices.get(code)

    async def subscribe(self, codes):
        self.subscribed.extend(codes)


def _config(**over):
    values = dict(trading_mode="paper", monitor_poll_interval_seconds=10,
                  monitor_tick_poll_seconds=1.0, ws_tick_max_age_seconds=5.0,
                  monitor_hard_stop_pct=0.04, monitor_eod_exit_minutes_before_close=10,
                  monitor_ai_interval_cycles=6, monitor_trailing_stop_enabled=False,
                  monitor_trailing_stop_pct=0.02, monitor_resync_cycles=3,
                  monitor_target_mode="off", daemon_min_profit_threshold_pct=0.5,
                  daemon_min_profit_threshold_inr=150.0)
    values.update(over)
    return SimpleNamespace(**values)


def _monitor(feed, client=None, config=None, **kw):
    config = config or _config()
    return PositionMonitor(MagicMock(), client or AsyncMock(), MagicMock(), config,
                           ai_enabled=False, wall=lambda: BASE_WALL, price_feed=feed,
                           exit_planner=ExitPlanner(config), **kw)


def _pos():
    return MonitoredPosition(symbol="TCS", scrip_code=TCS, entry_price=100.0, quantity=10)


async def test_a_fresh_tick_needs_no_rest_quote():
    feed = FakeFeed()
    feed.prices[TCS] = 101.5
    client = AsyncMock()
    mon = _monitor(feed, client)
    assert await mon._price(_pos()) == 101.5
    client.get_ltp.assert_not_awaited()


async def test_a_stale_feed_falls_back_to_rest_at_the_normal_pace():
    feed = FakeFeed()
    client = AsyncMock()
    client.get_ltp = AsyncMock(return_value=99.0)
    mon = _monitor(feed, client)
    pos = _pos()

    assert await mon._price(pos) == 99.0             # no tick: REST
    feed.t += 3
    assert await mon._price(pos) == 0.0              # within the 10 s poll: skipped
    assert await mon._price(pos, any_age=True) == 99.0   # the shutdown sale always asks
    feed.t += 10
    assert await mon._price(pos) == 99.0
    assert client.get_ltp.await_count == 3


def test_the_fast_loop_keeps_the_ai_and_resync_pace_in_seconds():
    mon = _monitor(FakeFeed())
    mon._watching = lambda: False                    # nothing open at the broker
    assert (mon._loop_interval, mon._scale) == (1.0, 10)
    # Every 3 polls of 10 s = every 30 one-second cycles
    assert mon._resync_due(10, []) is False and mon._resync_due(30, []) is True
    plain = PositionMonitor(MagicMock(), AsyncMock(), MagicMock(), _config(),
                            ai_enabled=False)
    assert (plain._loop_interval, plain._scale) == (10, 1)


async def test_paper_positions_are_subscribed_and_stopped_out_on_a_tick(monkeypatch):
    async def scrip(client, symbol, exchange="NSE"):
        return TCS

    monkeypatch.setattr("skopaq.broker.scrip_resolver.resolve_scrip_code", scrip)
    monkeypatch.setattr("skopaq.notifications.notify", AsyncMock())
    feed = FakeFeed()
    feed.prices[TCS] = 95.0                           # below the 4 % hard stop
    router = AsyncMock()
    router.get_positions = AsyncMock(return_value=[Position(
        symbol="TCS", quantity=Decimal(10), average_price=100.0)])
    router._paper = MagicMock()
    executor = AsyncMock()
    executor.execute_signal = AsyncMock(return_value=MagicMock(success=True, fill_price=95.0,
                                                               mode="paper"))
    client = AsyncMock()
    mon = PositionMonitor(executor, client, router, _config(monitor_tick_poll_seconds=0.001,
                                                            monitor_poll_interval_seconds=0.01),
                          ai_enabled=False, wall=lambda: BASE_WALL, price_feed=feed)

    result = await asyncio.wait_for(mon.run(), 5)

    assert feed.subscribed == [TCS]
    assert result.sells_executed == 1 and "HARD STOP" in result.exit_reasons[0]
    client.get_ltp.assert_not_awaited()
