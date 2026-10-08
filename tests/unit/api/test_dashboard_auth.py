"""Dashboard login (skopaq/api/dashboard_auth.py): Supabase sessions, allow-list, roles."""

from __future__ import annotations

import base64
import json
import sys
import types
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.api import dashboard, dashboard_auth
from skopaq.api.dashboard_auth import parse_users

SUPA = "https://x.supabase.co"
USERS = "boss@example.com:admin, Viewer@Example.com:viewer, odd@example.com:king"


def _jwt(session="s-1"):
    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'session_id': session})}.sig"


def _config(**over):
    base = dict(api_token=SecretStr(""), trading_mode="paper", initial_paper_capital=1e6,
                supabase_url=SUPA, supabase_anon_key="anon", supabase_service_key=SecretStr("svc"),
                dashboard_users=USERS)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def env(monkeypatch, tmp_path):
    kite = types.ModuleType("skopaq.broker.kite_client")
    kite.get_access_token = lambda: "kite-tok"
    monkeypatch.setitem(sys.modules, "skopaq.broker.kite_client", kite)
    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    cfg = {"value": _config()}
    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: cfg["value"])
    monkeypatch.setattr(dashboard, "SkopaqConfig", lambda: cfg["value"])
    recorded = []
    monkeypatch.setattr(dashboard_auth, "_record_login",
                        lambda config, user, status, request: recorded.append((user.email, status)))
    dashboard_auth.reset_state()
    dashboard._jobs.clear()
    from skopaq.api.server import app

    with TestClient(app) as c:
        yield SimpleNamespace(client=c, cfg=cfg, recorded=recorded)
    dashboard_auth.reset_state()


def _user(email, confirmed=True, provider="email"):
    return {"id": "u-1", "email": email, "email_confirmed_at": "2026-10-01T00:00:00Z" if confirmed
            else None, "app_metadata": {"provider": provider}, "user_metadata": {"full_name": "B"}}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_parse_users():
    assert parse_users(USERS) == {"boss@example.com": "admin", "viewer@example.com": "viewer",
                                  "odd@example.com": "viewer"}
    assert parse_users("") == {} and parse_users("nope, :admin") == {}


def test_503_when_no_login_is_configured(env):
    env.cfg["value"] = _config(dashboard_users="")
    assert env.client.get("/api/dashboard/me", headers=_h("x")).status_code == 503


def test_401_without_a_token(env):
    assert env.client.get("/api/dashboard/me").status_code == 401


@respx.mock
def test_admin_login_and_cache(env):
    route = respx.get(f"{SUPA}/auth/v1/user").mock(
        return_value=httpx.Response(200, json=_user("Boss@example.com", provider="google")))
    tok = _jwt()
    r = env.client.get("/api/dashboard/me", headers=_h(tok))
    assert r.status_code == 200
    u = r.json()["user"]
    assert u == {"email": "boss@example.com", "role": "admin", "via": "supabase", "user_id": "u-1",
                 "provider": "google", "name": "B"}
    assert route.calls[0].request.headers["apikey"] == "anon"
    env.client.get("/api/dashboard/kill-switch", headers=_h(tok))
    assert route.call_count == 1                       # second request served from the cache
    assert env.recorded == [("boss@example.com", "ok"), ("boss@example.com", "ok")]


@respx.mock
def test_account_not_on_the_list_is_denied(env):
    respx.get(f"{SUPA}/auth/v1/user").mock(return_value=httpx.Response(200, json=_user("x@y.com")))
    r = env.client.get("/api/dashboard/me", headers=_h(_jwt()))
    assert r.status_code == 403 and "not allowed" in r.json()["detail"]
    assert env.recorded == [("x@y.com", "denied")]


@respx.mock
def test_unconfirmed_email_is_denied(env):
    respx.get(f"{SUPA}/auth/v1/user").mock(
        return_value=httpx.Response(200, json=_user("boss@example.com", confirmed=False)))
    r = env.client.get("/api/dashboard/me", headers=_h(_jwt()))
    assert r.status_code == 403 and "not confirmed" in r.json()["detail"]


@respx.mock
def test_viewer_can_read_but_not_act(env):
    respx.get(f"{SUPA}/auth/v1/user").mock(
        return_value=httpx.Response(200, json=_user("viewer@example.com")))
    h = _h(_jwt())
    assert env.client.get("/api/dashboard/me", headers=h).json()["user"]["role"] == "viewer"
    assert env.client.get("/api/dashboard/kill-switch", headers=h).status_code == 200
    assert env.client.get("/api/dashboard/market/watchlist", headers=h).status_code == 200
    for path, body in [("/api/dashboard/jobs", {"kind": "scan"}),
                       ("/api/dashboard/kill-switch/halt", {"reason": "x"}),
                       ("/api/dashboard/kill-switch/resume", None),
                       ("/api/dashboard/chat", {"message": "hi"})]:
        r = env.client.post(path, json=body, headers=h)
        assert r.status_code == 403, path
        assert "View-only" in r.json()["detail"]
    assert env.client.get("/api/dashboard/auth/logins?scope=all", headers=h).status_code == 403


@respx.mock
def test_admin_halt_is_tagged_with_the_email(env, monkeypatch):
    respx.get(f"{SUPA}/auth/v1/user").mock(
        return_value=httpx.Response(200, json=_user("boss@example.com")))
    from skopaq.execution import kill_switch

    seen = {}
    monkeypatch.setattr(kill_switch, "halt", lambda reason, by="": seen.update(by=by) or ["file"])
    r = env.client.post("/api/dashboard/kill-switch/halt", json={"reason": "t"},
                        headers=_h(_jwt()))
    assert r.status_code == 200 and seen["by"] == "dashboard:boss@example.com"


@respx.mock
def test_rejected_token_and_lockout(env):
    respx.get(f"{SUPA}/auth/v1/user").mock(return_value=httpx.Response(401, json={}))
    for i in range(dashboard_auth._FAIL_LIMIT):
        r = env.client.get("/api/dashboard/me", headers=_h(f"bad-{i}"))
        assert r.status_code == 401
    r = env.client.get("/api/dashboard/me", headers=_h("bad-last"))
    assert r.status_code == 429


def test_api_token_still_works_as_admin(env):
    env.cfg["value"] = _config(api_token=SecretStr("tok"))
    r = env.client.get("/api/dashboard/me", headers=_h("tok"))
    assert r.json()["user"]["role"] == "admin"


@respx.mock
def test_supabase_down_is_503(env):
    respx.get(f"{SUPA}/auth/v1/user").mock(side_effect=httpx.ConnectError("down"))
    assert env.client.get("/api/dashboard/me", headers=_h(_jwt())).status_code == 503


@respx.mock
def test_chat_for_admin(env, monkeypatch):
    respx.get(f"{SUPA}/auth/v1/user").mock(
        return_value=httpx.Response(200, json=_user("boss@example.com")))
    from skopaq.chat import bridge

    async def fake_send(body):
        return bridge.ChatMessageResponse(message=f"echo {body.message}", session_id="c1")

    monkeypatch.setattr(bridge, "send_message", fake_send)
    r = env.client.post("/api/dashboard/chat", json={"message": "hi"}, headers=_h(_jwt()))
    assert r.status_code == 200 and r.json()["message"] == "echo hi"


def test_record_login_writes_one_row_per_session(monkeypatch):
    import threading

    from skopaq.api.dashboard_auth import DashboardUser, _record_login

    dashboard_auth.reset_state()
    rows, done = [], threading.Event()

    class Table:
        def upsert(self, row, **kw):
            rows.append((row, kw))
            return self

        def execute(self):
            done.set()

    fake = types.ModuleType("supabase")
    fake.create_client = lambda url, key: SimpleNamespace(table=lambda name: Table())
    monkeypatch.setitem(sys.modules, "supabase", fake)
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1", "user-agent": "UA"},
                          client=None)
    user = DashboardUser(email="boss@example.com", role="admin", via="supabase", user_id="u",
                         provider="google", session_id="s-9")
    _record_login(_config(), user, "ok", req)
    _record_login(_config(), user, "ok", req)          # same session: not written again
    assert done.wait(5)
    assert len(rows) == 1
    row, kw = rows[0]
    assert row["ip"] == "1.2.3.4" and row["status"] == "ok" and row["session_id"] == "s-9"
    assert kw == {"on_conflict": "session_id,status", "ignore_duplicates": True}
    dashboard_auth.reset_state()
