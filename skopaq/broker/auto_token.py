"""The day's INDstocks access token from TOTP, without the dashboard.

INDstocks (``POST /generate/token``, https://api-docs.indstocks.com/Users/) issues a 24 h
access token for the account's Client ID (``x-api-key``), MPIN and the current TOTP code
of the authenticator set up on the Access Tokens page. With ``SKOPAQ_INDSTOCKS_CLIENT_ID``,
``SKOPAQ_INDSTOCKS_MPIN`` and ``SKOPAQ_INDSTOCKS_TOTP_SECRET`` set, the scheduler makes the
day's token before the session (and the daemon's PRE_OPEN when it still has none), and
stores it with ``TokenManager`` on the shared home volume, where every container reads it.

The broker's rules shape this module:

- **One live token.** Each generation invalidates the previous TOTP token, so a token is
  made only when the stored one cannot carry the session (``ensure_token``), by one
  process at a time (a file lock), and never twice within 65 s.
- **Lockouts.** Five wrong codes in 15 minutes lock generation for 15 minutes. After two
  failures in 15 minutes this module stops trying for 15 minutes on its own, so a
  misconfigured secret never locks the account.
- **Clock.** A TOTP code is only right on a clock within about a minute of the broker's;
  ``skopaq preflight`` compares the two.

Neither the MPIN, the secret nor a token is ever logged or put in an error message.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

TOKEN_TTL_HOURS = 23.75            # the broker's 24 h, less a margin
MIN_GAP_S = 65.0                   # the broker allows one generation a minute
FAILURE_WINDOW_S = 15 * 60.0
MAX_FAILURES = 2                   # in the window: then pause (the broker locks at 5)
PAUSE_S = 15 * 60.0
_STATE = "token-gen.json"
_LOCK = "token-gen.lock"


class AutoTokenError(Exception):
    """The token could not be generated (the reason is safe to show)."""


@dataclass(frozen=True)
class AutoTokenResult:
    ok: bool                       # a token that carries the session is stored
    generated: bool                # this call made it
    message: str
    expires_at: Optional[datetime] = None


# ── TOTP (RFC 6238, SHA-1, 6 digits, 30 s) ───────────────────────────────────


def _secret_bytes(secret: str) -> bytes:
    cleaned = "".join(secret.split()).upper().rstrip("=")
    if not cleaned:
        raise AutoTokenError("SKOPAQ_INDSTOCKS_TOTP_SECRET is empty")
    try:
        return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (ValueError, TypeError) as exc:
        raise AutoTokenError("SKOPAQ_INDSTOCKS_TOTP_SECRET is not a base32 secret "
                             "(the key shown under the QR code)") from exc


def totp(secret: str, at: Optional[float] = None, *, step: int = 30, digits: int = 6) -> str:
    """The TOTP code of ``secret`` (base32) at ``at`` (epoch seconds; now by default)."""
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(_secret_bytes(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10 ** digits).zfill(digits)


# ── Configuration ────────────────────────────────────────────────────────────


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    value = getter() if callable(getter) else value
    return value.strip() if isinstance(value, str) else ""


def credentials(config: Any) -> Optional[tuple[str, str, str]]:
    """(client id, MPIN, TOTP secret), or None unless all three are set (as text)."""
    client_id = _secret(getattr(config, "indstocks_client_id", ""))
    mpin = _secret(getattr(config, "indstocks_mpin", ""))
    secret = _secret(getattr(config, "indstocks_totp_secret", ""))
    if client_id and mpin and secret:
        return client_id, mpin, secret
    return None


def configured(config: Any) -> bool:
    return credentials(config) is not None


# ── State (on the shared home volume) ────────────────────────────────────────


def _dir() -> Path:
    from skopaq.broker import token_manager

    return Path(token_manager.TOKEN_DIR)


def _read_state() -> dict:
    try:
        data = json.loads((_dir() / _STATE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    path = _dir() / _STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def generation_state(now: Optional[float] = None) -> dict:
    """For the dashboard and preflight: when it last tried, failures, a pause."""
    now = time.time() if now is None else now
    state = _read_state()
    failures = [t for t in state.get("failures", []) if now - t < FAILURE_WINDOW_S]
    return {"last_attempt": state.get("last_attempt"), "last_ok": state.get("last_ok"),
            "last_error": state.get("last_error", ""), "recent_failures": len(failures),
            "paused_until": state.get("paused_until")
            if (state.get("paused_until") or 0) > now else None}


@contextlib.contextmanager
def _locked():
    """One generator at a time across processes (fcntl; a no-op where unavailable)."""
    path = _dir() / _LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        try:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


# ── Generation ───────────────────────────────────────────────────────────────


def _broker_message(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        text = body.get("message") or body.get("error_code") or body.get("error")
        if isinstance(text, dict):
            text = text.get("msg") or text.get("message")
        if text:
            return f"HTTP {resp.status_code}: {str(text)[:200]}"
    return f"HTTP {resp.status_code}"


def _token_of(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    data = body.get("data") if isinstance(body, dict) else None
    for source in (data, body):
        if isinstance(source, dict):
            token = source.get("token") or source.get("access_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
    return ""


async def generate_token(
    config: Any,
    *,
    transport: Any = None,
    wall: Callable[[], float] = time.time,
    sleep: Callable[[float], Any] = asyncio.sleep,
    token_manager: Any = None,
) -> datetime:
    """Make and store a new token now; returns when it expires. Raises
    ``AutoTokenError`` (throttled, paused, refused) — never with a secret in it."""
    import httpx

    from skopaq.broker.token_manager import TokenManager

    creds = credentials(config)
    if creds is None:
        raise AutoTokenError("automatic token is off: set SKOPAQ_INDSTOCKS_CLIENT_ID, "
                             "SKOPAQ_INDSTOCKS_MPIN and SKOPAQ_INDSTOCKS_TOTP_SECRET")
    client_id, mpin, secret = creds
    _secret_bytes(secret)                                   # a bad secret: say so first
    state = _read_state()
    now = wall()
    if (state.get("paused_until") or 0) > now:
        raise AutoTokenError(
            f"paused after repeated failures until "
            f"{datetime.fromtimestamp(state['paused_until'], timezone.utc):%H:%M} UTC "
            f"(last: {state.get('last_error') or 'unknown'}) — check the MPIN, the TOTP "
            "secret and the host clock")
    if now - float(state.get("last_attempt") or 0) < MIN_GAP_S:
        raise AutoTokenError("a token was asked for less than a minute ago (the broker "
                             "allows one a minute)")
    # A code about to roll over may be stale when it arrives: use the next one
    if 30 - (now % 30) < 3:
        await sleep(30 - (now % 30) + 0.5)
        now = wall()
    code = totp(secret, now)
    state["last_attempt"] = now
    _write_state(state)

    url = str(getattr(config, "indstocks_base_url", "https://api.indstocks.com")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as http:
            resp = await http.post(f"{url}/generate/token",
                                   headers={"x-api-key": client_id,
                                            "Content-Type": "application/json"},
                                   json={"mpin": mpin, "totp": code})
    except httpx.HTTPError as exc:
        _failed(state, now, f"broker not reached ({type(exc).__name__})")
        raise AutoTokenError(f"INDstocks not reached: {type(exc).__name__}") from None
    token = _token_of(resp) if resp.status_code < 400 else ""
    if not token:
        reason = _broker_message(resp) if resp.status_code >= 400 else "no token in the answer"
        _failed(state, now, reason)
        raise AutoTokenError(f"INDstocks refused the token request ({reason})")

    (token_manager or TokenManager()).set_token(token, ttl_hours=TOKEN_TTL_HOURS)
    state.update(last_ok=now, last_error="", failures=[], paused_until=None)
    _write_state(state)
    expires = datetime.fromtimestamp(now, timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)
    logger.info("INDstocks token generated with TOTP; valid until %s", expires.isoformat())
    return expires


def _failed(state: dict, now: float, reason: str) -> None:
    failures = [t for t in state.get("failures", []) if now - t < FAILURE_WINDOW_S] + [now]
    state["failures"] = failures
    state["last_error"] = reason
    if len(failures) >= MAX_FAILURES:
        state["paused_until"] = now + PAUSE_S
    _write_state(state)
    logger.error("INDstocks token generation failed: %s", reason)


async def ensure_token(config: Any, required_until: datetime, **kwargs: Any
                       ) -> AutoTokenResult:
    """Make a token only when the stored one cannot carry a session until
    ``required_until`` (and the credentials are set). Never raises."""
    from skopaq.broker.token_manager import TokenManager, session_token_problem

    manager = kwargs.get("token_manager") or TokenManager()
    try:
        health = manager.get_health(notify=False)
        problem = session_token_problem(health, required_until)
    except Exception as exc:
        health, problem = None, f"token unreadable: {exc}"
    if not problem:
        return AutoTokenResult(True, False, "the stored token carries the session",
                               getattr(health, "expires_at", None))
    if not configured(config):
        return AutoTokenResult(False, False, problem)
    try:
        with _locked():
            # Another process may have made it while this one waited for the lock
            health = manager.get_health(notify=False)
            if not session_token_problem(health, required_until):
                return AutoTokenResult(True, False, "another process made today's token",
                                       health.expires_at)
            expires = await generate_token(config, **kwargs)
    except AutoTokenError as exc:
        return AutoTokenResult(False, False, f"automatic token failed: {exc}")
    except Exception as exc:                    # never a secret: only the type
        logger.exception("Automatic token failed")
        return AutoTokenResult(False, False, f"automatic token failed: {type(exc).__name__}")
    return AutoTokenResult(True, True, "a new token was generated with TOTP", expires)


def ensure_token_sync(config: Any, required_until: datetime) -> AutoTokenResult:
    """``ensure_token`` from synchronous code (the scheduler)."""
    return asyncio.run(ensure_token(config, required_until))
