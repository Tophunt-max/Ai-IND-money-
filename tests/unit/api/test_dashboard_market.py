"""Dashboard market extras (skopaq/api/dashboard_market.py) and the alert store
(skopaq/alerts.py): price alerts fire once, the notification feed, the live price stream,
the NIFTY 50 heatmap and the symbol search."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq import alerts as alerts_mod
from skopaq.alerts import AlertStore
from skopaq.api import dashboard_market

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def q(ltp, pct=None):
    return {"ltp": ltp, "change_pct": pct, "change": None, "volume": 1000, "source": "yahoo"}


# ── the store ────────────────────────────────────────────────────────────────


def test_alerts_fire_once_and_land_in_the_feed(tmp_path):
    store = AlertStore(tmp_path / "alerts.json")
    up = store.add("tcs", "above", 4000, note="breakout")
    down = store.add("INFY", "below", 1500)
    pct = store.add("SBIN", "pct_down", 2)
    assert up.symbol == "TCS" and store.active_symbols() == {"TCS", "INFY", "SBIN"}

    assert store.check({"TCS": q(3999.9), "INFY": q(1500.1), "SBIN": q(800, -0.019)}) == []
    fired = store.check({"TCS": q(4000.0), "INFY": q(1499), "SBIN": q(790, -0.021)})
    assert {a.id for a in fired} == {up.id, down.id, pct.id}
    assert all(not a.active and a.triggered_price for a in store.alerts())
    assert store.check({"TCS": q(4100)}) == []                     # once only
    feed = store.feed()
    assert len(feed) == 3 and all(n.kind == "alert" and not n.read for n in feed)
    assert any("breakout" in n.body for n in feed)

    assert store.mark_read([feed[0].id]) == 1 and store.mark_read() == 2
    assert store.rearm(up.id).active and store.check({"TCS": q(4001)})[0].id == up.id
    assert store.delete(down.id) and not store.delete(down.id)


def test_alert_values_are_validated(tmp_path):
    store = AlertStore(tmp_path / "a.json")
    for sym, op, v in (("", "above", 1), ("TCS", "cross", 1), ("TCS", "above", 0),
                       ("TCS", "pct_up", 60), ("BAD SYMBOL!", "above", 1)):
        with pytest.raises(ValueError):
            store.add(sym, op, v)
    assert store.alerts() == []


def test_a_missing_or_corrupt_file_reads_as_empty(tmp_path):
    p = tmp_path / "alerts.json"
    assert AlertStore(p).alerts() == []
    p.write_text("{not json")
    assert AlertStore(p).alerts() == [] and AlertStore(p).check({"TCS": q(1)}) == []


def test_two_checkers_at_once_fire_an_alert_only_once(tmp_path):
    path = tmp_path / "alerts.json"
    AlertStore(path).add("TCS", "above", 100)
    results: list[int] = []

    def run():
        results.append(len(AlertStore(path).check({"TCS": q(150)})))

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [0, 0, 0, 0, 0, 1]


async def test_check_now_quotes_active_symbols_and_notifies(tmp_path, monkeypatch):
    store = AlertStore(tmp_path / "a.json")
    store.add("TCS", "above", 100)
    seen, sent = [], []

    async def get_quotes(symbols):
        seen.append(symbols)
        return {"TCS": q(120)}, {}

    async def notify(text):
        sent.append(text)

    from skopaq.broker import live_quotes
    monkeypatch.setattr(live_quotes, "get_quotes", get_quotes)
    monkeypatch.setattr("skopaq.notifications.notify", notify)
    fired = await alerts_mod.check_now(store)
    assert seen == [["TCS"]] and len(fired) == 1 and "TCS" in sent[0]
    assert await alerts_mod.check_now(store) == [] and len(seen) == 1   # nothing active


# ── the API ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKOPAQ_ALERTS_FILE", str(tmp_path / "alerts.json"))
    from skopaq.api import dashboard_auth

    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: SimpleNamespace(
        api_token=SecretStr(TOKEN), supabase_url="", supabase_anon_key="", dashboard_users=""))
    dashboard_auth.reset_state()

    async def get_quotes(symbols):
        prices = {"TCS": q(4000, 0.012), "INFY": q(1500, -0.02), "RELIANCE": q(2900, 0.005)}
        return {s: {**prices[s], "symbol": s} for s in symbols if s in prices}, {}

    from skopaq.broker import live_quotes
    monkeypatch.setattr(live_quotes, "get_quotes", get_quotes)

    async def no_notify(fired):
        return None

    monkeypatch.setattr(alerts_mod, "notify_fired", no_notify)
    from skopaq.api.server import app

    with TestClient(app) as c:
        yield c


def test_market_extras_need_a_login(client):
    for path in ("/api/dashboard/alerts", "/api/dashboard/notifications",
                 "/api/dashboard/market/heatmap", "/api/dashboard/market/search?q=TC"):
        assert client.get(path).status_code == 401, path


def test_alert_crud_over_the_api(client):
    r = client.post("/api/dashboard/alerts", headers=AUTH,
                    json={"symbol": "tcs", "op": "above", "value": 3900})
    assert r.status_code == 200 and r.json()["alert"]["symbol"] == "TCS"
    aid = r.json()["alert"]["id"]
    assert "TCS at or above" in client.get("/api/dashboard/alerts", headers=AUTH).json()["alerts"][0]["text"]
    bad = client.post("/api/dashboard/alerts", headers=AUTH, json={"symbol": "TCS", "op": "above", "value": -1})
    assert bad.status_code == 422
    assert client.post(f"/api/dashboard/alerts/{aid}/rearm", headers=AUTH).status_code == 200
    assert client.delete(f"/api/dashboard/alerts/{aid}", headers=AUTH).status_code == 200
    assert client.delete(f"/api/dashboard/alerts/{aid}", headers=AUTH).status_code == 404


def test_the_stream_sends_quotes_and_fires_alerts(client, monkeypatch):
    client.post("/api/dashboard/alerts", headers=AUTH, json={"symbol": "TCS", "op": "above", "value": 3900})
    monkeypatch.setattr(dashboard_market, "STREAM_MAX_S", 0.01)
    monkeypatch.setattr(dashboard_market, "STREAM_OPEN_S", 0.0)
    monkeypatch.setattr(dashboard_market, "STREAM_CLOSED_S", 0.0)
    with client.stream("GET", "/api/dashboard/market/stream?symbols=TCS,INFY,bad sym", headers=AUTH) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    frame = next(line for line in body.splitlines() if line.startswith("data:"))
    data = json.loads(frame[5:])
    assert set(data["quotes"]) == {"TCS", "INFY"}
    assert [a["symbol"] for a in data["alerts"]] == ["TCS"]
    feed = client.get("/api/dashboard/notifications", headers=AUTH).json()
    assert feed["unread"] == 1 and feed["items"][0]["symbol"] == "TCS"
    assert client.post("/api/dashboard/notifications/read", headers=AUTH, json={}).json()["marked"] == 1
    assert client.get("/api/dashboard/market/stream?symbols=,,", headers=AUTH).status_code == 422


def test_heatmap_groups_nifty_50_by_sector(client):
    data = client.get("/api/dashboard/market/heatmap", headers=AUTH).json()
    assert len(data["stocks"]) >= 50 and data["advances"] == 2 and data["declines"] == 1
    by = {s["symbol"]: s for s in data["stocks"]}
    assert by["TCS"]["sector"] == "IT" and by["INFY"]["change_pct"] == -0.02
    assert by["LT"]["sector"] != "OTHER"
    assert data["sectors"][0]["change_pct"] >= data["sectors"][-1]["change_pct"]


def test_search_ranks_prefix_matches_and_lists_indices(client):
    res = client.get("/api/dashboard/market/search?q=tc", headers=AUTH).json()["results"]
    assert res[0]["symbol"] == "TCS"
    assert all("TC" in r["symbol"] for r in res)
    empty = client.get("/api/dashboard/market/search", headers=AUTH).json()["results"]
    assert any(r["kind"] == "index" for r in empty) and any(r["kind"] == "stock" for r in empty)


def test_swing_idea_for_one_symbol(client, monkeypatch):
    from skopaq.swing import engine as eng
    from skopaq.swing.engine import SwingIdea
    from skopaq.swing.setups import Setup

    class FakeModel:
        version, threshold = "swing-x", 0.6

    class FakeEngine:
        model, model_state, rules_only = FakeModel(), "shadow", False

        async def idea(self, symbol):
            if symbol != "TCS":
                return None
            return SwingIdea("TCS", Setup("breakout", 100.0, 96.0, 108.0, "20-day high"), 0.7, 0.7, {})

    monkeypatch.setattr(eng.SwingEngine, "from_config", classmethod(lambda cls, c, paper: FakeEngine()))
    d = client.get("/api/dashboard/swing/idea?symbol=tcs", headers=AUTH).json()
    assert d["idea"]["setup"] == "breakout" and d["qualifies"] and d["model"] == "swing-x"
    none = client.get("/api/dashboard/swing/idea?symbol=INFY", headers=AUTH).json()
    assert none["idea"] is None and not none["qualifies"]
    assert client.get("/api/dashboard/swing/idea?symbol=bad sym!", headers=AUTH).status_code == 422
