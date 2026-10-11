"""API hardening: authenticated status/scanner/portfolio routes, docs and startup checks
for live mode, the trusted client address, duplicate-submission protection, and the chat
bridge's refusal of order-placing tools in live mode."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.api import auth, dashboard_auth
from skopaq.api.auth import docs_enabled, startup_problems
from skopaq.api.dashboard_auth import client_ip
from skopaq.api.idempotency import IdempotencyStore


def _cfg(**over):
    base = dict(api_token=SecretStr(""), supabase_url="", supabase_anon_key="",
                dashboard_users="", trading_mode="paper", cors_origins="*",
                api_docs_enabled=None)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch):
    from skopaq.api.server import app

    dashboard_auth.reset_state()
    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: _cfg())
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/status", "/api/portfolio", "/api/scanner/status",
                                  "/api/scanner/candidates"])
def test_account_routes_need_a_login(client, monkeypatch, path):
    assert client.get(path).status_code == 503                 # no auth configured: closed
    monkeypatch.setattr(dashboard_auth, "SkopaqConfig",
                        lambda: _cfg(api_token=SecretStr("tok")))
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_health_stays_open_without_account_data(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "positions" not in body


def test_docs_and_startup_checks_follow_the_mode():
    assert docs_enabled(_cfg()) is True
    assert docs_enabled(_cfg(trading_mode="live")) is False
    assert docs_enabled(_cfg(trading_mode="live", api_docs_enabled=True)) is True
    assert startup_problems(_cfg()) == []
    problems = " ".join(startup_problems(_cfg(trading_mode="live")))
    assert "dashboard auth" in problems and "CORS" in problems
    assert startup_problems(_cfg(trading_mode="live", api_token=SecretStr("t"),
                                 cors_origins="https://dash.example.com")) == []


def test_client_ip_trusts_only_the_configured_proxy_hops():
    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 1.2.3.4"},
                          client=SimpleNamespace(host="10.0.0.9"))
    assert client_ip(req) == "1.2.3.4"            # the proxy's entry, not the client's claim
    assert client_ip(req, 2) == "6.6.6.6"
    assert client_ip(req, 0) == "10.0.0.9"
    assert client_ip(SimpleNamespace(headers={}, client=SimpleNamespace(host="h")), 1) == "h"


async def test_idempotency_replays_and_refuses_duplicates():
    store = IdempotencyStore()
    calls = []

    async def place(key, body):
        async with store.guard("a@x", "order", body, key) as slot:
            if slot.replay is not None:
                return slot.replay
            calls.append(body)
            slot.result = {"ok": True, "n": len(calls)}
            return slot.result

    first = await place("k1", {"q": 1})
    assert await place("k1", {"q": 1}) == first and len(calls) == 1      # replayed
    await place(None, {"q": 2})
    with pytest.raises(HTTPException) as e:
        await place(None, {"q": 2})                                        # double click
    assert e.value.status_code == 409 and len(calls) == 2

    async def failing():
        async with store.guard("a@x", "order", {"q": 3}, None):
            raise HTTPException(409, "needs confirmation")
    with pytest.raises(HTTPException):
        await failing()
    await place(None, {"q": 3})                   # a refused request may be sent again
    assert len(calls) == 3


def _live(monkeypatch, live: bool):
    monkeypatch.setattr("skopaq.chat.bridge._live_mode", lambda: live)


@pytest.fixture
def bridge(monkeypatch):
    from fastapi import FastAPI

    from skopaq.chat.bridge import _sessions, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[auth.require_api_token] = lambda: None
    _sessions.clear()
    yield TestClient(app)
    _sessions.clear()


def test_chat_tool_refuses_trade_stock_in_live_mode(bridge, monkeypatch):
    _live(monkeypatch, True)
    session = MagicMock(id="s1")
    monkeypatch.setattr("skopaq.chat.bridge._get_or_create_session", lambda sid: session)
    tool = MagicMock()
    tool.name = "trade_stock"
    tool.ainvoke = AsyncMock(return_value="TRADED")
    monkeypatch.setattr("skopaq.chat.tools.get_all_tools", lambda: [tool])
    monkeypatch.setattr("skopaq.chat.tools.init_tools", lambda infra: None)
    r = bridge.post("/api/chat/tool", json={"tool": "trade_stock", "args": {"symbol": "TCS"}})
    assert r.status_code == 403 and tool.ainvoke.await_count == 0
    _live(monkeypatch, False)
    r = bridge.post("/api/chat/tool", json={"tool": "trade_stock", "args": {"symbol": "TCS"}})
    assert r.status_code == 200 and r.json()["result"] == "TRADED"        # paper: as before


def test_chat_message_does_not_auto_approve_trades_in_live_mode(bridge, monkeypatch):
    _live(monkeypatch, True)
    session = MagicMock(id="s1")
    session.get_history.return_value = []
    pending = SimpleNamespace(next=("tools",), values={"messages": [
        SimpleNamespace(tool_calls=[{"name": "trade_stock", "args": {"symbol": "TCS"}}])]})
    agent = MagicMock()
    agent.ainvoke = AsyncMock(return_value={"messages": []})
    agent.get_state.return_value = pending
    session.ensure_agent.return_value = agent
    monkeypatch.setattr("skopaq.chat.bridge._get_or_create_session", lambda sid: session)
    r = bridge.post("/api/chat/message", json={"message": "buy TCS"})
    assert r.status_code == 200 and "Not run: trade_stock" in r.json()["message"]
    assert agent.ainvoke.await_count == 1          # the first turn only: never resumed


def test_chat_errors_do_not_leak_exception_text(bridge, monkeypatch):
    def boom(sid):
        raise RuntimeError("postgres://user:secret@host failed")
    monkeypatch.setattr("skopaq.chat.bridge._get_or_create_session", boom)
    bridge = TestClient(bridge.app, raise_server_exceptions=False)
    r = bridge.post("/api/chat/tool", json={"tool": "get_quote", "args": {}})
    assert r.status_code == 500 and "secret" not in r.text
    r = bridge.post("/api/chat/message", json={"message": "x", "session_id": "bad id!"})
    assert r.status_code in (422, 500) and "secret" not in r.text
