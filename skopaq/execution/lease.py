"""Account-level leases across hosts (Upstash Redis), for what a host flock cannot cover.

``SKOPAQ_ACCOUNT_LEASE_ENABLED=true`` with ``SKOPAQ_UPSTASH_REDIS_URL``/``_TOKEN`` set:

* **entry lease** — each live BUY also takes ``skopaq:<account>:entry`` (``SET NX PX``)
  from the safety check until the broker's answer is final, so two machines trading one
  account never send entries at once (``EntryGate`` holds it inside the host flock);
* **session lease** — a daemon session holds ``skopaq:<account>:session`` (renewed every
  ``ttl/3``); a second session on another host is refused with a CRITICAL alert.

Release is compare-and-delete (only the holder's token deletes it). Enabled but Redis
unreachable → the lease is not granted: entries are refused (fail closed). Exits never
take a lease.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

_RELEASE = ("if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end")
_RENEW = ("if redis.call('get', KEYS[1]) == ARGV[1] then "
          "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end")


class LeaseUnavailable(Exception):
    pass


class RedisLease:
    def __init__(self, client: Any, key: str, *, ttl_s: float = 120.0) -> None:
        self._client = client
        self.key = key
        self.ttl_ms = int(max(1.0, ttl_s) * 1000)
        self.token = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self.held = False

    async def acquire(self, wait_s: float = 0.0, poll_s: float = 0.5) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + max(0.0, wait_s)
        while True:
            try:
                ok = await self._client.set(self.key, self.token, nx=True, px=self.ttl_ms)
            except Exception as exc:
                raise LeaseUnavailable(f"lease store unreachable ({exc})") from exc
            if ok:
                self.held = True
                return True
            if loop.time() >= end:
                return False
            await asyncio.sleep(poll_s)

    async def holder(self) -> Optional[str]:
        try:
            return await self._client.get(self.key)
        except Exception:
            return None

    async def renew(self) -> bool:
        try:
            return bool(await self._client.eval(_RENEW, keys=[self.key],
                                                args=[self.token, str(self.ttl_ms)]))
        except Exception:
            return False

    async def release(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            await self._client.eval(_RELEASE, keys=[self.key], args=[self.token])
        except Exception:
            logger.warning("Lease %s not released (it expires by itself)", self.key)


def lease_client(config: Any) -> Optional[Any]:
    """The Redis client when account leases are enabled (None: host-only locking)."""
    if getattr(config, "account_lease_enabled", False) is not True:
        return None
    url = getattr(config, "upstash_redis_url", "")
    token = getattr(config, "upstash_redis_token", None)
    token = token.get_secret_value() if hasattr(token, "get_secret_value") else (token or "")
    if not url or not token:
        raise LeaseUnavailable("SKOPAQ_ACCOUNT_LEASE_ENABLED needs SKOPAQ_UPSTASH_REDIS_URL "
                               "and SKOPAQ_UPSTASH_REDIS_TOKEN")
    from upstash_redis.asyncio import Redis

    return Redis(url=url, token=token, allow_telemetry=False)


def account_key(config: Any, what: str) -> str:
    account = str(getattr(config, "indstocks_client_id", "") or "default") or "default"
    return f"skopaq:{account}:{what}"
