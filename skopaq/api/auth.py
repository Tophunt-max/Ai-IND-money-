"""API access control: the chat bearer token, the CORS origin list, and startup checks.

The endpoints that execute tools (``/api/chat/*``) need ``Authorization: Bearer
<SKOPAQ_API_TOKEN>``. With no token configured they are refused (503): they used to be
open, and they can run ``trade_stock``.

``startup_problems`` lists configurations the API refuses to start with in live mode
(no dashboard auth, wildcard CORS); ``docs_enabled`` decides whether ``/docs`` is served.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from skopaq.config import SkopaqConfig


def require_api_token(authorization: str = Header(default="")) -> None:
    """FastAPI dependency: 401 unless the request carries the configured bearer token."""
    expected = SkopaqConfig().api_token.get_secret_value()
    if not expected:
        # Fail closed: the chat endpoints run tools (trade_stock among them)
        raise HTTPException(
            503, "The chat API is disabled: set SKOPAQ_API_TOKEN on the server to use it")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        value.strip().encode(), expected.encode()
    ):
        raise HTTPException(
            401, "Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"}
        )


def cors_origins(config) -> list[str]:
    """``SKOPAQ_CORS_ORIGINS`` as a list; ``""`` allows no browser origin."""
    return [o.strip() for o in config.cors_origins.split(",") if o.strip()]


def docs_enabled(config) -> bool:
    """``SKOPAQ_API_DOCS_ENABLED`` when set; otherwise on in paper mode, off in live."""
    value = getattr(config, "api_docs_enabled", None)
    if isinstance(value, bool):
        return value
    return getattr(config, "trading_mode", "paper") != "live"


def startup_problems(config) -> list[str]:
    """Why the API must not start as configured (live mode only; empty when fine)."""
    if getattr(config, "trading_mode", "paper") != "live":
        return []
    from skopaq.api.dashboard_auth import auth_configured

    problems = []
    if not auth_configured(config):
        problems.append("live mode needs dashboard auth: SKOPAQ_API_TOKEN, or Supabase "
                        "auth with SKOPAQ_DASHBOARD_USERS")
    if "*" in cors_origins(config):
        problems.append("live mode needs an explicit SKOPAQ_CORS_ORIGINS list, not '*'")
    return problems
