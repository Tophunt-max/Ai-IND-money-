"""Dashboard API (skopaq/api/dashboard.py): auth, overview, trades, kill switch, jobs."""

from __future__ import annotations

import sys
import time
import types
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.api import dashboard
from skopaq.db.models import TradeRecord

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _config(**over):
    base = dict(
        api_token=SecretStr(TOKEN), trading_mode="paper", initial_paper_capital=1_000_000.0,
        supabase_url="https://x.supabase.co", supabase_service_key=SecretStr("svc"),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch, tmp_path):
    kite = types.ModuleType("skopaq.broker.kite_client")
    kite.get_access_token = lambda: "kite-tok"
    monkeypatch.setitem(sys.modules, "skopaq.broker.kite_client", kite)
    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.delenv("SKOPAQ_TRADING_HALTED", raising=False)
    monkeypatch.setattr(dashboard, "SkopaqConfig", lambda: _config())
    from skopaq.execution import kill_switch

    # Kill switch: file only (no Supabase, no env var), no cache between calls.
    monkeypatch.setattr(kill_switch, "_config", lambda: SimpleNamespace(
        trading_halted=False, supabase_url="", supabase_service_key=SecretStr("")))
    monkeypatch.setattr(kill_switch, "_cache", None)
    dashboard._jobs.clear()
    from skopaq.api.server import app

    # As a context manager: one event loop for the whole test, so job tasks keep running.
    with TestClient(app) as c:
        yield c


def _trade(**kw):
    base = dict(symbol="TCS", side="BUY", quantity=Decimal("2"), price=Decimal("100"),
                status="COMPLETE", is_paper=True, created_at=datetime.now(timezone.utc))
    base.update(kw)
    return TradeRecord(**base)


class _Repo:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_recent(self, limit=50, is_paper=None):
        self.calls.append((limit, is_paper))
        return [r for r in self.rows if is_paper is None or r.is_paper == is_paper][:limit]


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_disabled_without_a_configured_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "SkopaqConfig", lambda: _config(api_token=SecretStr("")))
    r = client.get("/api/dashboard/me", headers=AUTH)
    assert r.status_code == 503


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}],
)
def test_rejects_missing_or_wrong_token(client, headers):
    assert client.get("/api/dashboard/me", headers=headers).status_code == 401


def test_me_with_the_token(client):
    r = client.get("/api/dashboard/me", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "mode": "paper"}


# ── Overview and trades ───────────────────────────────────────────────────────


def test_overview_positions_and_realized_pnl(client, monkeypatch):
    rows = [
        _trade(symbol="TCS", fill_price=Decimal("101"), agent_decision={"stop_loss": 95}),
        _trade(symbol="INFY", closed_at=datetime.now(timezone.utc), pnl=Decimal("50.5")),
        _trade(symbol="SBIN", side="SELL", pnl=Decimal("50.5")),
        _trade(symbol="LIVE", is_paper=False),
    ]
    repo = _Repo(rows)
    monkeypatch.setattr(dashboard, "_trade_repository", lambda config: repo)
    data = client.get("/api/dashboard/overview", headers=AUTH).json()
    assert repo.calls == [(500, True)]
    assert [p["symbol"] for p in data["positions"]] == ["TCS"]
    assert data["positions"][0]["entry_price"] == 101.0
    assert data["positions"][0]["cost"] == 202.0
    assert data["positions"][0]["stop_loss"] == 95
    assert data["realized_pnl"] == 50.5 and data["closed_trades"] == 1
    assert data["kill_switch"]["halted"] is False
    assert data["database"] == "ok"


def test_overview_survives_a_missing_database(client, monkeypatch):
    monkeypatch.setattr(dashboard, "SkopaqConfig", lambda: _config(supabase_url=""))
    data = client.get("/api/dashboard/overview", headers=AUTH).json()
    assert data["positions"] == [] and "not configured" in data["database"]


def test_trades_filter(client, monkeypatch):
    repo = _Repo([_trade(), _trade(symbol="LIVE", is_paper=False)])
    monkeypatch.setattr(dashboard, "_trade_repository", lambda config: repo)
    paper = client.get("/api/dashboard/trades", headers=AUTH).json()["trades"]
    assert [t["symbol"] for t in paper] == ["TCS"]
    every = client.get("/api/dashboard/trades?mode=all&limit=10", headers=AUTH).json()["trades"]
    assert len(every) == 2 and repo.calls[-1] == (10, None)


# ── Kill switch ───────────────────────────────────────────────────────────────


def test_halt_and_resume(client, tmp_path):
    r = client.post("/api/dashboard/kill-switch/halt", json={"reason": "test"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["halted"] is True
    assert (tmp_path / "HALT").exists()
    assert client.get("/api/dashboard/kill-switch", headers=AUTH).json()["reason"] == "test"
    r = client.post("/api/dashboard/kill-switch/resume", headers=AUTH)
    assert r.json()["halted"] is False and not (tmp_path / "HALT").exists()


def test_kill_switch_needs_the_token(client):
    assert client.post("/api/dashboard/kill-switch/halt", json={}).status_code == 401


# ── Jobs ──────────────────────────────────────────────────────────────────────


def _wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/dashboard/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_analyze_job(client, monkeypatch):
    from skopaq.cli import main as cli

    async def fake_analyze(symbol, date):
        signal = SimpleNamespace(action="BUY", confidence=72, entry_price=100.0,
                                 stop_loss=95.0, target=110.0, reasoning="why")
        return SimpleNamespace(symbol=symbol, trade_date=date, error=None, duration_seconds=3.2,
                               signal=signal, raw_decision="BUY",
                               agent_state={"market_report": "up", "n": 3,
                                            "investment_debate_state": {"judge_decision": "go"}})

    monkeypatch.setattr(cli, "_run_analyze", fake_analyze)
    r = client.post("/api/dashboard/jobs", json={"kind": "analyze", "symbol": "tcs"}, headers=AUTH)
    assert r.status_code == 202
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done" and job["symbol"] == "TCS"
    assert job["result"]["signal"]["action"] == "BUY"
    assert job["result"]["reports"] == {"market_report": "up",
                                        "investment_debate_state.judge_decision": "go"}
    listed = client.get("/api/dashboard/jobs", headers=AUTH).json()["jobs"]
    assert listed[0]["id"] == job["id"] and "result" not in listed[0]


def test_analyze_error_marks_the_job_failed(client, monkeypatch):
    from skopaq.cli import main as cli

    async def broken(symbol, date):
        return SimpleNamespace(symbol=symbol, trade_date=date, error="API key not valid",
                               duration_seconds=1, signal=None, raw_decision="", agent_state={})

    monkeypatch.setattr(cli, "_run_analyze", broken)
    r = client.post("/api/dashboard/jobs", json={"kind": "analyze", "symbol": "TCS"}, headers=AUTH)
    job = _wait(client, r.json()["id"])
    assert job["status"] == "failed" and job["error"] == "API key not valid"


def test_scan_job(client, monkeypatch):
    from skopaq.cli import main as cli

    async def fake_scan(n):
        return [SimpleNamespace(to_dict=lambda: {"symbol": "SBIN", "reason": "volume"})]

    monkeypatch.setattr(cli, "_run_scan", fake_scan)
    r = client.post("/api/dashboard/jobs", json={"kind": "scan", "max_candidates": 3}, headers=AUTH)
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done"
    assert job["result"] == {"candidates": [{"symbol": "SBIN", "reason": "volume"}]}


@pytest.mark.parametrize("symbol", ["", "rel;rm -rf", "A" * 30])
def test_analyze_rejects_bad_symbols(client, symbol):
    r = client.post("/api/dashboard/jobs", json={"kind": "analyze", "symbol": symbol}, headers=AUTH)
    assert r.status_code == 422


def test_one_job_at_a_time(client):
    dashboard._jobs["busy"] = {"id": "busy", "status": "running", "created_at": time.time()}
    r = client.post("/api/dashboard/jobs", json={"kind": "scan"}, headers=AUTH)
    assert r.status_code == 409


def test_unknown_job(client):
    assert client.get("/api/dashboard/jobs/nope", headers=AUTH).status_code == 404
