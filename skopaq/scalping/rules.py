"""Exit rules of an open scalp, shared by the live engine and the backtest.

In order: stop-loss, target, time stop (held ``max_hold`` without profit), and the
breakeven/trailing step — at 1 R in profit the stop moves to entry plus the trade's
charges per share, and from then on trails one ATR below the high.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class ScalpPosition:
    symbol: str
    strategy: str
    qty: int
    entry: float
    stop: float
    target: float
    opened_at: datetime
    atr: float = 0.0
    cost_per_share: float = 0.0
    high: float = 0.0
    breakeven: bool = False
    scrip_code: str = ""
    initial_stop: float = field(default=0.0)
    exiting: bool = False

    def __post_init__(self) -> None:
        if self.high <= 0:
            self.high = self.entry
        if self.initial_stop <= 0:
            self.initial_stop = self.stop

    @property
    def risk(self) -> float:
        return max(self.entry - self.initial_stop, 0.01)


def update_trail(pos: ScalpPosition, price: float) -> None:
    """Raise the high; move the stop to breakeven at 1 R, then trail one ATR below."""
    if price > pos.high:
        pos.high = price
    if not pos.breakeven and pos.high - pos.entry >= pos.risk:
        pos.breakeven = True
        pos.stop = max(pos.stop, round(pos.entry + pos.cost_per_share, 2))
    if pos.breakeven and pos.atr > 0:
        pos.stop = max(pos.stop, round(pos.high - pos.atr, 2))


def exit_reason(pos: ScalpPosition, price: float, now: datetime,
                max_hold: timedelta) -> Optional[str]:
    """Why to sell now at ``price``, or None."""
    if price <= pos.stop:
        kind = "TRAIL" if pos.breakeven else "STOP"
        return f"SCALP {kind}: {price:.2f} <= {pos.stop:.2f} ({pos.strategy})"
    if price >= pos.target:
        return f"SCALP TARGET: {price:.2f} >= {pos.target:.2f} ({pos.strategy})"
    if now - pos.opened_at >= max_hold and price <= pos.entry + pos.cost_per_share:
        return f"SCALP TIME STOP: no profit after {int(max_hold.total_seconds() // 60)} min"
    return None
