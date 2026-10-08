"""Production hardening (Phase 6): the automatic TOTP token, the exchange algo ids, the
broker's rate-limit categories and 429 back-off, the live readiness check and the static-IP
gate, and the daemon's shared price feed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from skopaq.broker import auto_token
from skopaq.broker import client as client_mod
from skopaq.broker.client import INDstocksClient, algo_id_for
from skopaq.broker.models import Exchange, Funds, OrderRequest, OrderType, Side, UserProfile
from skopaq.execution import readiness

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"      # RFC 6238's "12345678901234567890"
NOW = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)   # 08:30 IST


@pytest.fixture
def token_dir(tmp_path, monkeypatch):
    from skopaq.broker import token_manager as tm

    d = tmp_path / ".skopaq"
    monkeypatch.setattr(tm, "TOKEN_DIR", d)
    monkeypatch.setattr(tm, "TOKEN_FILE", d / "token.enc")
    monkeypatch.setattr(tm, "KEY_FILE", d / "token.key")
    return d


def _config(**kw):
    base = dict(indstocks_client_id="CLIENT-1", indstocks_mpin=SecretStr("4321"),
                indstocks_totp_secret=SecretStr(SECRET),
                indstocks_base_url="https://api.indstocks.test")
    base.update(kw)
    return SimpleNamespace(**base)


# ── TOTP token ───────────────────────────────────────────────────────────────


def test_totp_matches_the_rfc_6238_vectors():
    assert auto_token.totp(SECRET, 59) == "287082"
    assert auto_token.totp(SECRET, 1111111109) == "081804"
    assert auto_token.totp(SECRET.lower() + "  ", 1234567890) == "005924"
    with pytest.raises(auto_token.AutoTokenError, match="base32"):
        auto_token.totp("not base32 !!", 1)


def test_credentials_need_all_three_as_text():
    assert auto_token.configured(_config())
    assert not auto_token.configured(_config(indstocks_mpin=SecretStr("")))
    assert not auto_token.configured(MagicMock())          # a mock config is not set up


async def test_a_token_is_generated_stored_and_throttled(token_dir):
    from skopaq.broker.token_manager import TokenManager

    seen = []

    def broker(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"token": "TOK-NEW"}})

    clock = [1_800_000_010.0]                       # 10 s into a 30 s TOTP window
    expires = await auto_token.generate_token(
        _config(), transport=httpx.MockTransport(broker), wall=lambda: clock[0])
    [req] = seen
    assert req.url.path == "/generate/token" and req.headers["x-api-key"] == "CLIENT-1"
    body = json.loads(req.content)
    assert body == {"mpin": "4321", "totp": auto_token.totp(SECRET, clock[0])}
    assert TokenManager().get_token() == "TOK-NEW"
    assert expires > datetime.fromtimestamp(clock[0], timezone.utc) + timedelta(hours=23)
    # The broker allows one a minute: a second request is not sent
    clock[0] += 30
    with pytest.raises(auto_token.AutoTokenError, match="less than a minute"):
        await auto_token.generate_token(_config(), transport=httpx.MockTransport(broker),
                                        wall=lambda: clock[0])
    assert len(seen) == 1


async def test_failures_pause_generation_before_the_brokers_lockout(token_dir):
    calls = []

    def broker(request):
        calls.append(request)
        return httpx.Response(401, json={"status": "error", "message": "Invalid TOTP"})

    clock = [1_800_000_010.0]
    for _ in range(2):
        with pytest.raises(auto_token.AutoTokenError) as info:
            await auto_token.generate_token(_config(), transport=httpx.MockTransport(broker),
                                            wall=lambda: clock[0])
        assert "4321" not in str(info.value) and "Invalid TOTP" in str(info.value)
        clock[0] += 70
    with pytest.raises(auto_token.AutoTokenError, match="paused"):
        await auto_token.generate_token(_config(), transport=httpx.MockTransport(broker),
                                        wall=lambda: clock[0])
    assert len(calls) == 2                                  # the third never reached it
    state = auto_token.generation_state(clock[0])
    assert state["paused_until"] and state["recent_failures"] == 2


async def test_ensure_token_makes_one_only_when_the_session_needs_it(token_dir):
    from skopaq.broker.token_manager import TokenManager

    calls = []

    def broker(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"token": f"TOK-{len(calls)}"}})

    until = datetime.now(timezone.utc) + timedelta(hours=8)
    off = await auto_token.ensure_token(_config(indstocks_client_id=""), until)
    assert not off.ok and "INDstocks token invalid" in off.message
    made = await auto_token.ensure_token(_config(), until,
                                         transport=httpx.MockTransport(broker))
    assert made.ok and made.generated and TokenManager().get_token() == "TOK-1"
    again = await auto_token.ensure_token(_config(), until,
                                          transport=httpx.MockTransport(broker))
    assert again.ok and not again.generated and len(calls) == 1


# ── Orders and rate limits ───────────────────────────────────────────────────


def test_algo_ids_per_exchange():
    assert algo_id_for("NSE") == "99999"
    assert algo_id_for("BSE") == "9999999999999999"
    cfg = SimpleNamespace(indstocks_algo_id_nse="12345", indstocks_algo_id_bse="oops")
    assert algo_id_for("NSE", cfg) == "12345"
    assert algo_id_for("BSE", cfg) == "9999999999999999"     # not digits: the default


def _client(handler, **cfg):
    config = MagicMock()
    config.indstocks_base_url = "https://api.indstocks.test"
    config.indstocks_order_remarks_enabled = False
    for k, v in cfg.items():
        setattr(config, k, v)
    tokens = MagicMock()
    tokens.get_token.return_value = "TOKEN"
    return INDstocksClient(config, tokens, transport=httpx.MockTransport(handler))


async def test_a_bse_order_carries_the_bse_algo_id():
    bodies = []

    def broker(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success",
                                         "data": {"order_id": "EQ-1", "order_status": "O"}})

    async with _client(broker) as client:
        for exchange in (Exchange.NSE, Exchange.BSE):
            await client.place_order(OrderRequest(
                symbol="RELIANCE", exchange=exchange, side=Side.BUY, quantity=Decimal(1),
                order_type=OrderType.LIMIT, price=100.0, security_id="1"))
    assert [b["algo_id"] for b in bodies] == ["99999", "9999999999999999"]


def test_market_paths_use_the_data_and_quote_limiters():
    assert client_mod._market_limiter("/market/quotes/ltp") is client_mod._quote_limiter
    assert client_mod._market_limiter("/market/historical/1minute") is client_mod._data_limiter
    assert client_mod._market_limiter("/market/option-chain") is client_mod._data_limiter
    assert client_mod._market_limiter("/market/instruments/search") is client_mod._data_limiter
    assert client_mod._market_limiter("/order") is None


async def test_a_rate_limited_read_is_retried_and_records_the_broker_clock(monkeypatch):
    waits = []

    async def no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(client_mod, "_sleep", no_sleep)
    answers = [httpx.Response(429, json={"message": "Too Many Requests"},
                              headers={"Retry-After": "2"}),
               httpx.Response(200, json={"data": {"NSE_1": {"live_price": 101.5}}},
                              headers={"Date": "Thu, 08 Oct 2026 03:00:00 GMT"})]

    async with _client(lambda request: answers.pop(0)) as client:
        assert await client.get_ltp("NSE_1") == 101.5
        assert client.server_date == NOW
    assert waits == [2.0]


# ── Readiness ────────────────────────────────────────────────────────────────


def test_static_ips_and_the_egress_verdict():
    cfg = SimpleNamespace(indstocks_static_ips="13.234.1.2, 2406:da1a::1 , nope")
    allowed = readiness.static_ips(cfg)
    assert allowed == ["13.234.1.2", "2406:da1a::1"]
    assert readiness.egress_verdict({"ipv4": "13.234.1.2", "ipv6": None}, allowed)[0] is True
    assert readiness.egress_verdict({"ipv4": "1.1.1.1", "ipv6": "2406:da1a::1"},
                                    allowed)[0] is True        # the IPv6 one is whitelisted
    assert readiness.egress_verdict({"ipv4": "1.1.1.1", "ipv6": None}, allowed)[0] is False
    assert readiness.egress_verdict({"ipv4": None, "ipv6": None}, allowed)[0] is None
    assert readiness.egress_verdict({"ipv4": "1.1.1.1"}, [])[0] is None


async def test_live_egress_problem_stops_a_session_only_on_a_known_mismatch():
    def ipify(request):
        if request.url.host == "api.ipify.org":
            return httpx.Response(200, json={"ip": "1.1.1.1"})
        return httpx.Response(503)

    transport = httpx.MockTransport(ipify)
    cfg = SimpleNamespace(indstocks_static_ips="13.234.1.2")
    assert "not among the whitelisted" in await readiness.live_egress_problem(
        cfg, transport=transport)
    assert await readiness.live_egress_problem(
        SimpleNamespace(indstocks_static_ips="1.1.1.1"), transport=transport) == ""
    assert await readiness.live_egress_problem(
        SimpleNamespace(indstocks_static_ips=""), transport=transport) == ""


def _account(**flags):
    client = MagicMock()
    profile = UserProfile(user_id="7", name="Asha", ucc="1ABC", **flags)
    client.get_profile = AsyncMock(return_value=profile)
    client.get_funds = AsyncMock(return_value=Funds(available_cash=50_000,
                                                    option_buy_available=20_000))
    client.server_date = NOW - timedelta(seconds=45)              # the host is 45 s ahead
    return client


async def test_readiness_judges_the_account_clock_and_ip_for_live(token_dir):
    from skopaq.broker.token_manager import TokenManager

    TokenManager().set_token("TOK", ttl_hours=24)
    cfg = SimpleNamespace(trading_mode="paper", fno_enabled=True, indstocks_client_id="",
                          indstocks_static_ips="13.234.1.2", ws_price_feed_enabled=True,
                          ws_order_feed_enabled=False, control_dir="", supabase_url="",
                          supabase_service_key=SecretStr(""))
    client = _account(is_nse_onboarded=True, is_nse_fno_onboarded=False,
                      is_ddpi_active=False)
    result = await readiness.check_readiness(
        cfg, live=True, client=client, egress={"ipv4": "1.1.1.1", "ipv6": None},
        wall=lambda: NOW)
    status = {c.name: c.status for c in result.checks}
    assert status["token"] == "ok" and status["broker"] == "ok"
    assert status["F&O segment"] == "fail" and status["DDPI"] == "warn"
    assert status["clock"] == "fail" and status["static IP"] == "fail"
    assert status["funds"] == "ok" and status["websockets"] == "ok"
    assert not result.passed
    assert [c.name for c in result.checks][:2] == ["mode", "token"]
    # Paper: an IP or segment problem only warns
    paper = await readiness.check_readiness(
        cfg, live=False, client=_account(is_nse_fno_onboarded=False),
        egress={"ipv4": "1.1.1.1"}, wall=lambda: NOW)
    status = {c.name: c.status for c in paper.checks}
    assert status["static IP"] == "warn" and status["F&O segment"] == "warn"


async def test_readiness_without_a_token_fails_and_skips_the_broker(token_dir):
    cfg = SimpleNamespace(trading_mode="live", indstocks_static_ips="", control_dir="",
                          supabase_url="", supabase_service_key="")
    result = await readiness.check_readiness(cfg, egress={"ipv4": "1.1.1.1"},
                                             wall=lambda: NOW)
    names = {c.name: c.status for c in result.checks}
    assert names["token"] == "fail" and "broker" not in names and not result.passed
    assert result.as_dict()["passed"] is False


# ── The daemon's shared price feed ───────────────────────────────────────────


async def test_the_monitor_shares_the_scalpers_feed_and_does_not_stop_it():
    from skopaq.execution.daemon import TradingDaemon

    feed = SimpleNamespace(stop=AsyncMock(), start=AsyncMock())
    result = SimpleNamespace(positions_left=[])
    daemon = SimpleNamespace(_scalp_feed=feed, _fno_feed=None, _price_feed=None,
                             _config=SimpleNamespace(ws_price_feed_enabled=True),
                             _run_monitor=AsyncMock(return_value=result),
                             _monitor_again=lambda r: False)

    async def run_monitor():
        assert daemon._price_feed is feed
        return result

    daemon._run_monitor = run_monitor
    assert await TradingDaemon._phase_monitor(daemon) is result
    feed.stop.assert_not_awaited()
    feed.start.assert_not_awaited()
    assert daemon._price_feed is None
