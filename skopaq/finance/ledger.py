"""Execution ledger of one position: P&L from broker-confirmed fills only.

A ``PositionLedger`` takes fills (``Fill``: order id, side, quantity, price) as the broker
confirms them, partial fills included, and reports:

* requested vs executed quantity per order, average entry and exit prices;
* gross realized P&L (average-cost basis) and unrealized P&L at a mark price;
* estimated charges per *order* (one brokerage per order however many fills it had, so
  partial fills are never charged twice) from ``skopaq.finance.charges``;
* broker-confirmed charges once a contract note gives them (``confirm_charges``), the net
  P&L on them, and the discrepancy against the estimate;
* slippage of each fill against the price the order expected.

Nothing here assumes a fill: an order with no fills adds nothing. A target being reached
is not realized profit until an exit fill is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from skopaq.finance.charges import CURRENT, Charges, ChargeSchedule, leg_charges


@dataclass(frozen=True)
class Fill:
    order_id: str
    side: str                 # BUY / SELL
    qty: float
    price: float
    expected_price: Optional[float] = None   # the decision's price, for slippage
    fill_id: str = ""         # the broker's trade id, when it has one (dedup)


@dataclass
class _Order:
    order_id: str
    side: str
    requested: float = 0.0
    filled: float = 0.0
    value: float = 0.0
    fill_ids: set[str] = field(default_factory=set)

    @property
    def avg_price(self) -> Optional[float]:
        return self.value / self.filled if self.filled else None


class PositionLedger:
    """One long position's executions (buys open, sells close; never short)."""

    def __init__(self, symbol: str, product: str, *,
                 schedule: ChargeSchedule = CURRENT) -> None:
        self.symbol = symbol
        self.product = product
        self.schedule = schedule
        self.orders: dict[str, _Order] = {}
        self.slippage: float = 0.0             # ₹, positive = worse than expected
        self.qty: float = 0.0                  # open quantity
        self.avg_cost: float = 0.0             # of the open quantity
        self.realized_gross: float = 0.0
        self.confirmed: Optional[Charges] = None
        self.discrepancies: list[str] = []

    def request(self, order_id: str, side: str, qty: float) -> None:
        """An order was sent for ``qty`` (no fill yet)."""
        order = self.orders.setdefault(order_id, _Order(order_id, side.upper()))
        order.requested = max(order.requested, float(qty))

    def add_fill(self, fill: Fill) -> bool:
        """Record a confirmed fill; False (ignored) for a duplicate trade id, a zero
        quantity, or a SELL beyond what is held (that is a reconciliation problem)."""
        side = fill.side.upper()
        if fill.qty <= 0 or fill.price <= 0:
            return False
        order = self.orders.setdefault(fill.order_id, _Order(fill.order_id, side))
        if fill.fill_id and fill.fill_id in order.fill_ids:
            return False
        if side == "SELL" and fill.qty > self.qty + 1e-9:
            self.discrepancies.append(
                f"SELL fill {fill.qty} of {fill.order_id} exceeds the open {self.qty}")
            return False
        if fill.fill_id:
            order.fill_ids.add(fill.fill_id)
        order.filled += fill.qty
        order.value += fill.qty * fill.price
        if fill.expected_price:
            diff = fill.price - fill.expected_price
            self.slippage += (diff if side == "BUY" else -diff) * fill.qty
        if side == "BUY":
            total = self.avg_cost * self.qty + fill.price * fill.qty
            self.qty += fill.qty
            self.avg_cost = total / self.qty
        else:
            self.realized_gross += (fill.price - self.avg_cost) * fill.qty
            self.qty -= fill.qty
            if self.qty <= 1e-9:
                self.qty, self.avg_cost = 0.0, 0.0
        return True

    # ── Reports ─────────────────────────────────────────────────────────

    def _side(self, side: str) -> list[_Order]:
        return [o for o in self.orders.values() if o.side == side and o.filled > 0]

    def avg_entry(self) -> Optional[float]:
        buys = self._side("BUY")
        qty = sum(o.filled for o in buys)
        return sum(o.value for o in buys) / qty if qty else None

    def avg_exit(self) -> Optional[float]:
        sells = self._side("SELL")
        qty = sum(o.filled for o in sells)
        return sum(o.value for o in sells) / qty if qty else None

    def estimated_charges(self) -> Charges:
        total = Charges(schedule=self.schedule.version)
        for o in self.orders.values():
            if o.filled > 0:
                total = total + leg_charges(self.product, o.side, o.value,
                                            schedule=self.schedule)
        return total

    def unrealized(self, mark: Optional[float]) -> float:
        if not mark or self.qty <= 0:
            return 0.0
        return (mark - self.avg_cost) * self.qty

    def confirm_charges(self, charges: Charges, *, tolerance: float = 1.0) -> None:
        """The broker's (contract note) charges for every order of this position."""
        self.confirmed = Charges(**{**charges.__dict__, "estimated": False})
        diff = self.confirmed.total - self.estimated_charges().total
        if abs(diff) > tolerance:
            self.discrepancies.append(
                f"charges: broker ₹{self.confirmed.total:.2f} vs estimate "
                f"₹{self.estimated_charges().total:.2f} (diff ₹{diff:+.2f})")

    @property
    def closed(self) -> bool:
        return self.qty <= 1e-9 and bool(self._side("SELL"))

    def summary(self, mark: Optional[float] = None) -> dict:
        est = self.estimated_charges()
        charges = self.confirmed or est
        return {
            "symbol": self.symbol, "product": self.product, "open_qty": self.qty,
            "avg_entry": self.avg_entry(), "avg_exit": self.avg_exit(),
            "orders": [{"order_id": o.order_id, "side": o.side, "requested": o.requested,
                        "filled": o.filled, "avg_price": o.avg_price}
                       for o in self.orders.values()],
            "gross_realized": round(self.realized_gross, 2),
            "unrealized": round(self.unrealized(mark), 2),
            "estimated_charges": est.as_dict(),
            "confirmed_charges": self.confirmed.as_dict() if self.confirmed else None,
            "net_realized_estimated": round(self.realized_gross - est.total, 2),
            "net_realized_confirmed": (round(self.realized_gross - self.confirmed.total, 2)
                                       if self.confirmed else None),
            "net_status": "reconciled" if self.confirmed and self.closed else
                          "estimated" if self.closed else "open",
            "slippage": round(self.slippage, 2),
            "charges_used": "confirmed" if self.confirmed else "estimated",
            "net_total": round(self.realized_gross - charges.total, 2),
            "discrepancies": list(self.discrepancies),
        }
