"""Order lifecycle (13 states), startup reconciliation, unrealized losses in the daily
limit, cross-host leases and the entry-block alerts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from skopaq.broker.models import Funds, Position
from skopaq.constants import SafetyRules
from skopaq.execution.entry_gate import EntryBusy, EntryGate
from skopaq.execution.executor import _alert_entry_block, _unrealized
from skopaq.execution.lease import RedisLease
from skopaq.execution.order_journal import OrderJournal
from skopaq.execution.order_lifecycle import (
    TERMINAL,
    can_transition,
    lifecycles,
)
from skopaq.execution.order_lifecycle import (
    LifecycleState as S,
)
from skopaq.execution.safety_checker import SafetyChecker
from skopaq.execution.startup_reconcile import reconcile_at_start


def _lines(*events):
    return [{"ts": f"t{i}", "internal_id": "i1", "symbol": "TCS", "side": "BUY", "qty": "10",
             **e} for i, e in enumerate(events)]


def test_lifecycle_happy_path_partial_cancel_and_unknowns():
    [lc] = lifecycles(_lines({"event": "created"}, {"event": "validated"},
                             {"event": "submitting"},
                             {"event": "placed", "order_id": "EQ-1", "status": "QUEUED"},
                             {"event": "final", "order_id": "EQ-1", "status": "SUCCESS",
                              "filled": "10"}))
    assert [h[1] for h in lc.history] == ["CREATED", "VALIDATED", "SUBMITTED",
                                          "ACKNOWLEDGED", "FILLED"]
    assert lc.terminal and not lc.anomalies and lc.order_id == "EQ-1"
    [lc] = lifecycles(_lines({"event": "submitting"},
                             {"event": "placed", "order_id": "EQ-2"},
                             {"event": "cancel_sent", "order_id": "EQ-2"},
                             {"event": "final", "order_id": "EQ-2", "status": "CANCELLED",
                              "filled": "4"}))
    assert lc.state == S.CANCELLED and lc.filled == 4
    assert "CANCEL_PENDING" in [h[1] for h in lc.history]
    [lc] = lifecycles(_lines({"event": "submitting"}, {"event": "uncertain"},
                             {"event": "reconciled_absent"}))
    assert [h[1] for h in lc.history] == ["SUBMITTED", "RECONCILIATION_REQUIRED", "FAILED"]
    [lc] = lifecycles(_lines({"event": "submitting"}, {"event": "not_placed"}))
    assert lc.state == S.REJECTED
    [lc] = lifecycles(_lines({"event": "placed", "order_id": "EQ-3"},
                             {"event": "final", "order_id": "EQ-3", "status": "EXPIRED"}))
    assert lc.state == S.EXPIRED


def test_transitions_out_of_terminal_states_are_flagged_not_hidden():
    assert all(not can_transition(t, S.ACKNOWLEDGED) for t in TERMINAL)
    assert len(S) == 13
    [lc] = lifecycles(_lines({"event": "placed", "order_id": "EQ-1"},
                             {"event": "final", "order_id": "EQ-1", "status": "SUCCESS",
                              "filled": "10"},
                             {"event": "placed", "order_id": "EQ-1"}))
    assert lc.anomalies and lc.state == S.ACKNOWLEDGED      # the broker is the truth


def _row(oid, side="BUY", qty=10, sid="11536"):
    return SimpleNamespace(order_id=oid, side=side, requested_qty=Decimal(qty),
                           security_id=sid, status_raw="SUCCESS", traded_qty=Decimal(qty),
                           traded_price=Decimal("100"))


async def test_startup_reconciliation_matches_absent_ambiguous_and_external(tmp_path):
    j = OrderJournal(tmp_path)
    for iid, qty in (("found", 10), ("gone", 5), ("two", 7)):
        j.record("submitting", internal_id=iid, symbol="TCS", side="BUY", qty=Decimal(qty),
                 security_id="11536")
    import json
    p = j.path_for(j._wall().date())
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    for r in rows:
        r["pid"] = -1                                   # left by a dead process
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    alerts = []

    async def book():
        return [_row("EQ-1", qty=10), _row("EQ-7a", qty=7), _row("EQ-7b", qty=7),
                _row("EQ-MANUAL", side="SELL", qty=3)]

    rep = await reconcile_at_start(book, j, set(), lambda *a, **k: alerts.append(a))
    assert rep.ok and rep.matched == [("found", "EQ-1")] and rep.absent == ["gone"]
    assert rep.ambiguous and rep.ambiguous[0][0] == "two"
    assert "EQ-MANUAL" in rep.external
    left = {b["internal_id"] for b in j.entry_blockers()}
    assert left == {"two"}                              # only the ambiguous one still blocks
    assert {a[1] for a in alerts} >= {"startup-ambiguous:two", "external-orders"}
    assert "EQ-1" in j.own_ids_today()                  # adopted: the monitor resumes it

    async def broken():
        raise RuntimeError("401")
    bad = await reconcile_at_start(broken, j, set(), lambda *a, **k: None)
    assert not bad.ok and "unreadable" in bad.error


def test_open_losses_count_toward_the_daily_limit():
    from tests.unit.execution.test_safety_checker import _buy_order

    checker = SafetyChecker(rules=SafetyRules(market_hours_only=False, require_stop_loss=False))
    funds = Funds(available_margin=1_000_000)
    ok = checker.validate(_buy_order(qty=1, price=100), None, [], funds, 1_000_000,
                          unrealized_pnl=-20_000)
    assert ok.passed
    bad = checker.validate(_buy_order(qty=1, price=100), None, [], funds, 1_000_000,
                           unrealized_pnl=-31_000)
    assert any("open" in r and "Daily loss" in r for r in bad.rejections)
    gains = checker.validate(_buy_order(qty=1, price=100), None, [], funds, 1_000_000,
                             unrealized_pnl=+90_000)
    assert gains.passed                                 # open gains never offset losses
    rows = [Position(symbol="A", quantity=Decimal(10), average_price=100, last_price=90),
            Position(symbol="B", quantity=Decimal(5), average_price=50, last_price=0)]
    assert _unrealized(rows) == pytest.approx(-100)


class FakeRedis:
    def __init__(self, fail=False):
        self.data, self.fail = {}, fail

    async def set(self, k, v, nx=None, px=None, ex=None):
        if self.fail:
            raise ConnectionError("down")
        if nx and k in self.data:
            return None
        self.data[k] = v
        return True

    async def get(self, k):
        return self.data.get(k)

    async def eval(self, script, keys, args):
        k = keys[0]
        if self.data.get(k) != args[0]:
            return 0
        if "del" in script:
            del self.data[k]
        return 1


async def test_cross_host_entry_lease(tmp_path):
    redis = FakeRedis()
    a = RedisLease(redis, "skopaq:acct:entry")
    b = RedisLease(redis, "skopaq:acct:entry")
    async with EntryGate(wait_s=0.1, lock_dir=tmp_path / "h1", live=True, key="h1", lease=a):
        with pytest.raises(EntryBusy, match="another host"):
            async with EntryGate(wait_s=0.1, lock_dir=tmp_path / "h2", live=True, key="h2",
                                 lease=b):
                pass
    assert "skopaq:acct:entry" not in redis.data          # released by its holder
    down = RedisLease(FakeRedis(fail=True), "k")
    with pytest.raises(EntryBusy, match="cannot be checked"):
        async with EntryGate(wait_s=0.1, lock_dir=tmp_path / "h3", live=True, key="h3",
                             lease=down):
            pass
    # A foreign token cannot release someone else's lease
    c = RedisLease(redis, "x")
    await c.acquire()
    intruder = RedisLease(redis, "x")
    intruder.held = True
    await intruder.release()
    assert redis.data["x"] == c.token


def test_router_fails_closed_when_leases_are_enabled_without_redis(tmp_path, monkeypatch):
    from skopaq.broker.paper_engine import PaperEngine
    from skopaq.execution.order_router import OrderRouter

    monkeypatch.setenv("SKOPAQ_ORDER_JOURNAL_DIR", str(tmp_path / "j"))
    monkeypatch.setenv("SKOPAQ_ORDER_LOCK_DIR", str(tmp_path / "l"))
    config = SimpleNamespace(trading_mode="live", account_lease_enabled=True,
                             upstash_redis_url="", upstash_redis_token=None)
    router = OrderRouter(config, PaperEngine(initial_capital=1), live_client=object())

    async def enter():
        async with router.entry_gate():
            pass
    with pytest.raises(EntryBusy, match="lease"):
        asyncio.run(enter())


def test_entry_block_alerts_carry_an_incident_and_an_action(monkeypatch):
    sent = []
    monkeypatch.setattr("skopaq.execution.executor.get_alerter",
                        lambda: SimpleNamespace(alert=lambda *a, **k: sent.append(a)))
    order = SimpleNamespace(quantity=5, symbol="TCS")
    _alert_entry_block(order, "Daily loss 3.2% hit circuit breaker (3%)")
    _alert_entry_block(order, "Reconciliation required before a new entry: x")
    _alert_entry_block(order, "Max open positions")            # routine: no alert
    assert [a[0] for a in sent] == ["CRITICAL", "CRITICAL"]
    day = datetime.now(timezone.utc).astimezone().date()
    assert "[incident daily-loss-lockout-" in sent[0][2] and "Action:" in sent[0][2]
    assert sent[1][1].startswith("entries-blocked:") and str(day.year) in sent[1][1]
