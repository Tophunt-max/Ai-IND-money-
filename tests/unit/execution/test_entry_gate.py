"""Sequential entries (skopaq/execution/entry_gate.py), the reconciliation blockers, the
consecutive-loss stop and the fail-closed risk state."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from skopaq.broker.models import ExecutionResult, Funds, OrderType, Position, TradingSignal
from skopaq.constants import SafetyRules
from skopaq.execution import pnl_history
from skopaq.execution.entry_gate import EntryBusy, EntryGate, entry_blockers
from skopaq.execution.executor import Executor
from skopaq.execution.order_journal import OrderJournal
from skopaq.execution.safety_checker import SafetyChecker

RULES = SafetyRules(market_hours_only=False, require_stop_loss=False)


def buy(symbol="TCS", qty=1, price=100.0) -> TradingSignal:
    return TradingSignal(symbol=symbol, action="BUY", confidence=80, entry_price=price,
                         order_type=OrderType.MARKET, quantity=Decimal(qty))


def sell(symbol="TCS", qty=1, price=100.0) -> TradingSignal:
    return TradingSignal(symbol=symbol, action="SELL", confidence=100, entry_price=price,
                         order_type=OrderType.MARKET, quantity=Decimal(qty))


class SlowRouter:
    """A paper-like router whose execute takes a while, recording overlap."""

    mode = "paper"

    def __init__(self, delay=0.05, wait_s=5.0, blockers=None):
        self.delay, self.wait_s = delay, wait_s
        self.active = self.max_active = 0
        self.blockers = blockers or []
        self.executed = []

    def entry_gate(self):
        return EntryGate(wait_s=self.wait_s, lock_dir=None, live=False)

    def entry_blockers(self):
        return list(self.blockers)

    def sell_lock(self, order):
        return None

    async def sell_inputs(self, order, position_only=False):
        return None

    async def get_positions(self):
        return [Position(symbol="TCS", quantity=Decimal(5), average_price=100.0)]

    async def get_all_positions(self):
        return await self.get_positions()

    async def get_holdings(self):
        return []

    async def get_settled_holdings(self):
        return []

    async def get_funds(self):
        return Funds(available_cash=1_000_000, available_margin=1_000_000,
                     total_collateral=1_000_000)

    async def execute(self, order, signal):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        self.executed.append(order.side.value)
        return ExecutionResult(success=True, signal=signal, mode="paper",
                               fill_price=signal.entry_price, filled_quantity=order.quantity)


def _executor(router, rules=RULES):
    return Executor(router, SafetyChecker(rules=rules))


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None
    monkeypatch.setattr("skopaq.notifications.notify", nothing)
    monkeypatch.setenv("SKOPAQ_HALT_FILE", "/nonexistent/HALT")
    monkeypatch.delenv("SKOPAQ_TRADING_HALTED", raising=False)


async def test_concurrent_entries_from_two_engines_run_one_at_a_time():
    router = SlowRouter()
    ex1, ex2 = _executor(router), _executor(router)       # scalper and F&O: two executors
    r = await asyncio.gather(ex1.execute_signal(buy("TCS")), ex2.execute_signal(buy("INFY")),
                             ex1.execute_signal(buy("SBIN")))
    assert all(x.success for x in r)
    assert router.max_active == 1


async def test_a_second_entry_is_refused_after_the_wait():
    router = SlowRouter(delay=0.3, wait_s=0.05)
    ex = _executor(router)
    first, second = await asyncio.gather(ex.execute_signal(buy("TCS")),
                                         ex.execute_signal(buy("INFY")))
    assert first.success and not second.success
    assert "another entry" in second.rejection_reason


async def test_exits_never_wait_for_the_entry_gate():
    router = SlowRouter(delay=0.2)
    ex = _executor(router)
    await asyncio.gather(ex.execute_signal(buy("TCS")), ex.execute_signal(sell("TCS")))
    assert router.max_active == 2                           # the SELL ran meanwhile


async def test_unreconciled_placements_block_entries_but_not_exits():
    router = SlowRouter(blockers=["BUY 5 TCS: placement outcome unknown"])
    ex = _executor(router)
    refused = await ex.execute_signal(buy())
    assert not refused.success and "Reconciliation required" in refused.rejection_reason
    exit_ = await ex.execute_signal(sell())
    assert exit_.success and router.executed == ["SELL"]


async def test_the_account_flock_excludes_a_second_holder(tmp_path):
    a = EntryGate(wait_s=1, lock_dir=tmp_path, live=True, key="a")
    b = EntryGate(wait_s=0.1, lock_dir=tmp_path, live=True, key="b")   # another process
    async with a:
        with pytest.raises(EntryBusy):
            async with b:
                pass
    async with b:                                           # free again
        pass
    with pytest.raises(EntryBusy):                          # live without a lock dir
        async with EntryGate(wait_s=0.1, lock_dir=None, live=True, key="c"):
            pass


def test_journal_blockers_and_operator_resolution(tmp_path):
    j = OrderJournal(tmp_path)
    j.record("submitting", internal_id="i1", symbol="TCS", side="BUY", qty=Decimal(5))
    j.record("submitting", internal_id="i2", symbol="INFY", side="BUY", qty=Decimal(1))
    j.record("placed", order_id="EQ-2", internal_id="i2", symbol="INFY", side="BUY")
    j.record("uncertain", internal_id="i3", symbol="SBIN", side="BUY", qty=Decimal(2))
    j.record("uncertain", internal_id="i4", symbol="LT", side="SELL", qty=Decimal(2))
    j.record("submitting", internal_id="i5", symbol="LT", side="BUY", qty=Decimal(1))
    j.record("not_placed", internal_id="i5", symbol="LT", side="BUY")
    found = {e["internal_id"]: e["blocker"] for e in j.entry_blockers()}
    assert found == {"i1": "sent without a recorded answer", "i3": "uncertain BUY placement"}
    # This process's own in-flight line is not a blocker for itself
    assert "i1" not in {e["internal_id"] for e in j.entry_blockers(own_pid=os.getpid())}
    assert j.resolve("i1", by="admin@example.com", note="not in the order book")
    assert {e["internal_id"] for e in j.entry_blockers()} == {"i3"}
    registry = SimpleNamespace(uncertain=lambda: [], unresolved=lambda: [])
    reasons = entry_blockers(registry, j)
    assert len(reasons) == 1 and "SBIN" in reasons[0]


def test_consecutive_losses_stop_buys_not_sells():
    from tests.unit.execution.test_safety_checker import _buy_order

    checker = SafetyChecker(rules=SafetyRules(market_hours_only=False, require_stop_loss=False,
                                              cool_down_after_loss_minutes=0))
    for _ in range(4):
        checker.record_pnl(-10)
    result = checker.validate(_buy_order(qty=1, price=100), None, [],
                              Funds(available_margin=1_000_000), 1_000_000)
    assert any("losing trades in a row" in r for r in result.rejections)
    checker.record_pnl(5)                                    # a win resets the run
    assert checker.loss_streak == 0


def test_config_can_only_tighten_the_streak_limit():
    checker = SafetyChecker(rules=RULES)
    checker.apply_config_limits(SimpleNamespace(max_consecutive_losses=99))
    assert checker.max_consecutive_losses == RULES.max_consecutive_losses
    checker.apply_config_limits(SimpleNamespace(max_consecutive_losses=2))
    assert checker.max_consecutive_losses == 2
    checker.apply_config_limits(SimpleNamespace(max_consecutive_losses=0))
    assert checker.max_consecutive_losses == 2               # 0 cannot switch it off


def test_restart_keeps_the_loss_streak_and_cool_down():
    from skopaq.db.models import TradeRecord

    now = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)   # 11:30 IST
    trades = [TradeRecord(symbol="TCS", side="BUY", quantity=Decimal(1),
                          closed_at=now - timedelta(minutes=m), pnl=Decimal(p))
              for m, p in ((90, "20"), (60, "-5"), (30, "-7"), (5, "-1"))]
    realized = pnl_history.summarize(trades, now)
    assert realized.loss_streak == 3
    assert realized.last_loss_at == now - timedelta(minutes=5)
    checker = SafetyChecker(rules=RULES)
    with patch.object(pnl_history, "load_realized_pnl", return_value=realized):
        pnl_history.seed_safety_checker(checker, MagicMock())
    assert checker.loss_streak == 3 and checker._last_loss_time == now - timedelta(minutes=5)


def test_live_risk_state_unavailable_fails_closed_for_buys_only():
    from tests.unit.execution.test_safety_checker import _buy_order

    config = MagicMock(supabase_url="https://x", trading_mode="live")
    config.supabase_service_key.get_secret_value.return_value = "key"
    config.max_shares_per_order = None
    config.max_lots_per_order = None
    config.max_consecutive_losses = None
    checker = SafetyChecker(rules=RULES)
    with patch.object(pnl_history, "load_realized_pnl", return_value=None):
        pnl_history.seed_safety_checker(checker, config)
    assert checker.risk_state_problem
    result = checker.validate(_buy_order(qty=1, price=100), None, [],
                              Funds(available_margin=1_000_000), 1_000_000)
    assert any("risk state not restored" in r for r in result.rejections)
    paper = MagicMock(supabase_url="https://x", trading_mode="paper")
    paper.supabase_service_key.get_secret_value.return_value = "key"
    checker2 = SafetyChecker(rules=RULES)
    with patch.object(pnl_history, "load_realized_pnl", return_value=None):
        pnl_history.seed_safety_checker(checker2, paper)
    assert checker2.risk_state_problem is None               # paper: process-local as before
