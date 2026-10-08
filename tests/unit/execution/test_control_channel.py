"""Dashboard control channel (skopaq/execution/control.py), the scheduler's start
request, and the monitor's commands and published status."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from skopaq.broker.models import Quote
from skopaq.broker.paper_engine import PaperEngine
from skopaq.constants import SafetyRules
from skopaq.execution.control import COMMAND_TTL_S, REQUEST_TTL_S, ControlChannel, watch_stop
from skopaq.execution.executor import Executor
from skopaq.execution.order_router import OrderRouter
from skopaq.execution.position_monitor import PositionMonitor
from skopaq.execution.safety_checker import SafetyChecker
from skopaq.execution.scheduler import JobResult, SchedulerState, ScheduleSettings, _tick
from skopaq.risk.calendar import IST
from tests.unit.execution._fakes import BASE_WALL

TCS = "NSE_11536"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _channel(tmp_path, clock=None):
    return ControlChannel(tmp_path / "control", clock=clock or Clock())


# ── The channel ──────────────────────────────────────────────────────────────


def test_status_round_trip_with_age_and_freshness(tmp_path):
    clock = Clock()
    ch = _channel(tmp_path, clock)
    ch.write_status("monitor", {"positions": [{"symbol": "TCS"}]})
    clock.t += 7
    got = ch.read_status("monitor")
    assert got["positions"] == [{"symbol": "TCS"}] and got["age_s"] == 7
    assert ch.fresh_status("monitor", 10) is not None
    assert ch.fresh_status("monitor", 5) is None
    ch.write_status("monitor", {"ended": True})
    assert ch.fresh_status("monitor", 10) is None          # an ended process is not running
    with pytest.raises(ValueError):
        ch.write_status("other", {})


def test_stop_requests_count_only_after_the_process_started_and_expire(tmp_path):
    clock = Clock()
    ch = _channel(tmp_path, clock)
    ch.request_stop("dashboard:a@x", "test")
    assert ch.stop_requested(since=clock.t - 1)["by"] == "dashboard:a@x"
    assert ch.stop_requested(since=clock.t + 1) is None    # made before this process
    clock.t += REQUEST_TTL_S + 1
    assert ch.stop_requested(since=0) is None              # too old
    ch.request_start("dashboard:a@x", job="monitor")
    assert ch.start_requested()["job"] == "monitor"
    ch.clear_start()
    assert ch.start_requested() is None


def test_commands_are_claimed_once_and_expire(tmp_path):
    clock = Clock()
    ch = _channel(tmp_path, clock)
    first = ch.submit("close", {"symbol": "TCS"}, "a@x")
    clock.t += 1
    ch.submit("close_all", {}, "a@x")
    other = ControlChannel(ch.dir, clock=clock)            # a second process
    claimed = ch.claim()
    assert [c["kind"] for c in claimed] == ["close", "close_all"] and other.claim() == []
    ch.complete(first, ok=True, message="done")
    assert ch.result(first)["ok"] is True
    stale = ch.submit("close", {"symbol": "INFY"}, "a@x")
    clock.t += COMMAND_TTL_S + 1
    assert ch.claim() == [] and ch.result(stale)["ok"] is False
    with pytest.raises(ValueError):
        ch.submit("rm -rf", {}, "a@x")


async def test_watch_stop_sets_the_event_and_clears_the_request(tmp_path):
    clock = Clock()
    ch = _channel(tmp_path, clock)
    stop = asyncio.Event()
    calls = []

    async def sleep(_s):
        calls.append(1)
        if len(calls) == 2:
            ch.request_stop("dashboard:a@x", "enough")
        await asyncio.sleep(0)

    await asyncio.wait_for(watch_stop(ch, stop, started_at=clock.t - 1, sleep=sleep), 2)
    assert stop.is_set() and ch.stop_requested(0) is None


# ── The scheduler's start request ────────────────────────────────────────────


def _sched(tmp_path, **over) -> ScheduleSettings:
    values = dict(
        scheduler_enabled=True, scheduler_mode="paper", scheduler_confirm_live=False,
        scheduler_start="09:15", scheduler_last_start="11:30", scheduler_deadline="15:45",
        scheduler_settle_at="18:30", scheduler_preflight="08:45", scheduler_poll_seconds=1,
        scheduler_kill_after_seconds=300, scheduler_state_dir=str(tmp_path / "state"),
        daemon_session_log_dir=str(tmp_path / "logs"), scheduler_ping_url="",
        heartbeat_file="", nse_holidays="", monitor_eod_exit_minutes_before_close=10,
        control_dir=str(tmp_path / "control"),
    )
    values.update(over)
    return ScheduleSettings.from_config(SimpleNamespace(**values))


def _tick_at(settings, hhmm, day=(2026, 9, 28)):
    h, m = map(int, hhmm.split(":"))
    now = datetime(*day, h, m, tzinfo=IST)
    calls, alerts = [], []

    def runner(cmd, **kwargs):
        calls.append(cmd[3:])
        return JobResult(0)

    state = SchedulerState(settings.state_dir, settings.log_dir)
    _tick(now, settings, state, runner=runner, alert=alerts.append,
          ping=lambda url, ok: None, stop=threading.Event(), clock=lambda: now)
    return calls, alerts, state


def test_a_start_request_runs_the_session_after_the_catch_up_window(tmp_path):
    settings = _sched(tmp_path)
    ControlChannel(settings.control_dir).request_start("dashboard:a@x")
    calls, alerts, state = _tick_at(settings, "13:10")    # past last_start 11:30
    assert calls == [["daemon", "--once", "--paper"]] and alerts == []
    assert state.last_exit("daemon", datetime(2026, 9, 28).date()) == 0
    assert ControlChannel(settings.control_dir).start_requested() is None


@pytest.mark.parametrize("hhmm, day, job, why", [
    ("15:10", (2026, 9, 28), "daemon", "only between"),
    ("10:00", (2026, 10, 2), "daemon", ""),              # a holiday
    ("10:00", (2026, 9, 28), "monitor", "live mode only"),
])
def test_start_requests_that_are_refused(tmp_path, hhmm, day, job, why):
    settings = _sched(tmp_path)
    ControlChannel(settings.control_dir).request_start("dashboard:a@x", job=job)
    calls, alerts, _ = _tick_at(settings, hhmm, day)
    assert all(c[0] != job for c in calls)
    refusal = [a for a in alerts if "ignored" in a]
    assert refusal and why in refusal[0]


def test_a_live_monitor_start_request_runs_the_monitor(tmp_path):
    settings = _sched(tmp_path, scheduler_mode="live", scheduler_confirm_live=True)
    ControlChannel(settings.control_dir).request_start("dashboard:a@x", job="monitor")
    calls, _, _ = _tick_at(settings, "13:00")
    assert calls == [["monitor"]]


# ── The monitor: commands and status ─────────────────────────────────────────


RULES = SafetyRules(market_hours_only=False, require_stop_loss=False,
                    max_lots_per_position=10_000, max_order_value_inr=10_000_000,
                    max_position_pct=1.0)


def _paper_monitor(tmp_path, monkeypatch, *, ltp=100.0):
    async def scrip(client, symbol, exchange="NSE"):
        return {"TCS": TCS, "INFY": "NSE_1594"}[symbol]

    monkeypatch.setattr("skopaq.broker.scrip_resolver.resolve_scrip_code", scrip)
    monkeypatch.setattr("skopaq.notifications.notify", AsyncMock())
    config = SimpleNamespace(
        trading_mode="paper", monitor_poll_interval_seconds=0.001,
        monitor_hard_stop_pct=0.04, monitor_eod_exit_minutes_before_close=10,
        monitor_ai_interval_cycles=2, monitor_trailing_stop_enabled=False,
        monitor_trailing_stop_pct=0.02, monitor_target_mode="off",
        daemon_min_profit_threshold_pct=0.5, daemon_min_profit_threshold_inr=150.0,
        max_sector_concentration_pct=1.0)
    paper = PaperEngine(initial_capital=1_000_000)
    for sym in ("TCS", "INFY"):
        paper.update_quote(Quote(symbol=sym, ltp=ltp, close=ltp))
    router = OrderRouter(config, paper)
    executor = Executor(router, SafetyChecker(rules=RULES))
    client = MagicMock()
    client.get_ltp = AsyncMock(return_value=ltp)
    channel = ControlChannel(tmp_path / "control")
    exits = []

    async def on_exit(signal, execution):
        exits.append((signal.action, signal.symbol, signal.quantity, signal.reasoning))

    monitor = PositionMonitor(executor, client, router, config, ai_enabled=False,
                              on_exit=on_exit, wall=lambda: BASE_WALL, control=channel)
    return monitor, executor, paper, channel, exits


async def test_dashboard_commands_on_a_paper_session(tmp_path, monkeypatch):
    from skopaq.broker.models import TradingSignal

    monitor, executor, paper, channel, exits = _paper_monitor(tmp_path, monkeypatch)
    assert (await executor.execute_signal(TradingSignal(
        symbol="TCS", action="BUY", entry_price=100.0, quantity=Decimal(10)))).success

    plan = channel.submit("set_plan", {"symbol": "TCS", "stop_loss": 97.5}, "dashboard:a@x")
    bad = channel.submit("set_plan", {"symbol": "TCS", "stop_loss": 120}, "dashboard:a@x")
    buy = channel.submit("order", {"symbol": "INFY", "side": "BUY", "quantity": 4},
                         "dashboard:a@x")
    run = asyncio.create_task(monitor.run())
    for _ in range(500):
        if channel.result(buy) is not None:
            break
        await asyncio.sleep(0.005)
    status = channel.read_status("monitor")
    close = channel.submit("close_all", {}, "dashboard:a@x")
    await asyncio.wait_for(run, 5)

    assert channel.result(plan)["ok"] and "stop 97.50" in channel.result(plan)["message"]
    assert channel.result(bad)["ok"] is False and "below the LTP" in channel.result(bad)["message"]
    assert channel.result(buy)["ok"] and "BUY 4 INFY" in channel.result(buy)["message"]
    assert channel.result(close)["ok"]
    assert {r["symbol"] for r in status["positions"]} == {"TCS", "INFY"}
    tcs = next(r for r in status["positions"] if r["symbol"] == "TCS")
    assert tcs["stop_loss"] == 97.5 and tcs["ltp"] == 100.0
    sells = [e for e in exits if e[0] == "SELL"]
    assert {(s, q) for _, s, q, _ in sells} == {("TCS", Decimal(10)), ("INFY", Decimal(4))}
    assert all("MANUAL CLOSE ALL" in r for *_, r in sells)
    assert channel.read_status("monitor")["ended"] is True
    assert not [p for p in paper.get_positions() if p.quantity > 0]


async def test_a_close_for_an_unknown_symbol_is_refused(tmp_path, monkeypatch):
    monitor, *_ , channel, _ = _paper_monitor(tmp_path, monkeypatch)
    ok, message = await monitor._command({"kind": "close", "payload": {"symbol": "XYZ"}},
                                         [], MagicMock())
    assert ok is False and "not monitored" in message
    ok, message = await monitor._command({"kind": "explode"}, [], MagicMock())
    assert ok is False
