"""Custom OpenAI-compatible LLM endpoint (SKOPAQ_CUSTOM_LLM_*): role order, judge model,
fallback when not configured, and the dashboard's connection check."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from skopaq.llm import model_tier

BASE = "https://gateway.example.com/v1"
KEYS = ("SKOPAQ_CUSTOM_LLM_BASE_URL", "SKOPAQ_CUSTOM_LLM_API_KEY", "SKOPAQ_CUSTOM_LLM_MODEL",
        "SKOPAQ_CUSTOM_LLM_JUDGE_MODEL")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env
    for k in KEYS + ("ANTHROPIC_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY",
                     "PERPLEXITY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    return monkeypatch


def _configure(env, judge=""):
    env.setenv("SKOPAQ_CUSTOM_LLM_BASE_URL", BASE + "/")
    env.setenv("SKOPAQ_CUSTOM_LLM_API_KEY", "cc-secret")
    env.setenv("SKOPAQ_CUSTOM_LLM_MODEL", "fast-model")
    if judge:
        env.setenv("SKOPAQ_CUSTOM_LLM_JUDGE_MODEL", judge)


def _fake_create(provider, model, **kw):
    llm = MagicMock()
    llm._provider, llm._model = provider, model
    return llm


def test_not_configured_keeps_gemini(env):
    env.setenv("SKOPAQ_CUSTOM_LLM_BASE_URL", BASE)  # no key / model: ignored
    assert model_tier.custom_endpoint() is None
    env.setattr(model_tier, "_create_llm", _fake_create)
    llm_map = model_tier.build_llm_map()
    assert llm_map["market_analyst"]._provider == "google"
    assert llm_map["_default"]._provider == "google"


def test_custom_endpoint_goes_first_for_every_role(env):
    _configure(env, judge="smart-model")
    env.setattr(model_tier, "_create_llm", _fake_create)
    llm_map = model_tier.build_llm_map()
    for role in ("market_analyst", "social_analyst", "news_analyst", "trader",
                 "bull_researcher", "sell_analyst"):
        assert (llm_map[role]._provider, llm_map[role]._model) == ("custom", "fast-model"), role
    for role in ("research_manager", "portfolio_manager", "chat_brain"):
        assert (llm_map[role]._provider, llm_map[role]._model) == ("custom", "smart-model"), role
    assert llm_map["_default"]._model == "fast-model"
    assert model_tier.custom_endpoint()["base_url"] == BASE  # trailing slash dropped


def test_judges_use_the_main_model_without_a_judge_model(env):
    _configure(env)
    env.setattr(model_tier, "_create_llm", _fake_create)
    assert model_tier.build_llm_map()["portfolio_manager"]._model == "fast-model"


def test_a_broken_custom_client_falls_back(env):
    _configure(env)

    def create(provider, model, **kw):
        if provider == "custom":
            raise RuntimeError("bad endpoint")
        return _fake_create(provider, model)

    env.setattr(model_tier, "_create_llm", create)
    assert model_tier.build_llm_map()["market_analyst"]._provider == "google"


def test_custom_client_uses_the_openai_compatible_provider(env):
    _configure(env)
    seen = {}

    def fake_client(provider, model, base_url=None, **kw):
        seen.update(provider=provider, model=model, base_url=base_url, **kw)
        return SimpleNamespace(get_llm=lambda: "llm")

    import tradingagents.llm_clients as clients

    env.setattr(clients, "create_llm_client", fake_client)
    assert model_tier._create_llm("custom", "fast-model") == "llm"
    assert seen == {"provider": "openai_compatible", "model": "fast-model",
                    "base_url": BASE, "api_key": "cc-secret"}


# ── Dashboard check ───────────────────────────────────────────────────────────


@pytest.fixture
def client(env):
    from skopaq.api import dashboard_auth

    env.setattr(dashboard_auth, "SkopaqConfig", lambda: SimpleNamespace(
        api_token=SecretStr("tok"), supabase_url="", supabase_anon_key="", dashboard_users=""))
    dashboard_auth.reset_state()
    from skopaq.api.server import app

    with TestClient(app) as c:
        yield c


H = {"Authorization": "Bearer tok"}


def test_check_needs_the_settings(client):
    r = client.post("/api/dashboard/llm/check", headers=H, json={})
    assert r.status_code == 422 and "SKOPAQ_CUSTOM_LLM_BASE_URL" in r.json()["detail"]


@respx.mock
def test_check_lists_models_and_tests_a_prompt(client, env):
    _configure(env)
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "fast-model"}, {"id": "smart-model"}]}))

    class LLM:
        async def ainvoke(self, prompt):
            return SimpleNamespace(content=" OK ")

    env.setattr(model_tier, "_create_llm", lambda p, m: LLM())
    body = client.post("/api/dashboard/llm/check", headers=H, json={}).json()
    assert body["ok"] is True and body["reply"] == "OK", body
    assert body["models"] == ["fast-model", "smart-model"], body["models_error"]
    assert "cc-secret" not in str(body)


@respx.mock
def test_check_reports_errors_without_the_key(client, env):
    _configure(env)
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(401, text="bad key"))

    class LLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("401 invalid api key cc-secret")

    env.setattr(model_tier, "_create_llm", lambda p, m: LLM())
    body = client.post("/api/dashboard/llm/check", headers=H, json={}).json()
    assert body["ok"] is False and "401" in body["error"] and "cc-secret" not in body["error"]
    assert body["models"] is None and body["models_error"].startswith("HTTP 401")
