"""/health is polled every 30 s by the compose health check: it must not send Telegram."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from fastapi.testclient import TestClient

from skopaq.broker import token_manager
from skopaq.broker.token_manager import TokenManager


def test_health_polls_near_expiry_send_no_token_warning(tmp_path, monkeypatch):
    token_dir = tmp_path / ".skopaq"
    monkeypatch.setattr(token_manager, "_NOTIFIED", set())
    sent = []

    def fake_notify(msg):
        sent.append(msg)
        return asyncio.sleep(0)

    monkeypatch.setattr("skopaq.notifications.notify", fake_notify)
    with (
        patch("skopaq.broker.token_manager.TOKEN_DIR", token_dir),
        patch("skopaq.broker.token_manager.TOKEN_FILE", token_dir / "token.enc"),
        patch("skopaq.broker.token_manager.KEY_FILE", token_dir / "token.key"),
    ):
        TokenManager().set_token("expiring-soon", ttl_hours=1.5)  # inside the 2 h warning window
        from skopaq.api.dashboard_auth import DashboardUser, current_user
        from skopaq.api.server import app

        # /api/status needs a dashboard login now (tests/unit/api/test_api_security.py)
        app.dependency_overrides[current_user] = lambda: DashboardUser(
            email="viewer@example.com", role="viewer", via="test")
        client = TestClient(app)
        for _ in range(4):
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["token_valid"] is True
        try:
            status = client.get("/api/status").json()
        finally:
            app.dependency_overrides.pop(current_user, None)
        assert status["broker"]["token_warning"]  # still shown to the dashboard

    assert sent == []
