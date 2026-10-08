"""Dashboard Environment endpoints (/api/dashboard/settings/env): admin only, validation,
the live-trading confirmation, secrets hidden, Telegram notice."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq import env_overrides

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PATH = "/api/dashboard/settings/env"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(env_overrides.FILE_ENV, str(tmp_path / "env_overrides.json"))
    monkeypatch.setenv("SKOPAQ_TRADING_MODE", "paper")
    for key in ("SKOPAQ_SCHEDULER_MODE", "SKOPAQ_SCHEDULER_CONFIRM_LIVE",
                "SKOPAQ_SCANNER_MAX_CANDIDATES", "SKOPAQ_GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    from skopaq.api import dashboard_auth

    # Auth by the API token only (admin); the endpoints build the real SkopaqConfig.
    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: SimpleNamespace(
        api_token=SecretStr(TOKEN), supabase_url="", supabase_anon_key="", dashboard_users=""))
    dashboard_auth.reset_state()
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    import skopaq.notifications

    monkeypatch.setattr(skopaq.notifications, "notify", notify)
    env_overrides.reset_for_tests()
    from skopaq.api.server import app

    with TestClient(app) as c:
        c.sent = sent
        yield c
    env_overrides.apply({})
    env_overrides.reset_for_tests()


def test_needs_a_login(client):
    assert client.get(PATH).status_code == 401
    r = client.post(PATH, json={"set": {"SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}})
    assert r.status_code == 401
    assert not os.path.exists(os.environ[env_overrides.FILE_ENV])


def test_lists_settings_without_secret_values(client):
    os.environ["SKOPAQ_GOOGLE_API_KEY"] = "AIza-secret"
    r = client.get(PATH, headers=AUTH)
    assert r.status_code == 200
    assert "AIza-secret" not in r.text
    items = {s["key"]: s for s in r.json()["settings"]}
    assert items["SKOPAQ_GOOGLE_API_KEY"]["is_set"] is True
    assert items["SKOPAQ_GOOGLE_API_KEY"]["value"] is None
    assert items["SKOPAQ_TRADING_MODE"]["value"] == "paper"
    assert items["SKOPAQ_DASHBOARD_USERS"]["locked"]


def test_saves_and_notifies(client):
    r = client.post(PATH, headers=AUTH, json={"set": {"SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] == ["SKOPAQ_SCANNER_MAX_CANDIDATES"] and body["live"] == []
    item = {s["key"]: s for s in body["settings"]}["SKOPAQ_SCANNER_MAX_CANDIDATES"]
    assert item["value"] == "7" and item["source"] == "dashboard"
    assert body["history"][0]["by"] == "dashboard:api-token"
    assert len(client.sent) == 1 and "SKOPAQ_SCANNER_MAX_CANDIDATES" in client.sent[0]
    assert "LIVE" not in client.sent[0]


@pytest.mark.parametrize("change", [
    {"set": {"SKOPAQ_API_TOKEN": "x"}},
    {"set": {"SKOPAQ_SCANNER_MAX_CANDIDATES": "lots"}},
    {"set": {"NOT_OURS": "1"}},
    {},
])
def test_bad_changes_are_422(client, change):
    r = client.post(PATH, headers=AUTH, json=change)
    assert r.status_code == 422, r.text
    assert client.sent == []


def test_live_needs_confirm_live(client):
    live = {"set": {"SKOPAQ_TRADING_MODE": "live"}}
    r = client.post(PATH, headers=AUTH, json=live)
    assert r.status_code == 409 and "SKOPAQ_TRADING_MODE=live" in r.json()["detail"]
    assert client.get("/api/dashboard/me", headers=AUTH).json()["mode"] == "paper"
    assert client.sent == []

    r = client.post(PATH, headers=AUTH, json={**live, "confirm_live": True})
    assert r.status_code == 200 and r.json()["live"] == ["SKOPAQ_TRADING_MODE=live"]
    assert client.get("/api/dashboard/me", headers=AUTH).json()["mode"] == "live"
    assert client.sent[0].startswith("🔴 LIVE TRADING")


def test_reset(client):
    client.post(PATH, headers=AUTH, json={"set": {"SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}})
    r = client.post(PATH, headers=AUTH, json={"remove": ["SKOPAQ_SCANNER_MAX_CANDIDATES"]})
    assert r.status_code == 200 and r.json()["removed"] == ["SKOPAQ_SCANNER_MAX_CANDIDATES"]
    item = {s["key"]: s for s in r.json()["settings"]}["SKOPAQ_SCANNER_MAX_CANDIDATES"]
    assert item["source"] == "default" and item["value"] == "5"
