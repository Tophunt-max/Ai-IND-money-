"""Startup reconciliation: before a live process sends its first BUY, compare what the
order journal says against the broker's order book.

1. Placements journalled without a known outcome (``OrderJournal.entry_blockers``: a
   ``submitting`` line with no answer, an uncertain BUY) are looked up in the book:
   exactly one unknown order of the same instrument, side and quantity → ``reconciled``
   (the order is ours: the monitor resumes it like any journalled order); none →
   ``reconciled_absent`` (it was never placed); several → still blocking, CRITICAL alert.
2. Orders in the book that no Skopaq process placed today are reported as external
   (manual orders, another tool): WARNING, never touched.

New entries stay blocked until this succeeds (``OrderRouter.ensure_startup_reconciled``);
an unreadable book means it is retried on the next BUY. Exits never wait for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class StartupReport:
    ok: bool = False
    matched: list[tuple[str, str]] = field(default_factory=list)   # (internal id, order id)
    absent: list[str] = field(default_factory=list)
    ambiguous: list[tuple[str, list[str]]] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if not self.ok:
            return f"startup reconciliation failed: {self.error}"
        return (f"startup reconciliation: {len(self.matched)} found in the book, "
                f"{len(self.absent)} never placed, {len(self.ambiguous)} ambiguous, "
                f"{len(self.external)} external order(s)")


def _dec(v: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def reconcile_at_start(rows_loader: Callable[[], Any], journal: Any, known_ids: set[str],
                             alert: Callable[..., None]) -> StartupReport:
    rep = StartupReport()
    try:
        rows = list(await rows_loader())
    except Exception as exc:
        rep.error = f"order book unreadable ({exc})"
        alert("CRITICAL", "startup-reconcile-failed",
              f"Startup reconciliation could not read the order book ({exc}): new entries "
              "stay blocked. Action: check the INDstocks token / connectivity; exits run.",
              dedup_s=600)
        return rep
    own = set(known_ids)
    if journal is not None:
        own |= journal.own_ids_today()
    claimed: set[str] = set()
    for b in (journal.entry_blockers() if journal is not None else []):
        iid = str(b.get("internal_id") or "")
        qty = _dec(b.get("qty"))
        cands = [r for r in rows if r.order_id not in own and r.order_id not in claimed
                 and r.side == b.get("side") and qty is not None and r.requested_qty == qty
                 and (not b.get("security_id") or r.security_id == b.get("security_id"))]
        if len(cands) == 1:
            r = cands[0]
            claimed.add(r.order_id)
            journal.record("reconciled", order_id=r.order_id, internal_id=iid,
                           symbol=b.get("symbol", ""), security_id=r.security_id,
                           segment=b.get("segment", ""), side=r.side, qty=qty,
                           purpose=b.get("purpose", ""), filled=r.traded_qty,
                           avg_price=r.traded_price, status=r.status_raw,
                           note="found in the order book at startup")
            rep.matched.append((iid, r.order_id))
        elif not cands:
            journal.record("reconciled_absent", internal_id=iid, symbol=b.get("symbol", ""),
                           side=b.get("side", ""), qty=qty,
                           note="not in the order book at startup")
            rep.absent.append(iid)
        else:
            ids = [r.order_id for r in cands]
            rep.ambiguous.append((iid, ids))
            alert("CRITICAL", f"startup-ambiguous:{iid}",
                  f"{b.get('side')} {b.get('qty')} {b.get('symbol')}: {len(ids)} orders in "
                  f"the book could be it ({', '.join(ids)}). New entries stay blocked. "
                  "Action: check them and resolve it on the dashboard (Signals & risk).",
                  order_ids=ids, dedup_s=600)
    rep.external = [r.order_id for r in rows
                    if r.order_id not in own and r.order_id not in claimed]
    if rep.external:
        alert("WARNING", "external-orders",
              f"{len(rep.external)} order(s) today were not placed by Skopaq (manual or "
              "another tool). They are left alone. Action: make sure they are intended.",
              order_ids=rep.external[:20], dedup_s=3600)
    rep.ok = True
    logger.info(rep.summary())
    return rep
