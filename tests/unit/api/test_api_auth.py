"""The open health route, the removed Kite routes and CORS origin parsing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from skopaq.api.auth import cors_origins


@pytest.fixture
def client():
    """The real API app."""
    from skopaq.api.server import app

    return TestClient(app)


def test_kite_routes_are_gone(client):
    """INDstocks is the only broker: the Kite OAuth/token/postback routes no longer exist."""
    for method, path in (("get", "/api/kite/login"), ("get", "/api/kite/callback"),
                         ("get", "/api/kite/status"), ("get", "/api/kite/token"),
                         ("post", "/api/kite/postback")):
        assert getattr(client, method)(path).status_code == 404, path


def test_health_stays_open(client):
    assert client.get("/health").status_code == 200


def test_cors_origins():
    def cfg(value):
        return SimpleNamespace(cors_origins=value)

    assert cors_origins(cfg("*")) == ["*"]
    assert cors_origins(cfg("https://a, https://b")) == ["https://a", "https://b"]
    assert cors_origins(cfg("")) == []
