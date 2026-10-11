"""The order lifecycle: thirteen explicit states, their allowed transitions, and each
order's history rebuilt from the order journal.

    CREATED → VALIDATED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
                                   ↘ REJECTED / FAILED     ↘ CANCEL_PENDING → CANCELLED
    any non-terminal → UNKNOWN / RECONCILIATION_REQUIRED → (broker evidence) → …

Every state change comes from broker evidence or Skopaq's own action, journalled as it
happens (``OrderJournal``: ``created``/``validated`` by the executor, ``submitting``,
``placed``, ``cancel_sent``, ``final``, ``uncertain``, ``stuck``, ``interrupted``,
``not_placed`` by the live order worker, ``reconciled``/``reconciled_absent`` by the
startup reconciliation, ``operator_resolved`` by an admin). ``lifecycles`` replays them,
so a restarted process sees exactly where every order of the day stands. A transition
the table does not allow is kept (the broker is the truth) and flagged in ``anomalies``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


class LifecycleState(str, Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


S = LifecycleState
TERMINAL = frozenset({S.FILLED, S.CANCELLED, S.REJECTED, S.EXPIRED, S.FAILED})
_UNSURE = {S.UNKNOWN, S.RECONCILIATION_REQUIRED}
_LIVE = {S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.CANCEL_PENDING}

TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    S.CREATED: frozenset({S.VALIDATED, S.REJECTED, S.FAILED}),
    S.VALIDATED: frozenset({S.SUBMITTED, S.FAILED}),
    S.SUBMITTED: frozenset({S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.REJECTED,
                            S.FAILED, S.CANCELLED, S.EXPIRED, *_UNSURE}),
    S.ACKNOWLEDGED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_PENDING, S.CANCELLED,
                               S.REJECTED, S.EXPIRED, *_UNSURE}),
    S.PARTIALLY_FILLED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_PENDING,
                                   S.CANCELLED, S.EXPIRED, *_UNSURE}),
    S.CANCEL_PENDING: frozenset({S.CANCEL_PENDING, S.CANCELLED, S.FILLED,
                                 S.PARTIALLY_FILLED, S.EXPIRED, *_UNSURE}),
    S.UNKNOWN: frozenset({*_LIVE, S.FILLED, S.CANCELLED, S.REJECTED, S.EXPIRED, S.FAILED,
                          *_UNSURE}),
    S.RECONCILIATION_REQUIRED: frozenset({*_LIVE, S.FILLED, S.CANCELLED, S.REJECTED,
                                          S.EXPIRED, S.FAILED, *_UNSURE}),
    **{t: frozenset() for t in TERMINAL},
}


def can_transition(old: Optional[LifecycleState], new: LifecycleState) -> bool:
    if old is None:
        return new in (S.CREATED, S.VALIDATED, S.SUBMITTED, S.RECONCILIATION_REQUIRED,
                       S.ACKNOWLEDGED)
    return new in TRANSITIONS[old]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def state_of_line(line: dict) -> Optional[LifecycleState]:
    """The state one journal line puts its order in (None: no lifecycle meaning)."""
    ev = line.get("event")
    status = str(line.get("status") or "").upper()
    filled, qty = _num(line.get("filled")), _num(line.get("qty"))
    if ev == "created":
        return S.CREATED
    if ev == "validated":
        return S.VALIDATED
    if ev == "submitting":
        return S.SUBMITTED
    if ev in ("placed", "reconciled"):
        if status in ("SUCCESS", "COMPLETE", "FILLED", "EXECUTED", "TRADED"):
            return S.FILLED
        return S.PARTIALLY_FILLED if filled > 0 else S.ACKNOWLEDGED
    if ev == "cancel_sent":
        return S.CANCEL_PENDING
    if ev == "not_placed":
        return S.REJECTED
    if ev in ("uncertain", "interrupted"):
        return S.RECONCILIATION_REQUIRED
    if ev == "stuck":
        return S.PARTIALLY_FILLED if filled > 0 else S.UNKNOWN
    if ev in ("reconciled_absent", "operator_resolved"):
        return S.FAILED
    if ev == "final":
        if "EXPIRED" in status:
            return S.EXPIRED
        if status in ("FAILED", "ABORTED", "REJECTED") and filled <= 0:
            return S.REJECTED
        if qty and filled >= qty:
            return S.FILLED
        if status in ("SUCCESS", "COMPLETE", "FILLED", "EXECUTED", "TRADED"):
            return S.FILLED
        return S.CANCELLED          # cancelled, possibly after a partial fill
    return None


@dataclass
class Lifecycle:
    key: str                       # internal id (order id when it has none)
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    qty: float = 0.0
    filled: float = 0.0
    state: Optional[LifecycleState] = None
    history: list[tuple[str, str]] = field(default_factory=list)   # (ts, state)
    anomalies: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def as_dict(self) -> dict:
        return {"key": self.key, "order_id": self.order_id, "symbol": self.symbol,
                "side": self.side, "qty": self.qty, "filled": self.filled,
                "state": self.state.value if self.state else None,
                "terminal": self.terminal,
                "history": [{"ts": t, "state": st} for t, st in self.history],
                "anomalies": self.anomalies}


def lifecycles(lines: Iterable[dict]) -> list[Lifecycle]:
    """Each order's lifecycle from the day's journal lines (in order)."""
    by_key: dict[str, Lifecycle] = {}
    alias: dict[str, str] = {}              # order id -> internal id
    order: list[str] = []
    for line in lines:
        new = state_of_line(line)
        if new is None:
            continue
        iid, oid = str(line.get("internal_id") or ""), str(line.get("order_id") or "")
        key = iid or alias.get(oid) or oid
        if not key:
            continue
        if iid and oid:
            alias[oid] = iid
        lc = by_key.get(key)
        if lc is None:
            lc = by_key[key] = Lifecycle(key=key)
            order.append(key)
        lc.order_id = oid or lc.order_id
        lc.symbol = line.get("symbol") or lc.symbol
        lc.side = line.get("side") or lc.side
        lc.qty = _num(line.get("qty")) or lc.qty
        lc.filled = max(lc.filled, _num(line.get("filled")))
        if not can_transition(lc.state, new):
            lc.anomalies.append(f"{lc.state.value if lc.state else '∅'} → {new.value} "
                                f"({line.get('event')})")
        if lc.state != new or not lc.history:
            lc.history.append((str(line.get("ts") or ""), new.value))
        lc.state = new
    return [by_key[k] for k in order]
