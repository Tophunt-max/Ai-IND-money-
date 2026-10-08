"""Optional bearer-token guard (SKOPAQ_API_TOKEN) and CORS origin parsing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.api import auth
from skopaq.api.auth import cors_origins


@pytest.fixture
def client():
    """The real API app."""
    from skopaq.api.server import app

    return TestClient(app)


def _token(monkeypatch, value: str) -> None:
    # Patch the config read so a developer .env cannot leak in.
    monkeypatch.setattr(auth, "SkopaqConfig", lambda: SimpleNamespace(api_token=SecretStr(value)))


def test_kite_routes_are_gone(client):
    """INDstocks is the only broker: the Kite OAuth/token/postback routes no longer exist."""
    for method, path in (("get", "/api/kite/login"), ("get", "/api/kite/callback"),
                         ("get", "/api/kite/status"), ("get", "/api/kite/token"),
                         ("post", "/api/kite/postback")):
        assert getattr(client, method)(path).status_code == 404, path


def test_chat_tool_endpoint_is_open_without_a_configured_token(client, monkeypatch):
    _token(monkeypatch, "")

    def teapot(_session_id):
        raise HTTPException(418, "reached the endpoint")

    monkeypatch.setattr("skopaq.chat.bridge._get_or_create_session", teapot)
    body = {"tool": "get_quote", "args": {"symbol": "TCS"}}
    assert client.post("/api/chat/tool", json=body).status_code == 418


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic s3cret"},
     {"Authorization": "s3cret"}],
)
def test_chat_tool_needs_the_bearer_token(client, monkeypatch, headers):
    _token(monkeypatch, "s3cret")
    body = {"tool": "get_quote", "args": {"symbol": "TCS"}}
    response = client.post("/api/chat/tool", json=body, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_chat_tool_endpoint_is_guarded(client, monkeypatch):
    _token(monkeypatch, "s3cret")
    body = {"tool": "get_quote", "args": {"symbol": "TCS"}}
    assert client.post("/api/chat/tool", json=body).status_code == 401
    assert client.post(
        "/api/chat/tool", json=body, headers={"Authorization": "Bearer wrong"}
    ).status_code == 401

    def teapot(_session_id):
        raise HTTPException(418, "reached the endpoint")

    monkeypatch.setattr("skopaq.chat.bridge._get_or_create_session", teapot)
    response = client.post("/api/chat/tool", json=body,
                           headers={"Authorization": "bearer s3cret"})  # scheme is case-insensitive
    assert response.status_code == 418


def test_health_stays_open(client, monkeypatch):
    _token(monkeypatch, "s3cret")
    assert client.get("/health").status_code == 200


def test_cors_origins():
    def cfg(value):
        return SimpleNamespace(cors_origins=value)

    assert cors_origins(cfg("*")) == ["*"]
    assert cors_origins(cfg("https://a, https://b")) == ["https://a", "https://b"]
    assert cors_origins(cfg("")) == []
