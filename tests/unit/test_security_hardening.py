"""Hardening fixes: locked broker endpoints, MARKET-order value checks, weekends and
holidays, a live kill switch that fails closed, token key handling."""

from __future__ import annotations

import os
import stat
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from skopaq.broker.models import (
    Exchange,
    Funds,
    OrderRequest,
    OrderType,
    Product,
    Side,
    TradingSignal,
)
from skopaq.constants import SafetyRules
from skopaq.execution.safety_checker import SafetyChecker, reference_price

# ── Dashboard cannot redirect the broker's endpoints ─────────────────────────


@pytest.mark.parametrize("key", [
    "SKOPAQ_INDSTOCKS_BASE_URL", "SKOPAQ_INDSTOCKS_WS_PRICE_URL",
    "SKOPAQ_INDSTOCKS_WS_ORDER_URL", "SKOPAQ_BINANCE_BASE_URL",
    "SKOPAQ_SCHEDULER_PING_URL", "SKOPAQ_UPSTASH_REDIS_URL",
])
def test_endpoints_that_receive_secrets_are_locked(key):
    from skopaq import env_overrides

    with pytest.raises(ValueError, match="cannot be changed from the dashboard"):
        env_overrides.editable(key)


# ── MARKET BUYs are checked at their reference price ─────────────────────────

RULES = SafetyRules(market_hours_only=False, require_stop_loss=False)
FUNDS = Funds(available_margin=10_000_000)


def _market_buy(qty: int) -> OrderRequest:
    return OrderRequest(symbol="RELIANCE", exchange=Exchange.NSE, side=Side.BUY,
                        quantity=Decimal(qty), order_type=OrderType.MARKET, price=None,
                        product=Product.CNC)


def _signal(entry: float | None) -> TradingSignal:
    return TradingSignal(symbol="RELIANCE", action="BUY", confidence=100, entry_price=entry,
                         order_type=OrderType.MARKET)


def test_reference_price_prefers_the_order_then_the_signal():
    limit = _market_buy(1).model_copy(update={"price": 101.0, "order_type": OrderType.LIMIT})
    assert reference_price(limit, _signal(99.0)) == 101.0
    assert reference_price(_market_buy(1), _signal(99.0)) == 99.0
    assert reference_price(_market_buy(1), None) == 0.0


def test_market_buy_over_the_value_cap_is_rejected():
    # 300 × ₹2,500 = ₹7.5 lakh > ₹5 lakh cap; the order itself carries no price
    result = SafetyChecker(rules=RULES).validate(
        _market_buy(300), _signal(2500.0), [], FUNDS, 100_000_000)
    assert not result.passed
    assert any(r.startswith("Order value") for r in result.rejections), result.rejections


def test_market_buy_beyond_funds_is_rejected():
    result = SafetyChecker(rules=RULES).validate(
        _market_buy(100), _signal(2500.0), [], Funds(available_margin=10_000), 10_000_000)
    assert any("Insufficient margin" in r for r in result.rejections), result.rejections


def test_market_buy_without_any_price_fails_closed():
    result = SafetyChecker(rules=RULES).validate(_market_buy(1), None, [], FUNDS, 1_000_000)
    assert not result.passed
    assert any("No price for this BUY" in r for r in result.rejections)


def test_small_market_buy_with_a_price_passes():
    result = SafetyChecker(rules=RULES).validate(
        _market_buy(2), _signal(2500.0), [], FUNDS, 10_000_000)
    assert result.passed, result.rejections


# ── Weekends and NSE holidays ────────────────────────────────────────────────

HOURS = SafetyRules(market_hours_only=True, require_stop_loss=False)


def _at(utc: datetime, checker: SafetyChecker) -> list[str]:
    rejections: list[str] = []
    with patch("skopaq.execution.safety_checker.datetime") as clock:
        clock.now.return_value = utc
        checker._check_market_hours(rejections)
    return rejections


def test_saturday_during_market_hours_is_closed():
    saturday = datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc)  # 10:30 IST, Saturday
    assert any("Saturday" in r for r in _at(saturday, SafetyChecker(rules=HOURS)))


def test_weekday_during_market_hours_is_open():
    monday = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)  # 10:30 IST, Monday
    assert _at(monday, SafetyChecker(rules=HOURS, extra_holidays="")) == []


def test_configured_holiday_is_closed():
    checker = SafetyChecker(rules=HOURS, extra_holidays="2026-10-12")
    monday = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
    assert any("NSE holiday" in r for r in _at(monday, checker))


def test_listed_nse_holiday_is_closed():
    from skopaq.risk.calendar import NSE_TRADING_HOLIDAYS

    weekday = next((d for year in sorted(NSE_TRADING_HOLIDAYS)
                    for d in NSE_TRADING_HOLIDAYS[year] if d.weekday() < 5), None)
    if weekday is None:
        pytest.skip("no weekday holiday in the calendar")
    utc = datetime(weekday.year, weekday.month, weekday.day, 5, 0, tzinfo=timezone.utc)
    assert any("NSE holiday" in r for r in _at(utc, SafetyChecker(rules=HOURS,
                                                                    extra_holidays="")))


def test_bad_extra_holidays_value_does_not_crash():
    checker = SafetyChecker(rules=HOURS, extra_holidays="not-a-date")
    checker._nse_holiday(date(2026, 10, 12))  # logged, never raises


# ── Kill switch: live mode fails closed on an unreadable Supabase flag ───────


class _Down:
    def get(self, key):
        raise RuntimeError("supabase down")


@pytest.fixture
def no_halt_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.delenv("SKOPAQ_KILL_SWITCH_FAIL_OPEN", raising=False)


def _status_with(monkeypatch, mode: str):
    from types import SimpleNamespace

    from skopaq.execution import kill_switch

    monkeypatch.setattr(kill_switch, "_config",
                        lambda: SimpleNamespace(trading_halted=False, trading_mode=mode))
    monkeypatch.setattr(kill_switch, "_flags", lambda config: _Down())
    return kill_switch.status(use_cache=False)


def test_live_kill_switch_fails_closed_when_supabase_is_unreadable(no_halt_file, monkeypatch):
    status = _status_with(monkeypatch, "live")
    assert status.halted and status.source == "supabase-unreadable"


def test_paper_kill_switch_still_fails_open(no_halt_file, monkeypatch):
    assert not _status_with(monkeypatch, "paper").halted


def test_live_fail_open_can_be_chosen_explicitly(no_halt_file, monkeypatch):
    monkeypatch.setenv("SKOPAQ_KILL_SWITCH_FAIL_OPEN", "true")
    assert not _status_with(monkeypatch, "live").halted


async def test_status_async_matches_status(no_halt_file):
    from skopaq.execution import kill_switch

    assert (await kill_switch.status_async(use_cache=False)).halted is False


def test_resume_survives_an_unremovable_halt_file(tmp_path, monkeypatch):
    from skopaq.execution import kill_switch

    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setattr(kill_switch, "_flags", lambda config: None)
    kill_switch.halt("test")

    def refuse(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(kill_switch.Path, "unlink", refuse)
    assert kill_switch.resume() == []  # logged, no exception


# ── Token key ────────────────────────────────────────────────────────────────


@pytest.fixture
def token_paths(tmp_path, monkeypatch):
    from skopaq.broker import token_manager as tm

    d = tmp_path / ".skopaq"
    monkeypatch.setattr(tm, "TOKEN_DIR", d)
    monkeypatch.setattr(tm, "TOKEN_FILE", d / "token.enc")
    monkeypatch.setattr(tm, "KEY_FILE", d / "token.key")
    monkeypatch.delenv("SKOPAQ_TOKEN_ENCRYPTION_KEY", raising=False)
    return d


def test_key_and_token_files_are_private(token_paths):
    from skopaq.broker.token_manager import TokenManager

    TokenManager().set_token("abc")
    for name in ("token.key", "token.enc"):
        mode = stat.S_IMODE(os.stat(token_paths / name).st_mode)
        assert mode == 0o600, (name, oct(mode))


def test_two_managers_share_one_key(token_paths):
    from skopaq.broker.token_manager import TokenManager

    TokenManager().set_token("shared")
    assert TokenManager().get_health(notify=False).token == "shared"


def test_key_from_the_environment_is_not_written_to_disk(token_paths, monkeypatch):
    from cryptography.fernet import Fernet

    from skopaq.broker.token_manager import TokenManager

    monkeypatch.setenv("SKOPAQ_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    TokenManager().set_token("from-env-key")
    assert not (token_paths / "token.key").exists()
    assert TokenManager().get_health(notify=False).token == "from-env-key"


# ── Dashboard login dedup set is bounded ─────────────────────────────────────


def test_seen_sessions_are_capped(monkeypatch):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from skopaq.api import dashboard_auth

    dashboard_auth.reset_state()
    monkeypatch.setattr(dashboard_auth, "_SEEN_LIMIT", 3)
    monkeypatch.setattr(dashboard_auth, "_login_pool",
                        SimpleNamespace(submit=lambda fn: None))
    config = SimpleNamespace(supabase_service_key=SecretStr("k"), supabase_url="u",
                             trusted_proxy_hops=1)
    req = SimpleNamespace(headers={}, client=None)
    for i in range(10):
        user = dashboard_auth.DashboardUser(email="a@b.c", role="viewer", via="supabase",
                                            session_id=f"s{i}")
        dashboard_auth._record_login(config, user, "ok", req)
    assert len(dashboard_auth._seen_sessions) == 3
    assert "s9:ok" in dashboard_auth._seen_sessions
    dashboard_auth.reset_state()
