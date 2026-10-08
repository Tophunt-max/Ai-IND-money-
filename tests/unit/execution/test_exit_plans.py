"""Exit plans (skopaq/execution/exit_plan.py) and how the monitor follows them: the
BUY's stop-loss, the target with partial booking, the rest trailing from breakeven, and
the plan surviving a restart."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from skopaq.broker.models import ExecutionResult, Quote, TradingSignal
from skopaq.broker.paper_engine import PaperEngine
from skopaq.constants import SafetyRules
from skopaq.execution.executor import Executor
from skopaq.execution.exit_plan import ExitPlan, ExitPlanner, ExitPlanStore
from skopaq.execution.order_router import OrderRouter
from skopaq.execution.position_monitor import MonitoredPosition, PositionMonitor
from skopaq.execution.safety_checker import SafetyChecker
from skopaq.risk.calendar import IST
from tests.unit.execution._fakes import BASE_WALL, Script

RULES = SafetyRules(market_hours_only=False, require_stop_loss=False,
                    max_lots_per_position=10_000, max_order_value_inr=10_000_000,
                    max_position_pct=1.0)
TCS = "NSE_11536"


def _settings(**over) -> SimpleNamespace:
    values = dict(
        trading_mode="paper", monitor_target_mode="rr", monitor_target_rr=2.0,
        monitor_target_pct=0.03, monitor_target_inr=1000.0, monitor_partial_booking_pct=0.5,
        monitor_hard_stop_pct=0.04, monitor_trailing_stop_pct=0.02,
        monitor_trailing_stop_enabled=False, monitor_poll_interval_seconds=0.001,
        monitor_eod_exit_minutes_before_close=10, monitor_ai_interval_cycles=2,
        monitor_resync_cycles=3, daemon_min_profit_threshold_pct=0.5,
        daemon_min_profit_threshold_inr=150.0, max_sector_concentration_pct=1.0,
        initial_paper_capital=1_000_000,
    )
    values.update(over)
    return SimpleNamespace(**values)


def _store(tmp_path) -> ExitPlanStore:
    return ExitPlanStore(tmp_path / "plans", today=lambda: BASE_WALL)


# ── The planner's arithmetic ─────────────────────────────────────────────────


@pytest.mark.parametrize("mode, extra, target", [
    ("rr", {"monitor_target_rr": 2.0}, 108.0),        # 100 + 2 × (100 − 96)
    ("rr", {"monitor_target_rr": 1.5}, 106.0),
    ("pct", {"monitor_target_pct": 0.03}, 103.0),
    ("inr", {"monitor_target_inr": 500.0}, 105.0),    # ₹500 over 100 shares
    ("off", {}, None),
])
def test_target_modes(mode, extra, target):
    planner = ExitPlanner(_settings(monitor_target_mode=mode, **extra))
    plan = planner.make("tcs", "paper", 100.0, 100, stop=96.0)
    assert (plan.symbol, plan.stop_loss, plan.target) == ("TCS", 96.0, target)


def test_a_missing_or_wrong_stop_uses_the_hard_stop_and_bad_settings_turn_targets_off():
    planner = ExitPlanner(_settings())
    assert planner.make("TCS", "paper", 100.0, 10).stop_loss == 96.0
    assert planner.make("TCS", "paper", 100.0, 10, stop=101.0).stop_loss == 96.0
    assert ExitPlanner(MagicMock()).target_mode == "off"      # a test double / typo
    assert ExitPlanner(_settings(monitor_target_mode="moon")).target_mode == "off"
    assert ExitPlanner(_settings(monitor_partial_booking_pct=0)).partial_fraction == 1.0


@pytest.mark.parametrize("fraction, qty, booking", [(0.5, 10, 5), (0.5, 1, 0), (1.0, 10, 0),
                                                    (0.3, 10, 3)])
def test_booking_quantity(fraction, qty, booking):
    plan = ExitPlanner(_settings(monitor_partial_booking_pct=fraction)).make(
        "TCS", "paper", 100.0, qty)
    assert plan.booking_qty == booking     # 0: the whole position at the target


# ── The store ────────────────────────────────────────────────────────────────


def test_store_round_trip_keeps_modes_apart(tmp_path):
    store = _store(tmp_path)
    planner = ExitPlanner(_settings(), store)
    plan = planner.plan_for_entry("TCS", "live", 100.0, 10, stop=96.0)
    plan.booked_qty, plan.target_hit, plan.high_water_mark = 5, True, 111.0
    planner.save(plan)

    again = ExitPlanner(_settings(), _store(tmp_path)).get("live", "tcs")
    assert (again.booked_qty, again.target_hit, again.high_water_mark, again.source) == (
        5, True, 111.0, "signal")
    assert _store(tmp_path).get("paper", "TCS") is None
    assert (tmp_path / "plans" / f"{BASE_WALL.date().isoformat()}.json").is_file()


def test_an_unwritable_store_keeps_the_plan_in_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    planner = ExitPlanner(_settings(), ExitPlanStore(blocker / "plans"))
    plan = planner.plan_for_entry("TCS", "paper", 100.0, 10)
    assert planner.get("paper", "TCS") is plan


def test_a_position_grown_since_its_plan_books_a_share_of_all_of_it(tmp_path):
    planner = ExitPlanner(_settings(), _store(tmp_path))
    planner.plan_for_entry("TCS", "paper", 100.0, 10, stop=96.0)
    plan = planner.plan_for_position("TCS", "paper", 100.0, 16)
    assert (plan.initial_qty, plan.booking_qty) == (16, 8)
    # A new BUY after the earlier position was sold: only what is held counts
    fresh = planner.plan_for_entry("TCS", "paper", 101.0, 4, stop=99.0)
    assert (fresh.initial_qty, fresh.booked_qty, fresh.target_hit) == (4, 0, False)
    default = planner.plan_for_position("INFY", "paper", 200.0, 4)
    assert (default.source, default.stop_loss, default.target) == ("default", 192.0, 216.0)


# ── The monitor's rules ──────────────────────────────────────────────────────


def _monitor(config=None, planner=None) -> PositionMonitor:
    config = config or _settings()
    return PositionMonitor(MagicMock(), MagicMock(), MagicMock(), config, ai_enabled=False,
                           wall=lambda: BASE_WALL, exit_planner=planner or ExitPlanner(config))


def _tracked(mon: PositionMonitor, qty=10, stop=None) -> MonitoredPosition:
    pos = MonitoredPosition(symbol="TCS", scrip_code=TCS, entry_price=100.0, quantity=qty)
    if stop is not None:
        mon._planner.plan_for_entry("TCS", "paper", 100.0, qty, stop=stop)
    mon._attach_plan(pos)
    return pos


def test_the_buys_stop_is_used_when_tighter_than_the_hard_stop():
    mon = _monitor()
    pos = _tracked(mon, stop=98.0)
    assert mon._check_safety(pos, 98.5) is None
    assert mon._check_safety(pos, 98.0).startswith("STOP LOSS")
    assert mon._check_safety(_tracked(_monitor()), 95.0).startswith("HARD STOP")


def test_the_target_books_part_then_the_rest_trails_from_breakeven():
    mon = _monitor()
    pos = _tracked(mon, stop=96.0)                       # target 108
    assert mon._target_exit(pos, 107.9) is None
    reason, qty = mon._target_exit(pos, 108.0)
    assert qty == 5 and reason.startswith("TARGET HIT") and "booking 5 of 10" in reason

    pos.plan.booked_qty, pos.plan.target_hit, pos.quantity = 5, True, 5
    assert mon._target_exit(pos, 120.0) is None          # booked once only
    mon._note_high(pos, 112.0)
    assert mon._check_safety(pos, 110.0) is None         # trail 109.76
    assert mon._check_safety(pos, 109.7).startswith("TRAILING STOP after target")
    pos.high_water_mark = 100.5                          # trail below breakeven
    assert "breakeven" in mon._check_safety(pos, 100.0)


def test_a_one_share_position_or_full_booking_sells_everything_at_the_target():
    mon = _monitor(_settings(monitor_partial_booking_pct=1.0))
    reason, qty = mon._target_exit(_tracked(mon), 108.0)
    assert qty == 10 and "selling all 10" in reason
    mon = _monitor()
    assert mon._target_exit(_tracked(mon, qty=1), 108.0)[1] == 1


def test_targets_off_keeps_the_old_rules():
    mon = _monitor(_settings(monitor_target_mode="off"))
    pos = _tracked(mon)
    assert mon._target_exit(pos, 150.0) is None and mon._check_safety(pos, 150.0) is None


# ── End to end, paper: Executor → plan → monitor ─────────────────────────────


class _Prices:
    """A client whose LTP walks through ``prices`` (then stays at the last)."""

    def __init__(self, prices):
        self.prices = list(prices)

    async def get_ltp(self, scrip_code):
        return self.prices.pop(0) if len(self.prices) > 1 else self.prices[0]


async def test_paper_buy_books_at_the_target_and_trails_the_rest(tmp_path, monkeypatch):
    async def scrip(client, symbol, exchange="NSE"):
        return TCS

    monkeypatch.setattr("skopaq.broker.scrip_resolver.resolve_scrip_code", scrip)
    monkeypatch.setattr("skopaq.notifications.notify", AsyncMock())
    config = _settings()
    planner = ExitPlanner(config, _store(tmp_path))
    paper = PaperEngine(initial_capital=1_000_000)
    paper.update_quote(Quote(symbol="TCS", ltp=100.0, close=100.0))
    router = OrderRouter(config, paper)
    executor = Executor(router, SafetyChecker(rules=RULES), exit_planner=planner)

    buy = TradingSignal(symbol="TCS", action="BUY", confidence=80, entry_price=100.0,
                        quantity=Decimal(10), stop_loss=96.0)
    bought = await executor.execute_signal(buy)
    assert bought.success, bought.rejection_reason
    fill = bought.fill_price
    assert buy.stop_loss == 96.0 and buy.target == round(fill + 2 * (fill - 96.0), 2)

    exits = []

    async def on_exit(signal, execution):
        exits.append((signal.quantity, signal.reasoning))

    monitor = PositionMonitor(executor, _Prices([100.0, 109.0, 112.0, 109.0]), router, config,
                              ai_enabled=False, on_exit=on_exit, wall=lambda: BASE_WALL,
                              exit_planner=planner)
    result = await asyncio.wait_for(monitor.run(), 10)

    assert [q for q, _ in exits] == [Decimal(5), Decimal(5)]
    assert exits[0][1].startswith("TARGET HIT") and "TRAILING STOP after target" in exits[1][1]
    assert result.sells_executed == 2 and result.total_pnl > 0
    assert not [p for p in await router.get_positions() if p.quantity > 0]
    saved = ExitPlanner(config, _store(tmp_path)).get("paper", "TCS")
    assert (saved.booked_qty, saved.target_hit, saved.high_water_mark) == (5, True, 112.0)


# ── Live: a restarted monitor does not book the target twice ─────────────────


@pytest.fixture
def live_harness(monkeypatch):
    from tests.unit.execution import test_position_monitor_live as live_tests

    async def security_id(client, symbol, exchange="NSE"):
        return "11536"

    async def scrip(client, symbol, exchange="NSE"):
        return TCS

    monkeypatch.setattr("skopaq.execution.order_router.resolve_security_id", security_id)
    monkeypatch.setattr("skopaq.broker.scrip_resolver.resolve_scrip_code", scrip)
    monkeypatch.setattr("skopaq.notifications.notify", AsyncMock())
    from skopaq.execution import order_alerts
    from tests.unit.execution._fakes import AlertSpy

    monkeypatch.setattr(order_alerts, "_alerter", AlertSpy())
    return live_tests.Live


async def test_live_target_booking_survives_a_monitor_restart(tmp_path, live_harness):
    target_cfg = dict(monitor_target_mode="rr", monitor_target_rr=2.0,
                      monitor_partial_booking_pct=0.5)
    live = live_harness({"TCS": (10, 100.0)}, ltps={TCS: 110.0}, **target_cfg)
    planner = ExitPlanner(live.config, _store(tmp_path))
    live.monitor._planner = planner
    live.broker.by_symbol["TCS"] = [Script(timeline=[(0, {
        "status": "SUCCESS", "traded_qty": 5, "traded_price": "110"})])]

    result = await live.run(stop_at=8)

    sells = [c for c in live.broker.placed()]
    assert [c[2] for c in sells] == [Decimal(5)]                 # the booking only
    assert result.sells_executed == 1 and result.positions_left == ["TCS"]
    saved = ExitPlanner(live.config, _store(tmp_path)).get("live", "TCS")
    assert (saved.booked_qty, saved.target_hit) == (5, True)

    # A new process: 5 still held, LTP still above the target and the trail
    again = live_harness({"TCS": (5, 100.0)}, ltps={TCS: 110.0}, **target_cfg)
    again.monitor._planner = ExitPlanner(again.config, _store(tmp_path))
    await again.run(stop_at=6)
    assert again.broker.placed() == []


async def test_executor_makes_no_plan_for_a_failed_buy(tmp_path):
    planner = MagicMock()
    executor = Executor(MagicMock(), MagicMock(), exit_planner=planner)
    signal = TradingSignal(symbol="TCS", action="BUY", entry_price=100.0)
    executor._plan_exit(signal, ExecutionResult(success=True, mode="paper"), 0)
    planner.plan_for_entry.assert_not_called()
    planner.plan_for_entry.side_effect = RuntimeError("disk")
    executor._plan_exit(signal, ExecutionResult(success=True, mode="paper", fill_price=100.0),
                        3)                                    # never raises
    assert signal.target is None


def test_plan_from_dict_rejects_garbage():
    assert ExitPlan.from_dict({"symbol": "TCS"}) is None
    created = datetime.fromisoformat(ExitPlanner(_settings()).make(
        "TCS", "paper", 1.0, 1).created_at)
    assert created.utcoffset() == datetime.now(IST).utcoffset()
