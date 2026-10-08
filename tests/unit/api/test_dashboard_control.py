"""Dashboard control endpoints (skopaq/api/dashboard_control.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.execution.control import ControlChannel

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKOPAQ_CONTROL_DIR", str(tmp_path / "control"))
    monkeypatch.setenv("SKOPAQ_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setenv("SKOPAQ_ENV_OVERRIDES_FILE", str(tmp_path / "overrides.json"))
    monkeypatch.setenv("SKOPAQ_SCHEDULER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SKOPAQ_TRADING_MODE", "paper")
    monkeypatch.setenv("SKOPAQ_SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("SKOPAQ_TRADING_HALTED", raising=False)
    monkeypatch.setattr("skopaq.notifications.notify", _no_notify)
    from skopaq.api import dashboard_auth
    from skopaq.execution import kill_switch

    monkeypatch.setattr(dashboard_auth, "SkopaqConfig", lambda: SimpleNamespace(
        api_token=SecretStr(TOKEN), supabase_url="", supabase_anon_key="", dashboard_users=""))
    dashboard_auth.reset_state()
    monkeypatch.setattr(kill_switch, "_config", lambda: SimpleNamespace(
        trading_halted=False, supabase_url="", supabase_service_key=SecretStr("")))
    monkeypatch.setattr(kill_switch, "_cache", None)
    from skopaq.api.server import app

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, channel=ControlChannel(tmp_path / "control"),
                              tmp=tmp_path)


async def _no_notify(text):
    return None


def test_everything_needs_a_login(env):
    assert env.client.get("/api/dashboard/control").status_code == 401
    assert env.client.post("/api/dashboard/control/pause", json={}).status_code == 401


def test_status_without_a_session(env):
    r = env.client.get("/api/dashboard/control", headers=AUTH).json()
    assert r["mode"] == "paper" and r["active"] is False and r["session"] is None
    assert r["scheduler"]["ok"] and r["scheduler"]["enabled"] is True
    assert r["halt"]["halted"] is False


def test_pause_and_resume(env):
    r = env.client.post("/api/dashboard/control/pause", headers=AUTH,
                        json={"reason": "results day"}).json()
    assert r["halt"]["halted"] and r["halt"]["reason"] == "results day"
    r = env.client.post("/api/dashboard/control/resume", headers=AUTH).json()
    assert r["halt"]["halted"] is False


def test_auto_sessions_off_then_start_is_refused(env):
    r = env.client.post("/api/dashboard/control/auto", headers=AUTH, json={"enabled": False})
    assert r.status_code == 200 and r.json()["scheduler"]["enabled"] is False
    r = env.client.post("/api/dashboard/control/start", headers=AUTH, json={})
    assert r.status_code == 409 and "Auto sessions are off" in r.json()["detail"]


def test_start_writes_a_request_and_stop_needs_a_running_session(env):
    r = env.client.post("/api/dashboard/control/start", headers=AUTH, json={"job": "daemon"})
    assert r.status_code == 200 and env.channel.start_requested()["job"] == "daemon"
    r = env.client.post("/api/dashboard/control/stop", headers=AUTH, json={})
    assert r.status_code == 409

    env.channel.write_status("session", {"phase": "MONITORING"})
    assert env.client.get("/api/dashboard/control", headers=AUTH).json()["active"] is True
    assert env.client.post("/api/dashboard/control/start", headers=AUTH,
                           json={}).status_code == 409
    r = env.client.post("/api/dashboard/control/stop", headers=AUTH, json={"reason": "x"})
    assert r.status_code == 200 and env.channel.stop_requested(0)["reason"] == "x"


def test_close_goes_to_the_running_monitor(env, monkeypatch):
    env.channel.write_status("monitor", {"positions": []})
    real_submit = ControlChannel.submit

    def submit_and_answer(self, kind, payload, by):
        cmd_id = real_submit(self, kind, payload, by)
        self.complete(cmd_id, ok=True, message=f"{kind} {payload.get('symbol')}")
        return cmd_id

    monkeypatch.setattr(ControlChannel, "submit", submit_and_answer)
    r = env.client.post("/api/dashboard/control/close", headers=AUTH, json={"symbol": "tcs"})
    assert r.status_code == 200
    assert r.json() == {"via": "session", **r.json(), "ok": True, "message": "close TCS"}


def test_paper_actions_without_a_session_are_refused(env):
    for path, body in (("/api/dashboard/control/close", {}),
                       ("/api/dashboard/control/orders",
                        {"symbol": "TCS", "side": "BUY", "quantity": 1})):
        r = env.client.post(path, headers=AUTH, json=body)
        assert r.status_code == 409 and "session" in r.json()["detail"], path


def test_live_orders_need_confirmation_and_symbols_are_validated(env, monkeypatch):
    monkeypatch.setenv("SKOPAQ_TRADING_MODE", "live")
    r = env.client.post("/api/dashboard/control/orders", headers=AUTH,
                        json={"symbol": "TCS", "side": "BUY", "quantity": 1})
    assert r.status_code == 409 and "real-money" in r.json()["detail"]
    r = env.client.post("/api/dashboard/control/close", headers=AUTH,
                        json={"symbol": "TCS; rm"})
    assert r.status_code == 422
    r = env.client.post("/api/dashboard/control/orders", headers=AUTH,
                        json={"symbol": "TCS", "side": "BUY", "quantity": 1,
                              "order_type": "LIMIT", "confirm_live": True})
    assert r.status_code == 422


def test_plan_change_without_a_monitor_edits_the_saved_plan(env, monkeypatch):
    from skopaq.config import SkopaqConfig
    from skopaq.execution.exit_plan import planner_from_config

    monkeypatch.setenv("SKOPAQ_EXIT_PLAN_DIR", str(env.tmp / "plans"))
    planner_from_config(SkopaqConfig()).plan_for_entry("TCS", "paper", 100.0, 10, stop=96.0)
    r = env.client.post("/api/dashboard/control/plan", headers=AUTH,
                        json={"symbol": "TCS", "stop_loss": 98, "target": 0})
    assert r.status_code == 200 and r.json()["via"] == "plan"
    plan = planner_from_config(SkopaqConfig()).get("paper", "TCS")
    assert (plan.stop_loss, plan.target) == (98.0, None)
    assert env.client.post("/api/dashboard/control/plan", headers=AUTH,
                           json={"symbol": "INFY", "stop_loss": 1}).status_code == 404


def test_the_stream_sends_status_events(env, monkeypatch):
    from skopaq.api import dashboard_control

    monkeypatch.setattr(dashboard_control, "_STREAM_MAX_S", 0.05)
    with env.client.stream("GET", "/api/dashboard/control/stream", headers=AUTH) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert body.startswith("retry: 3000") and '"mode": "paper"' in body
