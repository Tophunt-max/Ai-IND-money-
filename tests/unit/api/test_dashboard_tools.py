"""Dashboard tools (skopaq/api/dashboard_tools.py): broker status and token, portfolio,
scanner status, learning, the INDstocks option chain, and the backtest / Monte Carlo /
settle jobs."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.api import dashboard, dashboard_tools

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import skopaq.broker.token_manager as tm

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tm, "TOKEN_DIR", tmp_path / ".skopaq")
    monkeypatch.setattr(tm, "TOKEN_FILE", tmp_path / ".skopaq" / "token.enc")
    monkeypatch.setattr(tm, "KEY_FILE", tmp_path / ".skopaq" / "token.key")
    monkeypatch.setenv("SKOPAQ_INDSTOCKS_TOKEN", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from skopaq.api import dashboard_auth

    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: SimpleNamespace(
        api_token=SecretStr(TOKEN), supabase_url="", supabase_anon_key="", dashboard_users=""))
    dashboard_auth.reset_state()
    dashboard._jobs.clear()
    from skopaq.api.server import app

    with TestClient(app) as c:
        yield c


def _wait(client, job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/dashboard/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_everything_needs_a_login(client):
    for path in ("/api/dashboard/broker", "/api/dashboard/portfolio", "/api/dashboard/learning",
                 "/api/dashboard/scanner/status", "/api/dashboard/options/chain"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/dashboard/broker/indstocks-token",
                       json={"token": "x" * 20}).status_code == 401


def test_broker_without_any_token(client):
    r = client.get("/api/dashboard/broker", headers=AUTH).json()
    assert r["indstocks"]["valid"] is False and r["indstocks"]["source"] == "none"
    assert "kite" not in r


def test_set_and_clear_the_indstocks_token(client):
    r = client.post("/api/dashboard/broker/indstocks-token", headers=AUTH,
                    json={"token": "eyJabcdefghijklmnop", "ttl_hours": 12})
    assert r.status_code == 200
    ind = r.json()["indstocks"]
    assert ind["valid"] is True and ind["source"] == "stored" and ind["stored"] is True
    assert 11 * 3600 < ind["remaining_seconds"] <= 12 * 3600
    assert "eyJabcdefghijklmnop" not in r.text  # the token is never sent back

    r = client.delete("/api/dashboard/broker/indstocks-token", headers=AUTH)
    assert r.json()["indstocks"]["valid"] is False


def test_env_token_has_no_known_expiry(client, monkeypatch):
    monkeypatch.setenv("SKOPAQ_INDSTOCKS_TOKEN", "env-token-abcdefghij")
    ind = client.get("/api/dashboard/broker", headers=AUTH).json()["indstocks"]
    assert ind["valid"] is True and ind["source"] == "env"
    assert ind["expires_at"] is None and ind["remaining_seconds"] is None


@pytest.mark.parametrize("body", [{"token": "short"}, {"token": "has a space in it ok"},
                                  {"token": "eyJabcdefghijklmnop", "ttl_hours": 100}])
def test_bad_tokens_are_refused(client, body):
    assert client.post("/api/dashboard/broker/indstocks-token", headers=AUTH,
                       json=body).status_code == 422


def test_portfolio_without_brokers(client):
    r = client.get("/api/dashboard/portfolio", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["indstocks"]["available"] is False
    assert "INDstocks token" in body["indstocks"]["error"]
    assert "kite" not in body


def _store_token(client):
    assert client.post("/api/dashboard/broker/indstocks-token", headers=AUTH,
                       json={"token": "eyJabcdefghijklmnop"}).status_code == 200


def test_portfolio_reads_equity_and_fno_positions(client, monkeypatch):
    from skopaq.broker import client as broker_client
    from skopaq.broker.models import Funds, Holding, Position

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get_positions(self):
            return [Position(symbol="TCS", quantity=2, average_price=100, pnl=20, product="CNC")]

        async def get_derivative_positions(self):
            return [Position(symbol="NIFTY 3 JUL 25700 CE", quantity=75, average_price=120,
                             product="MARGIN")]

        async def get_holdings(self):
            return [Holding(symbol="INFY", quantity=5, average_price=1500)]

        async def get_funds(self):
            return Funds(available_cash=1000, option_buy_available=800)

        async def get_order_book(self):
            raise RuntimeError("order book down")

    _store_token(client)
    monkeypatch.setattr(broker_client, "INDstocksClient", FakeClient)
    ind = client.get("/api/dashboard/portfolio", headers=AUTH).json()["indstocks"]
    assert ind["available"] is True
    assert ind["positions"][0]["symbol"] == "TCS"
    assert ind["fno_positions"][0]["product"] == "MARGIN"
    assert ind["funds"]["option_buy_available"] == 800
    assert ind["orders"] == [] and "order book down" in ind["errors"]["orders"]


def test_options_need_an_indstocks_token(client):
    for path in ("/api/dashboard/options/chain?symbol=NIFTY",
                 "/api/dashboard/options/suggest?symbol=NIFTY",
                 "/api/dashboard/options/expiries?symbol=NIFTY"):
        r = client.get(path, headers=AUTH)
        assert r.status_code == 503 and "INDstocks token" in r.json()["detail"], path


def test_kite_endpoints_are_gone(client):
    for path in ("/api/dashboard/kite/gtt", "/api/dashboard/kite/mutual-funds"):
        assert client.get(path, headers=AUTH).status_code == 404, path


def _chain_data():
    from datetime import date

    from skopaq.options.chain import OptionChainData, OptionContract

    def leg(strike, kind, ltp, distance):
        return OptionContract(
            tradingsymbol=f"NIFTY-Aug2026-{strike}-{kind}", security_id=str(strike),
            exchange="NFO", strike=strike, option_type=kind, expiry=date(2026, 10, 13),
            lot_size=75, ltp=ltp, volume=10, oi=1000, spot_price=25000,
            distance_pct=distance, days_to_expiry=5, theta_estimate=2.0)

    return OptionChainData(
        symbol="NIFTY", spot_price=25000, expiry=date(2026, 10, 13), lot_size=75,
        calls=[leg(25000, "CE", 150, 0.0), leg(26000, "CE", 20, 4.0)],
        puts=[leg(24000, "PE", 25, 4.0), leg(25000, "PE", 140, 0.0)],
        expiries=["2026-10-13", "2026-10-20"])


def test_option_chain_and_suggestion_from_indstocks(client, monkeypatch):
    import skopaq.options.chain as chain_mod

    async def load(symbol, expiry_index=0, **kwargs):
        assert symbol == "NIFTY" and expiry_index == 1
        return _chain_data()

    _store_token(client)
    monkeypatch.setattr(chain_mod, "load_option_chain", load)
    r = client.get("/api/dashboard/options/chain?symbol=NIFTY&expiry_index=1", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lot_size"] == 75 and body["expiry"] == "2026-10-13"
    assert body["calls"][0]["security_id"] == "25000"

    r = client.get("/api/dashboard/options/suggest?symbol=NIFTY&strategy=SHORT_PUT"
                   "&expiry_index=1", headers=AUTH).json()
    assert r["trade"]["sell_contract"]["strike"] == 24000


def test_option_symbol_is_validated(client):
    assert client.get("/api/dashboard/options/chain?symbol=NIFTY;rm",
                      headers=AUTH).status_code == 422


def test_scanner_status_when_not_started(client):
    r = client.get("/api/dashboard/scanner/status", headers=AUTH).json()
    assert r["running"] is False and r["last_candidates"] == []


def test_learning_without_a_database(client):
    r = client.get("/api/dashboard/learning", headers=AUTH).json()
    assert r["available"] is False and "DATABASE_URL" in r["error"]


def test_memory_needs_supabase(client, monkeypatch):
    monkeypatch.setenv("SKOPAQ_SUPABASE_URL", "")
    assert client.get("/api/dashboard/memory?q=banks", headers=AUTH).status_code == 503


# ── Jobs ──────────────────────────────────────────────────────────────────────


def _prices(n=300):
    import math

    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [1000 + 80 * math.sin(i / 6) for i in range(n)]
    return pd.DataFrame({
        "Date": dates.strftime("%Y-%m-%d"), "Open": close, "High": [c * 1.01 for c in close],
        "Low": [c * 0.99 for c in close], "Close": close, "Volume": [1000] * n,
    })


def test_backtest_job(client, monkeypatch):
    monkeypatch.setattr(dashboard_tools, "_history", lambda symbol, days: _prices())
    r = client.post("/api/dashboard/jobs", headers=AUTH,
                    json={"kind": "backtest", "symbol": "tcs", "days": 365,
                          "stop_loss_pct": 2, "target_pct": 4})
    assert r.status_code == 202
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    res = job["result"]
    assert res["symbol"] == "TCS" and res["stop_loss_pct"] == 2
    assert res["metrics"]["total_trades"] == len(res["trades"]) > 0
    assert 2 <= len(res["equity_curve"]) <= 202
    assert {"date", "value"} <= set(res["equity_curve"][0])


def test_montecarlo_job(client, monkeypatch):
    monkeypatch.setattr(dashboard_tools, "_history", lambda symbol, days: _prices(600))
    r = client.post("/api/dashboard/jobs", headers=AUTH,
                    json={"kind": "montecarlo", "symbol": "TCS", "simulations": 200})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    res = job["result"]
    assert res["result"]["n_simulations"] == 200
    assert "all_final_returns" not in res["result"]
    assert sum(b["count"] for b in res["histogram"]) == 200


def test_backtest_job_reports_too_little_history(client, monkeypatch):
    def short(symbol, days):
        raise ValueError(f"Not enough price history for {symbol} (3 bars)")

    monkeypatch.setattr(dashboard_tools, "_history", short)
    r = client.post("/api/dashboard/jobs", headers=AUTH, json={"kind": "backtest", "symbol": "TCS"})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "failed" and "Not enough price history" in job["error"]


def test_settle_job(client, monkeypatch):
    monkeypatch.setattr(dashboard_tools, "run_settle_job", lambda: {"settled": 4})
    r = client.post("/api/dashboard/jobs", headers=AUTH, json={"kind": "settle"})
    assert r.status_code == 202 and r.json()["symbol"] == ""
    assert _wait(client, r.json()["id"])["result"] == {"settled": 4}


@pytest.mark.parametrize("body", [
    {"kind": "backtest"},  # no symbol
    {"kind": "backtest", "symbol": "TCS", "days": 10},
    {"kind": "montecarlo", "symbol": "TCS", "simulations": 5},
    {"kind": "evolve", "symbol": "TCS"},
])
def test_bad_job_requests(client, body):
    assert client.post("/api/dashboard/jobs", headers=AUTH, json=body).status_code == 422


def test_broker_status_shows_the_automatic_token_and_it_needs_its_credentials(client):
    status = client.get("/api/dashboard/broker", headers=AUTH).json()
    assert status["indstocks"]["auto"]["configured"] is False
    assert "mpin" not in str(status).lower()
    assert client.post("/api/dashboard/broker/indstocks-token/auto",
                       json={}).status_code == 401
    refused = client.post("/api/dashboard/broker/indstocks-token/auto", json={},
                          headers=AUTH)
    assert refused.status_code == 409 and "SKOPAQ_INDSTOCKS_MPIN" in refused.json()["detail"]


def test_readiness_endpoint_answers_the_checks(client, monkeypatch):
    from skopaq.api import dashboard_control
    from skopaq.execution import readiness

    async def egress(**_kw):
        return {"ipv4": "1.1.1.1", "ipv6": None}

    monkeypatch.setattr(readiness, "egress_ips", egress)
    dashboard_control._READINESS.clear()
    assert client.get("/api/dashboard/readiness").status_code == 401
    body = client.get("/api/dashboard/readiness?live=true", headers=AUTH).json()
    assert body["live"] is True and body["passed"] is False         # no token here
    names = [c["name"] for c in body["checks"]]
    assert names[:2] == ["mode", "token"] and "static IP" in names
