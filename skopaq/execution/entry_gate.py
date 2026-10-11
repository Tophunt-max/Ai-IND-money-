"""Sequential entries: one BUY at a time per account, and none while exposure is unknown.

Every entry path (daemon swing BUYs, scalper, F&O engine, chat, dashboard, CLI) reaches
the broker through ``Executor.execute_signal``, which holds the ``EntryGate`` for a BUY
from the safety check until the router returns — for a live order that is when the
broker's answer is final (filled, cancelled, rejected) or the order was recorded as
unconfirmed. So:

* within one process, an ``asyncio.Lock`` serialises the BUYs of every engine (the
  scalper and F&O engine run as tasks beside the swing session and share one checker:
  without it both could pass ``max_open_positions`` or the funds check together);
* across processes on this host (live), an ``flock`` on
  ``<SKOPAQ_ORDER_LOCK_DIR>/entry-account.lock`` does the same. It is per host: two
  machines trading one account are not coordinated (see docs/architecture/execution-safety.md);
* a BUY is refused while an earlier placement's outcome is unknown: an uncertain BUY,
  an own BUY order still working, or a journalled ``submitting`` line with no recorded
  answer (a process died mid-placement). An admin resolves those after checking the
  broker (``POST /api/dashboard/control/reconcile/{internal_id}/resolve``).

SELLs never take this gate: protective exits keep running while entries are blocked.
If the lock directory cannot be used in live mode, entries are refused (fail closed).
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from skopaq.execution.sell_lock import SellLock, SellLockBusy

_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}


class EntryBusy(SellLockBusy):
    """Another entry is in progress (or the lock cannot be used): this BUY is refused."""


def _process_lock(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    k = (key, id(loop))
    lock = _LOCKS.get(k)
    if lock is None:
        # Forget locks of closed loops (tests and CLI commands run one loop each)
        for old in [x for x in _LOCKS if x[0] == key and x[1] != id(loop)]:
            if not _LOCKS[old].locked():
                _LOCKS.pop(old, None)
        lock = _LOCKS[k] = asyncio.Lock()
    return lock


class _AccountLock(SellLock):
    """``entry-account.lock`` in the order lock dir; never proceeds without the lock."""

    def __init__(self, *, wait_s: float, lock_dir: Path, sleep, clock) -> None:
        super().__init__("ENTRY", wait_s=wait_s, lock_dir=lock_dir, sleep=sleep, clock=clock)
        self.path = Path(lock_dir).expanduser() / "entry-account.lock"

    def _busy(self, holder: str) -> SellLockBusy:
        return EntryBusy(f"another entry is in progress{f' (pid {holder})' if holder else ''}"
                         f"; waited {self.wait_s:.0f}s")

    def _unavailable(self, exc: Exception) -> None:
        raise EntryBusy(f"the entry lock cannot be used ({exc}): new entries are refused")


class EntryGate:
    """Async context manager: the in-process lock, then (live) the account flock."""

    def __init__(self, *, wait_s: float, lock_dir: Optional[Path], live: bool,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic, key: str = "account") -> None:
        self.wait_s = max(0.0, float(wait_s))
        self._lock_dir = lock_dir
        self._live = live
        self._sleep = sleep
        self._clock = clock
        self._key = f"{key}:{lock_dir}" if lock_dir else key
        self._plock: Optional[asyncio.Lock] = None
        self._flock: Optional[_AccountLock] = None

    async def __aenter__(self) -> "EntryGate":
        lock = _process_lock(self._key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self.wait_s or 0.001)
        except asyncio.TimeoutError:
            raise EntryBusy(f"another entry in this process is in progress; waited "
                            f"{self.wait_s:.0f}s") from None
        self._plock = lock
        if self._live:
            if self._lock_dir is None:
                lock.release()
                self._plock = None
                raise EntryBusy("SKOPAQ_ORDER_LOCK_DIR cannot be used: new live entries "
                                "are refused")
            flock = _AccountLock(wait_s=self.wait_s, lock_dir=self._lock_dir,
                                 sleep=self._sleep, clock=self._clock)
            try:
                await flock.__aenter__()
            except BaseException:
                lock.release()
                self._plock = None
                raise
            self._flock = flock
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            if self._flock is not None:
                await self._flock.__aexit__(*exc)
        finally:
            self._flock = None
            if self._plock is not None:
                self._plock.release()
                self._plock = None


def entry_blockers(registry: Any, journal: Any) -> list[str]:
    """Why a new BUY must wait for reconciliation (empty: it may go ahead)."""
    reasons: list[str] = []
    try:
        for p in registry.uncertain():
            if getattr(p, "side", "") == "BUY":
                reasons.append(f"BUY {p.qty} {p.symbol}: placement outcome unknown")
        for t in registry.unresolved():
            if getattr(t, "side", "") == "BUY":
                reasons.append(f"BUY order {t.order_id} {t.symbol} not final yet")
    except Exception as exc:                      # an unreadable registry blocks entries
        reasons.append(f"order registry unreadable: {exc}")
    if journal is not None:
        try:
            for e in journal.entry_blockers(own_pid=os.getpid()):
                reasons.append(f"{e.get('side', '?')} {e.get('qty', '?')} "
                               f"{e.get('symbol', '?')} ({e.get('internal_id', '')[:12]}): "
                               f"{e.get('blocker')}")
        except Exception as exc:
            reasons.append(f"order journal unreadable: {exc}")
    return reasons
