"""Shared API state across replicas (Upstash Redis), when
``SKOPAQ_API_SHARED_STATE_ENABLED=true``: the failed-login lockout and the
duplicate-submission keys then hold for every API process, not just one. Without it (one
API process, the default deployment) both stay in memory. Redis errors fail closed for
order submissions (409) and fall back to the in-memory lockout for logins.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)
_clients: dict[str, Any] = {}


def shared_client(config: Any) -> Optional[Any]:
    if getattr(config, "api_shared_state_enabled", False) is not True:
        return None
    url = getattr(config, "upstash_redis_url", "")
    token = getattr(config, "upstash_redis_token", None)
    token = token.get_secret_value() if hasattr(token, "get_secret_value") else (token or "")
    if not url or not token:
        return None
    if url not in _clients:
        from upstash_redis.asyncio import Redis

        _clients[url] = Redis(url=url, token=token, allow_telemetry=False)
    return _clients[url]


class SharedLockout:
    """20 failures in 10 min lock an address for 10 min, across replicas."""

    def __init__(self, client: Any, *, limit: int = 20, window_s: int = 600,
                 lock_s: int = 600) -> None:
        self.c, self.limit, self.window, self.lock = client, limit, window_s, lock_s

    async def locked(self, ip: str) -> bool:
        return bool(await self.c.get(f"skopaq:lock:{ip}"))

    async def failure(self, ip: str) -> None:
        key = f"skopaq:fail:{ip}"
        n = await self.c.incr(key)
        if n == 1:
            await self.c.expire(key, self.window)
        if n >= self.limit:
            await self.c.set(f"skopaq:lock:{ip}", "1", ex=self.lock)
            await self.c.delete(key)

    async def success(self, ip: str) -> None:
        await self.c.delete(f"skopaq:fail:{ip}")


class SharedKeys:
    """Idempotency keys: claim (NX), then store the answer for replays."""

    def __init__(self, client: Any, ttl_s: int = 600) -> None:
        self.c, self.ttl = client, ttl_s

    async def claim(self, key: str) -> tuple[bool, Optional[Any]]:
        """(claimed, stored answer): (True, None) — run it; (False, None) — in flight;
        (False, answer) — replay."""
        k = f"skopaq:idem:{key}"
        if await self.c.set(k, "__pending__", nx=True, ex=self.ttl):
            return True, None
        raw = await self.c.get(k)
        if raw in (None, "__pending__"):
            return False, None
        try:
            return False, json.loads(raw)
        except ValueError:
            return False, None

    async def store(self, key: str, value: Any) -> None:
        await self.c.set(f"skopaq:idem:{key}", json.dumps(value, default=str), ex=self.ttl)

    async def forget(self, key: str) -> None:
        await self.c.delete(f"skopaq:idem:{key}")
