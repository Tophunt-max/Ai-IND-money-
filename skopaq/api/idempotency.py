"""Duplicate-submission protection for state-changing dashboard actions.

A request that may place or close a real position carries an ``Idempotency-Key`` header
(the dashboard sends a fresh UUID per click). The first request with a key runs; a second
one with the same key while it runs gets 409, and after it finished gets the first
answer again — a double click or a retried request never places a second order.

Without a key, the same user sending the same body within ``window_s`` is refused (409)
too. State is in this process's memory (one API process per deployment); with several
replicas, put a shared store behind ``IdempotencyStore``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_TTL_S = 600.0
_WINDOW_S = 5.0
_MAX = 2000


class IdempotencyStore:
    def __init__(self, *, ttl_s: float = _TTL_S, window_s: float = _WINDOW_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl, self._window, self._clock = ttl_s, window_s, clock
        self._lock = threading.Lock()
        self._keys: dict[str, tuple[float, Optional[Any]]] = {}   # key -> (at, result|None)
        self._bodies: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        for k in [k for k, (at, _) in self._keys.items() if now - at > self._ttl]:
            self._keys.pop(k, None)
        for k in [k for k, at in self._bodies.items() if now - at > self._window]:
            self._bodies.pop(k, None)
        while len(self._keys) > _MAX:
            self._keys.pop(next(iter(self._keys)))

    @asynccontextmanager
    async def guard(self, user: str, action: str, body: Any,
                    key: Optional[str], shared: Any = None) -> AsyncIterator["_Slot"]:
        """Run the block once per key (or per identical body in the window). ``shared``:
        a ``skopaq.api.shared_state.SharedKeys`` — then the claim holds across API
        replicas (and a Redis error refuses the request rather than risk a duplicate)."""
        if shared is not None:
            async with self._shared_guard(shared, user, action, body, key) as slot:
                yield slot
            return
        now = self._clock()
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                                .encode()).hexdigest()[:32]
        slot = _Slot()
        with self._lock:
            self._prune(now)
            if key:
                k = f"{user}|{action}|{key[:100]}"
                if k in self._keys:
                    _, result = self._keys[k]
                    if result is None:
                        raise HTTPException(409, "This request is already being processed")
                    slot.replay = result
                else:
                    self._keys[k] = (now, None)
            else:
                k = ""
                b = f"{user}|{action}|{digest}"
                slot.body_key = b
                if b in self._bodies:
                    raise HTTPException(409, "The same request was just sent: wait a few "
                                             "seconds, or send an Idempotency-Key")
                self._bodies[b] = now
        if slot.replay is not None:
            yield slot
            return
        try:
            yield slot
        except BaseException:
            with self._lock:                    # refused or failed: it may be sent again
                if k:
                    self._keys.pop(k, None)
                if slot.body_key:
                    self._bodies.pop(slot.body_key, None)
            raise
        if k:
            with self._lock:
                self._keys[k] = (self._clock(), slot.result)


    @asynccontextmanager
    async def _shared_guard(self, shared: Any, user: str, action: str, body: Any,
                            key: Optional[str]) -> AsyncIterator["_Slot"]:
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                                .encode()).hexdigest()[:32]
        k = f"{user}|{action}|{key[:100]}" if key else f"{user}|{action}|body:{digest}"
        slot = _Slot()
        try:
            claimed, answer = await shared.claim(k)
        except Exception as exc:
            raise HTTPException(409, f"Duplicate check unavailable ({exc}): not sent") from exc
        if not claimed:
            if answer is None:
                raise HTTPException(409, "This request is already being processed")
            if not key:
                raise HTTPException(409, "The same request was just sent")
            slot.replay = answer
            yield slot
            return
        try:
            yield slot
        except BaseException:
            try:
                await shared.forget(k)
            except Exception:
                pass
            raise
        try:
            if key:
                await shared.store(k, slot.result)
        except Exception:
            logger.warning("Idempotency answer not stored", exc_info=True)

    def reset(self) -> None:
        with self._lock:
            self._keys.clear()
            self._bodies.clear()


class _Slot:
    def __init__(self) -> None:
        self.replay: Optional[Any] = None
        self.result: Optional[Any] = None
        self.body_key: str = ""


STORE = IdempotencyStore()
