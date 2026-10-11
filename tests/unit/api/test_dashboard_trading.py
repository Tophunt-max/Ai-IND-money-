"""Dashboard trading endpoints (skopaq/api/dashboard_trading.py): signals, risk,
reconciliation, chart analysis and the model registry, with viewer/admin boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.api import dashboard_auth
from skopaq.api.dashboard_auth import DashboardUser

TOKEN = "admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKOPAQ_SIGNAL_JOURNAL_DIR", str(tmp_path / "signals"))
    monkeypatch.setenv("SKOPAQ_ORDER_JOURNAL_DIR", str(tmp_path / "orders"))
    monkeypatch.setenv("SKOPAQ_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setenv("SKOPAQ_TRADING_MODE", "paper")
    monkeypatch.setenv("SKOPAQ_SUPABASE_URL", "")
    monkeypatch.delenv("SKOPAQ_TRADING_HALTED", raising=False)

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr("skopaq.notifications.notify", nothing)
    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: SimpleNamespace(
        api_token=SecretStr(TOKEN), supabase_url="", supabase_anon_key="", dashboard_users=""))
    dashboard_auth.reset_state()
    from skopaq.api.server import app

    with TestClient(app) as c:
        c.tmp = tmp_path
        yield c
    app.dependency_overrides.clear()


def _as_viewer():
    from skopaq.api.server import app

    app.dependency_overrides[dashboard_auth.current_user] = lambda: DashboardUser(
        email="v@example.com", role="viewer", via="test")


def test_reads_need_a_login_and_actions_need_an_admin(client):
    for path in ("/api/dashboard/signals", "/api/dashboard/risk",
                 "/api/dashboard/control/reconcile", "/api/dashboard/learning/models"):
        assert client.get(path).status_code == 401, path
    _as_viewer()
    assert client.get("/api/dashboard/signals").status_code == 200
    assert client.post("/api/dashboard/learning/rollback",
                       json={"reason": "testing it"}).status_code == 403
    assert client.post("/api/dashboard/control/reconcile/abcdefgh12/resolve",
                       json={"note": "checked the book"}).status_code == 403


def test_signals_show_decisions_with_reasons(client):
    from skopaq.strategy.journal import SignalJournal
    from skopaq.strategy.signal import SignalCandidate, qualify

    j = SignalJournal(client.tmp / "signals")
    now = datetime.now(IST)
    c = SignalCandidate(strategy_id="scalp.orb", strategy_version="1", symbol="TCS",
                        product="equity_intraday", direction=1, decided_at=now,
                        timeframe_s=60, entry=100, stop=101, target=103, qty=10)
    j.record_decision(c, qualify(c, now=now))
    body = client.get("/api/dashboard/signals", headers=AUTH).json()
    assert body["counts"] == {"REJECTED": 1}
    assert body["signals"][0]["reasons"]
    assert client.get("/api/dashboard/signals?status=QUALIFIED",
                      headers=AUTH).json()["signals"] == []


def test_risk_shows_ceilings_and_the_charge_schedule(client):
    body = client.get("/api/dashboard/risk", headers=AUTH).json()
    assert body["ceilings"]["max_daily_loss_pct"] == 0.03
    assert body["charges"]["schedule"] == "nse-indstocks-2024-10"
    assert body["realized_source"] == "unavailable"


def test_reconcile_lists_and_resolves_blockers(client):
    from skopaq.execution.order_journal import OrderJournal

    j = OrderJournal(client.tmp / "orders")
    j.record("submitting", internal_id="deadbeef0001", symbol="TCS", side="BUY",
             qty=Decimal(5))
    import json
    path = j.path_for(j._wall().date())
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    lines[0]["pid"] = -1                                   # another (dead) process
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    body = client.get("/api/dashboard/control/reconcile", headers=AUTH).json()
    assert body["blocking_entries"] and body["blockers"][0]["internal_id"] == "deadbeef0001"
    r = client.post("/api/dashboard/control/reconcile/deadbeef0001/resolve", headers=AUTH,
                    json={"note": "not in the order book"})
    assert r.status_code == 200 and r.json()["blockers"] == []
    assert client.post("/api/dashboard/control/reconcile/deadbeef0001/resolve", headers=AUTH,
                       json={"note": "again please"}).status_code == 404


def test_market_analysis_returns_overlays_and_labels_the_source(client, monkeypatch):
    t0 = int(datetime(2026, 7, 1, tzinfo=IST).timestamp())
    candles = [{"t": t0 + 86400 * i, "o": 100 + i, "h": 101.5 + i, "l": 99 + i,
                "c": 101 + i, "v": 1000} for i in range(80)]

    async def history(symbol, range_):
        return {"symbol": symbol, "candles": candles, "interval": "1d", "source": "yahoo"}

    monkeypatch.setattr("skopaq.broker.live_quotes.get_history", history)
    body = client.get("/api/dashboard/market/analysis?symbol=TCS", headers=AUTH).json()
    assert body["source"] == "delayed" and body["bars"] == 80
    assert body["overlays"][-1]["ema21"] and body["overlays"][0]["ema21"] is None
    assert body["regime"]["trend"] == "trending_up"
    assert body["data_quality"]["ok"]


def test_model_registry_endpoints(client):
    from skopaq.learning.registry import ModelRegistry

    reg = ModelRegistry(client.tmp / "models")
    entry = reg.register(model={"kind": "x"}, report={"samples": 5}, params={}, dataset={},
                         strategy_id="scalp.orb")
    body = client.get("/api/dashboard/learning/models", headers=AUTH).json()
    assert body["champion"] is None and body["versions"][0]["state"] == "candidate"
    r = client.post(f"/api/dashboard/learning/models/{entry['version']}/promote",
                    headers=AUTH, json={"reason": "looks good"})
    assert r.status_code == 409 and "shadow" in r.json()["detail"]
    r = client.post("/api/dashboard/learning/rollback", headers=AUTH,
                    json={"reason": "nothing to roll back"})
    assert r.status_code == 200 and r.json()["champion"] is None


def test_lifecycle_contract_note_regimes_and_drift_endpoints(client, monkeypatch):
    from skopaq.execution.order_journal import OrderJournal
    from skopaq.strategy.selector import RegimeMapStore

    monkeypatch.setenv("SKOPAQ_CHARGES_DIR", str(client.tmp / "charges"))
    j = OrderJournal(client.tmp / "orders")
    j.record("submitting", internal_id="abcdef123456", symbol="TCS", side="BUY",
             qty=Decimal(1))
    j.record("placed", order_id="EQ-1", internal_id="abcdef123456", symbol="TCS",
             side="BUY", qty=Decimal(1), status="QUEUED")
    body = client.get("/api/dashboard/control/lifecycle", headers=AUTH).json()
    assert body["orders"][0]["state"] == "ACKNOWLEDGED" and body["open"] == 1

    note = ("Trade Date,Order No,Scrip Name,Buy/Sell,Quantity,Rate,Brokerage,STT,Total\n"
            "08-10-2026,EQ-1,TCS,Buy,1,4000,10,0,12\n")
    _as_viewer()
    assert client.post("/api/dashboard/finance/contract-note",
                       json={"csv": note}).status_code == 403
    from skopaq.api.server import app
    app.dependency_overrides.clear()
    r = client.post("/api/dashboard/finance/contract-note", headers=AUTH, json={"csv": note})
    assert r.status_code == 200 and r.json()["rows"] == 1
    assert client.get("/api/dashboard/finance/reconciliation",
                      headers=AUTH).json()["reports"]
    assert client.post("/api/dashboard/finance/contract-note", headers=AUTH,
                       json={"csv": "a,b\n1,2\n"}).status_code == 422

    assert client.post("/api/dashboard/learning/regimes/approve", headers=AUTH,
                       json={"reason": "nothing yet"}).status_code == 409
    RegimeMapStore(client.tmp / "models").save_candidate({"regimes": {}}, dataset={})
    r = client.post("/api/dashboard/learning/regimes/approve", headers=AUTH,
                    json={"reason": "validated map"})
    assert r.status_code == 200
    assert client.get("/api/dashboard/learning/regimes", headers=AUTH).json()["approved"]
    assert client.get("/api/dashboard/learning/drift",
                      headers=AUTH).json()["available"] is False


def test_adaptive_status_and_parameter_approval(client):
    from skopaq.adaptive.hyperopt import ParamsStore

    body = client.get("/api/dashboard/learning/adaptive", headers=AUTH).json()
    assert body["enabled"] is False and body["versions"] == []
    assert body["config"]["model_kind"] == "lightgbm"
    store = ParamsStore(client.tmp / "models")
    store.save_candidate({"best_params": {"rr": 2.0}, "better_on_holdout": False,
                          "holdout_current": {"net": 1, "trades": 5},
                          "holdout_best": {"net": 0, "trades": 5}})
    r = client.post("/api/dashboard/learning/params/approve", headers=AUTH,
                    json={"reason": "try it out"})
    assert r.status_code == 409                       # not better on the holdout
    store.save_candidate({"best_params": {"rr": 2.0}, "better_on_holdout": True,
                          "holdout_current": {"net": 1, "trades": 5},
                          "holdout_best": {"net": 9, "trades": 5}})
    r = client.post("/api/dashboard/learning/params/approve", headers=AUTH,
                    json={"reason": "beat the holdout"})
    assert r.status_code == 200 and store.params() == {"rr": 2.0}
    assert client.post("/api/dashboard/learning/params/rollback", headers=AUTH,
                       json={"reason": "roll it back"}).status_code == 200
    assert store.params() is None


def test_rl_status_and_promotion_need_an_admin_and_the_shadow_days(client):
    from skopaq.adaptive.rl_agents import rl_registry

    body = client.get("/api/dashboard/learning/rl", headers=AUTH).json()
    assert body["enabled"] is False and body["allow_live"] is False
    assert body["algos"] == ["ppo", "a2c", "dqn"] and body["live_policy"] is None
    reg = rl_registry(client.tmp / "models")
    entry = reg.register(model={"kind": "rl-policy-v1", "bundle": "b", "path": "/nowhere"},
                         report={"algo": "ppo", "model": {"net": 5.0}}, params={},
                         dataset={}, strategy_id="rl.ppo")
    v = entry["version"]
    entry.update(state="shadow", shadow_since=datetime.now(timezone.utc).isoformat())
    reg._write_json(reg._path(v), entry)
    reg._set_pointer("shadow", v)
    body = client.get("/api/dashboard/learning/rl", headers=AUTH).json()
    assert body["shadow"] == v and body["paper_policy"] == v and body["live_policy"] is None
    assert body["versions"][0]["state"] == "shadow"
    _as_viewer()
    assert client.post(f"/api/dashboard/learning/rl/{v}/promote",
                       json={"reason": "looks good"}).status_code == 403
    from skopaq.api.server import app
    app.dependency_overrides.clear()
    r = client.post(f"/api/dashboard/learning/rl/{v}/promote", headers=AUTH,
                    json={"reason": "looks good"})
    assert r.status_code == 409 and "days of paper" in r.json()["detail"]
    assert client.post("/api/dashboard/learning/rl/nope-x/promote", headers=AUTH,
                       json={"reason": "looks good"}).status_code == 404
    assert client.post("/api/dashboard/learning/rl/rollback", headers=AUTH,
                       json={"reason": "nothing to roll"}).json()["champion"] is None
