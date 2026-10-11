"""API access control: the CORS origin list and startup checks.

``startup_problems`` lists configurations the API refuses to start with in live mode
(no dashboard auth, wildcard CORS); ``docs_enabled`` decides whether ``/docs`` is served.
``SKOPAQ_API_TOKEN`` is checked by ``skopaq.api.dashboard_auth`` (a bearer token that acts
as an admin, for machine access without Supabase).
"""

from __future__ import annotations


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
