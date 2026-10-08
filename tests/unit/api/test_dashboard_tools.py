"""Dashboard tools (skopaq/api/dashboard_tools.py): broker status and token, portfolio,
scanner status, learning, Kite read-outs, and the backtest / Monte Carlo / settle jobs."""

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
    monkeypatch.setenv("SKOPAQ_KITE_API_KEY", "")
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
                 "/api/dashboard/scanner/status", "/api/dashboard/kite/gtt"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/dashboard/broker/indstocks-token",
                       json={"token": "x" * 20}).status_code == 401


def test_broker_without_any_token(client):
    r = client.get("/api/dashboard/broker", headers=AUTH).json()
    assert r["indstocks"]["valid"] is False and r["indstocks"]["source"] == "none"
    assert r["kite"] == {"configured": False, "connected": False, "login_url": None}


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


def test_kite_login_url_needs_the_public_base_url(client, monkeypatch):
    monkeypatch.setenv("SKOPAQ_KITE_API_KEY", "kite-key")
    monkeypatch.setenv("SKOPAQ_PUBLIC_BASE_URL", "https://api.example.com/")
    monkeypatch.setattr(dashboard_tools, "_kite_token", lambda: "")
    kite = client.get("/api/dashboard/broker", headers=AUTH).json()["kite"]
    assert kite == {"configured": True, "connected": False,
                    "login_url": "https://api.example.com/api/kite/login"}


def test_portfolio_without_brokers(client):
    r = client.get("/api/dashboard/portfolio", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["indstocks"]["available"] is False
    assert "INDstocks token" in body["indstocks"]["error"]
    assert body["kite"] == {"available": False, "error": "Kite is not configured"}


def test_portfolio_reads_kite(client, monkeypatch):
    from skopaq.broker.models import Funds, Holding, Position

    class FakeKite:
        async def get_positions(self):
            return [Position(symbol="TCS", quantity=2, average_price=100, last_price=110, pnl=20)]

        async def get_holdings(self):
            return [Holding(symbol="INFY", quantity=5, average_price=1500, last_price=1600,
                            pnl=500)]

        async def get_funds(self):
            return Funds(available_cash=1000, used_margin=50)

        async def get_order_book(self):
            raise RuntimeError("order book down")

    monkeypatch.setattr(dashboard_tools, "_kite_status",
                        lambda: {"configured": True, "connected": True, "login_url": None})
    monkeypatch.setattr(dashboard_tools, "_kite_client", lambda: FakeKite())
    kite = client.get("/api/dashboard/portfolio", headers=AUTH).json()["kite"]
    assert kite["available"] is True
    assert kite["positions"][0]["symbol"] == "TCS" and kite["positions"][0]["pnl"] == 20
    assert kite["holdings"][0]["quantity"] == 5
    assert kite["funds"]["available_cash"] == 1000
    assert kite["orders"] == [] and "order book down" in kite["errors"]["orders"]


def test_kite_endpoints_need_a_session(client):
    for path in ("/api/dashboard/kite/gtt", "/api/dashboard/kite/mutual-funds",
                 "/api/dashboard/options/chain?symbol=NIFTY",
                 "/api/dashboard/options/suggest?symbol=NIFTY"):
        r = client.get(path, headers=AUTH)
        assert r.status_code == 503 and "Kite" in r.json()["detail"], path


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
