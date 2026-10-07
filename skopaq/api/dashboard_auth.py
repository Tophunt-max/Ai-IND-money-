"""Who may use the web dashboard, and with which role.

Two ways in, both as ``Authorization: Bearer <token>``:

1. **Supabase Auth** (the dashboard's login screen: email + password or Google). The
   access token is checked with Supabase (``GET /auth/v1/user``), so revoked sessions
   ("log out of all devices") stop working within the cache time. The user's email must be
   **confirmed** and listed in ``SKOPAQ_DASHBOARD_USERS``::

       SKOPAQ_DASHBOARD_USERS=me@example.com:admin,friend@example.com:viewer

   An account that exists in Supabase but is not on the list gets 403.
2. ``SKOPAQ_API_TOKEN`` (scripts, the old login): an admin.

Roles: ``admin`` may do everything; ``viewer`` may only read (no analyses, trades, scans,
chat or kill switch). Repeated failed tokens from one IP are refused for a while (429).
New Supabase sessions are recorded in the ``dashboard_logins`` table
(``supabase/migrations/004_dashboard_logins.sql``) for the login history.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Optional

import httpx
from fastapi import Depends, Header, HTTPException, Request

from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)

ROLES = ("admin", "viewer")
_VERIFY_TTL = 60.0          # seconds a checked token is trusted before asking Supabase again
_FAIL_WINDOW = 600.0        # failed-token window per IP ...
_FAIL_LIMIT = 20            # ... and how many failures it allows before 429
_LOCK_SECONDS = 600.0

_verified: dict[str, tuple[float, dict[str, Any]]] = {}
_failures: dict[str, deque] = {}
_locked_until: dict[str, float] = {}
_seen_sessions: set[str] = set()
_lock = threading.Lock()


@dataclass
class DashboardUser:
    email: str
    role: str
    via: str                      # "supabase" or "api_token"
    user_id: Optional[str] = None
    provider: Optional[str] = None
    name: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("session_id", None)
        return d


def parse_users(raw: str) -> dict[str, str]:
    """``a@x.com:admin, b@y.com:viewer`` → {email: role}. No role, or an unknown one: viewer."""
    users: dict[str, str] = {}
    for item in (raw or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        email, _, role = item.partition(":")
        email, role = email.strip().lower(), role.strip().lower()
        if "@" not in email:
            continue
        users[email] = role if role in ROLES else "viewer"
    return users


def _supabase_auth_ready(config) -> bool:
    return bool(getattr(config, "supabase_url", "") and getattr(config, "supabase_anon_key", "")
                and parse_users(getattr(config, "dashboard_users", "")))


def auth_configured(config) -> bool:
    token = getattr(config, "api_token", None)
    return bool((token and token.get_secret_value()) or _supabase_auth_ready(config))


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


# ── Rate limit on failed tokens ───────────────────────────────────────────────


def _check_locked(ip: str) -> None:
    with _lock:
        until = _locked_until.get(ip, 0.0)
    if until > time.monotonic():
        raise HTTPException(
            429, "Too many failed logins from this network: try again in 10 minutes")


def _note_failure(ip: str) -> None:
    now = time.monotonic()
    with _lock:
        q = _failures.setdefault(ip, deque())
        q.append(now)
        while q and now - q[0] > _FAIL_WINDOW:
            q.popleft()
        if len(q) >= _FAIL_LIMIT:
            _locked_until[ip] = now + _LOCK_SECONDS
            q.clear()
            logger.warning("Dashboard: %s locked out after %d failed tokens", ip, _FAIL_LIMIT)


def _note_success(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)


# ── Supabase token check ──────────────────────────────────────────────────────


def _jwt_claims(token: str) -> dict[str, Any]:
    """Payload of a JWT *already verified by Supabase* (no signature check here)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


async def _supabase_user(token: str, config) -> Optional[dict[str, Any]]:
    """The Supabase user for an access token, or None if Supabase rejects it."""
    now = time.monotonic()
    with _lock:
        hit = _verified.get(token)
        if hit and now - hit[0] < _VERIFY_TTL:
            return hit[1]
    url = config.supabase_url.rstrip("/") + "/auth/v1/user"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url, headers={"apikey": config.supabase_anon_key,
                                                 "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Login service (Supabase) not reachable: {exc}") from exc
    if res.status_code != 200:
        return None
    user = res.json()
    with _lock:
        _verified[token] = (now, user)
        if len(_verified) > 1000:
            for k in sorted(_verified, key=lambda k: _verified[k][0])[:200]:
                _verified.pop(k, None)
    return user


def _record_login(config, user: DashboardUser, status: str, request: Request) -> None:
    """Insert one row per new session into dashboard_logins (best effort, never raises)."""
    if not user.session_id or not config.supabase_service_key.get_secret_value():
        return
    key = f"{user.session_id}:{status}"
    with _lock:
        if key in _seen_sessions:
            return
        _seen_sessions.add(key)
    row = {
        "session_id": user.session_id, "user_id": user.user_id, "email": user.email,
        "role": user.role if status == "ok" else None, "status": status,
        "provider": user.provider, "ip": client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "")[:300],
    }

    def write():
        try:
            from supabase import create_client

            client = create_client(config.supabase_url,
                                   config.supabase_service_key.get_secret_value())
            client.table("dashboard_logins").upsert(
                row, on_conflict="session_id,status", ignore_duplicates=True).execute()
        except Exception:
            logger.warning("Could not record the dashboard login (run migration 004?)",
                           exc_info=True)

    threading.Thread(target=write, daemon=True).start()


# ── FastAPI dependencies ──────────────────────────────────────────────────────


async def current_user(request: Request, authorization: str = Header(default="")) -> DashboardUser:
    config = SkopaqConfig()
    if not auth_configured(config):
        raise HTTPException(
            503, "Dashboard login is not set up: set SKOPAQ_DASHBOARD_USERS (and the Supabase "
                 "URL and anon key) on the server")
    ip = client_ip(request)
    _check_locked(ip)
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Not logged in", headers={"WWW-Authenticate": "Bearer"})

    api_token = config.api_token.get_secret_value()
    if api_token and hmac.compare_digest(token.encode(), api_token.encode()):
        _note_success(ip)
        return DashboardUser(email="api-token", role="admin", via="api_token")

    if not _supabase_auth_ready(config):
        _note_failure(ip)
        raise HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})

    info = await _supabase_user(token, config)
    if info is None:
        _note_failure(ip)
        raise HTTPException(401, "Session expired or invalid: log in again",
                            headers={"WWW-Authenticate": "Bearer"})
    _note_success(ip)
    email = (info.get("email") or "").lower()
    claims = _jwt_claims(token)
    meta = info.get("user_metadata") or {}
    user = DashboardUser(
        email=email, role="viewer", via="supabase", user_id=info.get("id"),
        provider=(info.get("app_metadata") or {}).get("provider"),
        name=meta.get("full_name") or meta.get("name"),
        session_id=claims.get("session_id"),
    )
    role = parse_users(config.dashboard_users).get(email)
    if not role or not info.get("email_confirmed_at"):
        _record_login(config, user, "denied", request)
        why = "this email is not confirmed" if role else "this account is not allowed"
        raise HTTPException(403, f"Access denied: {why}. Ask the admin to add it.")
    user.role = role
    _record_login(config, user, "ok", request)
    return user


async def require_admin(user: DashboardUser = Depends(current_user)) -> DashboardUser:
    if not user.is_admin:
        raise HTTPException(403, "View-only account: this needs an admin")
    return user


def reset_state() -> None:
    """Forget caches, lockouts and seen sessions (tests)."""
    with _lock:
        _verified.clear()
        _failures.clear()
        _locked_until.clear()
        _seen_sessions.clear()


__all__ = ["DashboardUser", "current_user", "require_admin", "parse_users", "auth_configured",
           "client_ip", "reset_state"]
