"""Exit plans: the stop-loss, target and profit booking of each position.

A plan is made when a BUY fills (``Executor``) from its fill price and the stop-loss the
position sizer gave it, and for a position the monitor finds without one (a manual
BUY, a BUY whose fill was confirmed late) from the broker's average price and the hard
stop. The monitor then sells:

- everything at the **stop-loss** (the plan's, or the hard stop if that is tighter);
- at the **target**, ``monitor_partial_booking_pct`` of the position (all of it at 1);
- the rest with a stop at **breakeven** that **trails** ``monitor_trailing_stop_pct``
  below the high-water mark;
- everything at the EOD exit, as before.

Targets (``monitor_target_mode``):

- ``rr``: entry + ``monitor_target_rr`` × (entry − stop): 2 is 1:2 risk:reward;
- ``pct``: entry × (1 + ``monitor_target_pct``);
- ``inr``: entry + ``monitor_target_inr`` / quantity (that much profit on the position);
- ``off``: no target (stop-loss, trailing stop and EOD only).

Plans are kept per day in ``exit_plan_dir`` (``<YYYY-MM-DD>.json``, IST), keyed by mode
and symbol, with the high-water mark and what was already booked: a restarted monitor
(the daemon restarts it, the scheduler runs a recovery ``skopaq monitor``) neither books
the target twice nor forgets the high. A store that cannot be written is logged and the
plan kept in memory.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
TARGET_MODES = ("off", "rr", "pct", "inr")


def _now_ist() -> datetime:
    return datetime.now(_IST)


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    """``value`` as a finite float within [lo, hi]; ``default`` for anything else (a test
    double, a typo)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if not math.isfinite(value):
        return default
    return min(hi, max(lo, value))


@dataclass
class ExitPlan:
    """How one position is exited."""

    symbol: str
    mode: str                     # "paper" / "live"
    entry_price: float
    stop_loss: float              # sell everything at or below
    target: Optional[float]       # book profit at or above; None: no target
    initial_qty: int              # the position the booking share is taken of
    partial_fraction: float = 1.0  # share of initial_qty sold at the target
    booked_qty: int = 0           # sold at the target so far
    target_hit: bool = False      # the target's booking is done: the rest trails
    high_water_mark: float = 0.0
    source: str = "default"       # "signal" (made at the BUY) or "default"
    created_at: str = field(default_factory=lambda: _now_ist().isoformat())

    @property
    def booking_qty(self) -> int:
        """Shares to sell at the target in all; 0 means the whole position."""
        if self.partial_fraction >= 1:
            return 0
        return int(math.floor(self.initial_qty * self.partial_fraction))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["ExitPlan"]:
        try:
            target = data.get("target")
            return cls(
                symbol=str(data["symbol"]),
                mode=str(data["mode"]),
                entry_price=float(data["entry_price"]),
                stop_loss=float(data["stop_loss"]),
                target=float(target) if target is not None else None,
                initial_qty=int(data["initial_qty"]),
                partial_fraction=float(data.get("partial_fraction", 1.0)),
                booked_qty=int(data.get("booked_qty", 0)),
                target_hit=bool(data.get("target_hit", False)),
                high_water_mark=float(data.get("high_water_mark", 0.0)),
                source=str(data.get("source", "default")),
                created_at=str(data.get("created_at", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


class ExitPlanStore:
    """Today's plans in ``<dir>/<YYYY-MM-DD>.json``, keyed ``"<mode>:<SYMBOL>"``.

    Writes are atomic (a temp file renamed over the old one). Reads and writes never
    raise: a failure is logged and the caller keeps its plan in memory.
    """

    def __init__(self, directory: str | os.PathLike, *,
                 today: Callable[[], datetime] = _now_ist) -> None:
        self._dir = Path(os.path.expanduser(str(directory)))
        self._today = today
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: Any) -> Optional["ExitPlanStore"]:
        directory = getattr(config, "exit_plan_dir", None)
        if not isinstance(directory, str) or not directory.strip():
            return None
        return cls(directory)

    def _path(self) -> Path:
        return self._dir / f"{self._today().astimezone(_IST).date().isoformat()}.json"

    def _read(self) -> dict[str, Any]:
        path = self._path()
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("Exit plans %s unreadable — starting from none", path,
                           exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _key(mode: str, symbol: str) -> str:
        return f"{mode}:{symbol.upper()}"

    def get(self, mode: str, symbol: str) -> Optional[ExitPlan]:
        with self._lock:
            row = self._read().get(self._key(mode, symbol))
        return ExitPlan.from_dict(row) if isinstance(row, dict) else None

    def put(self, plan: ExitPlan) -> bool:
        """Save ``plan``; False when it could not be written."""
        with self._lock:
            data = self._read()
            data[self._key(plan.mode, plan.symbol)] = plan.to_dict()
            path = self._path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plans-", suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as fh:
                        json.dump(data, fh, indent=1, sort_keys=True)
                    os.replace(tmp, path)
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)
                    raise
            except OSError:
                logger.warning("Exit plan of %s not saved (%s) — kept in memory only",
                               plan.symbol, path, exc_info=True)
                return False
        return True


class ExitPlanner:
    """Makes, finds and saves exit plans from the ``monitor_*`` settings."""

    def __init__(self, config: Any, store: Optional[ExitPlanStore] = None) -> None:
        mode = getattr(config, "monitor_target_mode", "off")
        self.target_mode = mode if isinstance(mode, str) and mode in TARGET_MODES else "off"
        self.target_rr = _number(getattr(config, "monitor_target_rr", 2.0), 2.0, 0.1, 20.0)
        self.target_pct = _number(getattr(config, "monitor_target_pct", 0.03), 0.03,
                                  0.0005, 1.0)
        self.target_inr = _number(getattr(config, "monitor_target_inr", 1000.0), 1000.0,
                                  1.0, 1e9)
        self.partial_fraction = _number(getattr(config, "monitor_partial_booking_pct", 1.0),
                                        1.0, 0.0, 1.0)
        if self.partial_fraction <= 0:
            self.partial_fraction = 1.0   # 0 would book nothing: sell all at the target
        self.hard_stop_pct = _number(getattr(config, "monitor_hard_stop_pct", 0.04), 0.04,
                                     0.001, 0.5)
        self.trail_pct = _number(getattr(config, "monitor_trailing_stop_pct", 0.02), 0.02,
                                 0.001, 0.5)
        self._store = store
        self._memory: dict[str, ExitPlan] = {}

    # ── Making plans ─────────────────────────────────────────────────────

    def target_for(self, entry: float, stop: float, qty: int) -> Optional[float]:
        """The target price for an entry and its stop; None when targets are off."""
        if entry <= 0:
            return None
        if self.target_mode == "rr":
            target = entry + self.target_rr * (entry - stop)
        elif self.target_mode == "pct":
            target = entry * (1 + self.target_pct)
        elif self.target_mode == "inr":
            target = entry + self.target_inr / max(1, qty)
        else:
            return None
        target = round(target, 2)
        return target if target > entry else None

    def _stop_for(self, entry: float, stop: Optional[float]) -> float:
        """The BUY's stop when it is below entry, else the hard stop."""
        if isinstance(stop, (int, float)) and not isinstance(stop, bool) \
                and 0 < float(stop) < entry:
            return round(float(stop), 2)
        return round(entry * (1 - self.hard_stop_pct), 2)

    def make(self, symbol: str, mode: str, entry: float, qty: int,
             stop: Optional[float] = None, *, source: str = "default") -> ExitPlan:
        stop_loss = self._stop_for(entry, stop)
        return ExitPlan(
            symbol=symbol.upper(), mode=mode, entry_price=round(float(entry), 2),
            stop_loss=stop_loss, target=self.target_for(entry, stop_loss, qty),
            initial_qty=max(1, int(qty)), partial_fraction=self.partial_fraction,
            high_water_mark=float(entry), source=source,
        )

    def plan_for_entry(self, symbol: str, mode: str, entry: float, qty: int,
                       stop: Optional[float] = None) -> ExitPlan:
        """A new plan for a filled BUY (replaces today's plan of the symbol: the newest
        BUY sets the stop and the target of the whole position). Shares already held
        from before are added by :meth:`plan_for_position`, which sees what is held."""
        plan = self.make(symbol, mode, entry, qty, stop, source="signal")
        self.save(plan)
        logger.info("Exit plan %s: entry %.2f, stop %.2f, target %s, book %s of %d",
                    plan.symbol, plan.entry_price, plan.stop_loss,
                    f"{plan.target:.2f}" if plan.target else "off",
                    plan.booking_qty or "all", plan.initial_qty)
        return plan

    def plan_for_position(self, symbol: str, mode: str, entry: float, qty: int) -> ExitPlan:
        """Today's plan of a held position, or a default one (saved) from its average
        price and the hard stop."""
        plan = self.get(mode, symbol)
        if plan is None:
            plan = self.make(symbol, mode, entry, qty)
            self.save(plan)
            return plan
        held = int(qty) + plan.booked_qty
        if held > plan.initial_qty:
            plan.initial_qty = held   # more was bought since: book a share of all of it
            self.save(plan)
        return plan

    # ── Storage ──────────────────────────────────────────────────────────

    def get(self, mode: str, symbol: str) -> Optional[ExitPlan]:
        key = f"{mode}:{symbol.upper()}"
        if self._store is not None:
            plan = self._store.get(mode, symbol)
            if plan is not None:
                self._memory[key] = plan
                return plan
        return self._memory.get(key)

    def save(self, plan: ExitPlan) -> None:
        self._memory[f"{plan.mode}:{plan.symbol.upper()}"] = plan
        if self._store is not None:
            self._store.put(plan)


def planner_from_config(config: Any) -> ExitPlanner:
    """An ``ExitPlanner`` with the configured store (``exit_plan_dir``)."""
    return ExitPlanner(config, ExitPlanStore.from_config(config))
