"""Dashboard API (skopaq/api/dashboard.py): auth, overview, trades, kill switch, jobs."""

from __future__ import annotations

import time
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
        supabase_anon_key="", dashboard_users="",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.delenv("SKOPAQ_TRADING_HALTED", raising=False)
    monkeypatch.setattr(dashboard, "SkopaqConfig", lambda: _config())
    from skopaq.api import dashboard_auth

    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: _config())
    dashboard_auth.reset_state()
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
    from skopaq.api import dashboard_auth

    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: _config(api_token=SecretStr("")))
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
    assert r.json()["user"]["role"] == "admin" and r.json()["user"]["via"] == "api_token"


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


def test_scan_job_returns_the_swing_ideas(client, monkeypatch):
    monkeypatch.setattr(dashboard, "_swing_scan_job", lambda limit: {
        "model": "swing-lightgbm-1", "model_state": "shadow", "threshold": 0.55,
        "rules_only": False, "ideas": [{"symbol": "SBIN", "setup": "breakout", "limit": limit}]})
    r = client.post("/api/dashboard/jobs", json={"kind": "scan", "max_candidates": 3}, headers=AUTH)
    assert r.status_code == 202
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done"
    assert job["result"]["ideas"] == [{"symbol": "SBIN", "setup": "breakout", "limit": 3}]
    listed = client.get("/api/dashboard/jobs", headers=AUTH).json()["jobs"]
    assert listed[0]["id"] == job["id"] and "result" not in listed[0]


def test_a_failed_training_marks_the_job_failed(client, monkeypatch):
    def broken():
        raise ValueError("only 12 historical setups")

    monkeypatch.setattr(dashboard, "_swing_train_job", broken)
    r = client.post("/api/dashboard/jobs", json={"kind": "swing_train"}, headers=AUTH)
    job = _wait(client, r.json()["id"])
    assert job["status"] == "failed" and "12 historical setups" in job["error"]


@pytest.mark.parametrize("kind", ["analyze", "trade", "settle"])
def test_the_llm_jobs_are_gone(client, kind):
    r = client.post("/api/dashboard/jobs", json={"kind": kind, "symbol": "TCS"}, headers=AUTH)
    assert r.status_code == 422


@pytest.mark.parametrize("symbol", ["", "rel;rm -rf", "A" * 30])
def test_backtest_rejects_bad_symbols(client, symbol):
    r = client.post("/api/dashboard/jobs", json={"kind": "backtest", "symbol": symbol},
                    headers=AUTH)
    assert r.status_code == 422


def test_one_job_at_a_time(client):
    dashboard._jobs["busy"] = {"id": "busy", "status": "running", "created_at": time.time()}
    r = client.post("/api/dashboard/jobs", json={"kind": "scan"}, headers=AUTH)
    assert r.status_code == 409


def test_unknown_job(client):
    assert client.get("/api/dashboard/jobs/nope", headers=AUTH).status_code == 404


# ── Market data ───────────────────────────────────────────────────────────────


def _quote(sym, ltp, prev=None):
    return {
        "symbol": sym,
        "ltp": ltp,
        "prev_close": prev,
        "change_pct": ((ltp - prev) / prev) if prev else None,
    }


def test_overview_marks_positions_to_market(client, monkeypatch):
    from skopaq.broker import live_quotes

    repo = _Repo(
        [
            _trade(symbol="TCS", fill_price=Decimal("100")),
            _trade(symbol="NOPRICE", fill_price=Decimal("50")),
        ]
    )
    monkeypatch.setattr(dashboard, "_trade_repository", lambda config: repo)
    async def quotes(syms):
        return {"TCS": _quote("TCS", 110.0, 100.0)}, {"NOPRICE": "none"}

    monkeypatch.setattr(live_quotes, "get_quotes", quotes)
    data = client.get("/api/dashboard/overview", headers=AUTH).json()
    tcs, nop = (next(p for p in data["positions"] if p["symbol"] == s) for s in ("TCS", "NOPRICE"))
    assert tcs["ltp"] == 110.0 and tcs["unrealized_pnl"] == 20.0 and tcs["market_value"] == 220.0
    assert abs(tcs["unrealized_pct"] - 0.1) < 1e-9
    assert nop["ltp"] is None and nop["unrealized_pnl"] is None
    assert data["unrealized_pnl"] == 20.0 and data["price_errors"] == {"NOPRICE": "none"}


def test_market_quotes_validates_symbols(client, monkeypatch):
    from skopaq.broker import live_quotes

    async def quotes(syms):
        return {s: _quote(s, 1.0) for s in syms}, {}

    monkeypatch.setattr(live_quotes, "get_quotes", quotes)
    ok = client.get("/api/dashboard/market/quotes?symbols=TCS,^NSEI", headers=AUTH)
    assert ok.status_code == 200 and set(ok.json()["quotes"]) == {"TCS", "^NSEI"}
    bad = client.get("/api/dashboard/market/quotes?symbols=TCS,rm%20-rf;", headers=AUTH)
    assert bad.status_code == 422


def test_market_history(client, monkeypatch):
    from skopaq.broker import live_quotes

    async def history(s, r):
        return {"symbol": s, "range": r, "interval": "1day", "candles": [{"t": 1, "c": 5.0}],
                "source": "indstocks"}

    monkeypatch.setattr(live_quotes, "get_history", history)
    r = client.get("/api/dashboard/market/history?symbol=TCS&range=1mo", headers=AUTH)
    assert r.status_code == 200 and r.json()["candles"] == [{"t": 1, "c": 5.0}]
    assert r.json()["live"] is True and r.json()["ltp"] == 5.0
    assert (
        client.get("/api/dashboard/market/history?symbol=TCS&range=9y", headers=AUTH).status_code
        == 422
    )


def test_watchlist(client):
    symbols = client.get("/api/dashboard/market/watchlist", headers=AUTH).json()["symbols"]
    assert "RELIANCE" in symbols and symbols == sorted(symbols)


# ── P&L history ───────────────────────────────────────────────────────────────


def test_pnl_history_groups_by_ist_day(client, monkeypatch):
    rows = [
        _trade(closed_at=datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc), pnl=Decimal("10")),
        _trade(closed_at=datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc), pnl=Decimal("-4")),
        _trade(closed_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc), pnl=Decimal("6")),
    ]

    class Repo:
        def get_closed_since(self, since, is_paper):
            assert is_paper is True
            return rows

    monkeypatch.setattr(dashboard, "_trade_repository", lambda config: Repo())
    data = client.get("/api/dashboard/pnl-history?days=30", headers=AUTH).json()
    # 20:00 UTC on the 5th is the 6th in IST: it shares a day with the -4.
    assert data["points"] == [
        {"date": "2026-10-06", "pnl": 6.0, "cumulative": 6.0},
        {"date": "2026-10-07", "pnl": 6.0, "cumulative": 12.0},
    ]
    assert data["total"] == 12.0


# ── Scheduler ─────────────────────────────────────────────────────────────────


def test_scheduler_status_and_log(client, monkeypatch, tmp_path):
    from skopaq.execution import scheduler
    from skopaq.risk import calendar as nse_calendar

    state_dir, log_dir = tmp_path / "state", tmp_path / "logs"
    state_dir.mkdir()
    log_dir.mkdir()
    today = nse_calendar.now_ist().date().isoformat()
    (state_dir / f"daemon-{today}.started").write_text("2026-10-07T09:15:00+05:30")
    (state_dir / f"daemon-{today}.rc").write_text("0")
    (log_dir / f"daemon-{today}.log").write_text("".join(f"line {i}\n" for i in range(50)))
    monkeypatch.setattr(
        dashboard,
        "_schedule_settings",
        lambda: SimpleNamespace(enabled=True, mode="paper", state_dir=state_dir, log_dir=log_dir),
    )
    monkeypatch.setattr(scheduler, "describe", lambda settings, now, state: ["Mode: paper"])

    data = client.get("/api/dashboard/scheduler", headers=AUTH).json()
    assert data["ok"] and data["lines"] == ["Mode: paper"]
    started = {"started": "2026-10-07T09:15:00+05:30", "rc": 0}
    assert data["days"][0] == {"date": today, "has_log": True, "jobs": {"daemon": started}}

    log = client.get(f"/api/dashboard/scheduler/log?day={today}&lines=10", headers=AUTH).json()
    assert log["lines"] == [f"line {i}" for i in range(40, 50)]
    assert (
        client.get("/api/dashboard/scheduler/log?day=2020-01-01", headers=AUTH).status_code == 404
    )
    assert client.get("/api/dashboard/scheduler/log?day=../etc", headers=AUTH).status_code == 422


def test_scheduler_bad_config(client, monkeypatch):
    def broken():
        raise ValueError("SKOPAQ_SCHEDULER_START: bad")

    monkeypatch.setattr(dashboard, "_schedule_settings", broken)
    data = client.get("/api/dashboard/scheduler", headers=AUTH).json()
    assert data == {"ok": False, "error": "SKOPAQ_SCHEDULER_START: bad", "lines": [], "days": []}


def test_market_history_without_indstocks_is_502(client, monkeypatch):
    from skopaq.broker import live_quotes

    async def history(s, r):
        raise LookupError(live_quotes.NO_TOKEN)

    monkeypatch.setattr(live_quotes, "get_history", history)
    r = client.get("/api/dashboard/market/history?symbol=TCS&range=1d", headers=AUTH)
    assert r.status_code == 502 and "INDstocks token" in r.json()["detail"]
