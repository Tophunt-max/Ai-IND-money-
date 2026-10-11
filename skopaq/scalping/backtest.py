"""Backtest the scalping strategies on past 1-minute candles (``skopaq scalp-backtest``).

The same strategies and exit rules as the live engine, bar by bar:

- a setup is found on a candle's close and filled at the **next candle's open** (its
  stop and target keep their distances from that fill);
- inside a candle the stop is checked against its low before the target against its high
  (a candle that touches both counts as a loss), and the trail moves on its high after;
- the time stop and the flatten time act on the close; every trade pays the round-trip
  charges of ``skopaq/scalping/costs.py``.

``slippage_pct`` moves every fill against the trade (entry up, exit down) by that
fraction. ``on_decision(candles, setup, decided_at)`` is called for each setup with the
candles known at that close: return None/False to skip it (qualification, a model gate)
or any other value to take it, stored on the trade as ``tag`` (e.g. its features). It
ignores the safety rules' caps (pass ``max_qty`` to apply a share cap such as
``SKOPAQ_MAX_SHARES_PER_ORDER``) and partial fills.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

from skopaq.market.candles import Candle
from skopaq.scalping.costs import round_trip_cost
from skopaq.scalping.rules import ScalpPosition, exit_reason, update_trail
from skopaq.scalping.settings import ScalpSettings
from skopaq.scalping.strategies import atr_last, evaluate

_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class BacktestTrade:
    day: str
    strategy: str
    qty: int
    entry: float
    exit: float
    pnl: float
    reason: str
    opened: str
    closed: str
    decided_at: str = ""          # ISO time of the close the setup was found on
    tag: Any = None               # what on_decision returned (features, signal id)


@dataclass
class BacktestResult:
    symbol: str
    days: int
    trades: list[BacktestTrade] = field(default_factory=list)
    skipped_cost: int = 0

    @property
    def net_pnl(self) -> float:
        return round(sum(t.pnl for t in self.trades), 2)

    def stats(self) -> dict:
        def block(trades: Sequence[BacktestTrade]) -> dict:
            wins = [t.pnl for t in trades if t.pnl > 0]
            losses = [-t.pnl for t in trades if t.pnl <= 0]
            equity = peak = dd = 0.0
            for t in trades:
                equity += t.pnl
                peak = max(peak, equity)
                dd = max(dd, peak - equity)
            return {
                "trades": len(trades),
                "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
                "net_pnl": round(sum(t.pnl for t in trades), 2),
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
                "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(
                    losses) > 0 else None,
                "max_drawdown": round(dd, 2),
            }

        out = {"all": block(self.trades)}
        for name in sorted({t.strategy for t in self.trades}):
            out[name] = block([t for t in self.trades if t.strategy == name])
        return out


def simulate(symbol: str, days: Sequence[Sequence[Candle]], settings: ScalpSettings, *,
             equity: float = 1_000_000.0, max_qty: Optional[int] = None,
             slippage_pct: float = 0.0,
             on_decision: Optional[Callable[[Sequence[Candle], Any, datetime], Any]] = None,
             ) -> BacktestResult:
    """Run the scalper over ``days`` (each a day's 1-minute candles, in order)."""
    result = BacktestResult(symbol=symbol, days=len(days))
    s = settings
    slip = max(0.0, float(slippage_pct))
    for day in days:
        if not day:
            continue
        pos: Optional[ScalpPosition] = None
        pending = None          # (setup, decided at index)
        entries = 0
        day_pnl = 0.0
        last_loss: Optional[datetime] = None
        orb_done = False
        for i, c in enumerate(day):
            now = c.start + timedelta(seconds=s.candle_seconds)    # the candle's close
            if pending is not None and pos is None:
                setup, decided_at, tag = pending
                pending = None
                fill = c.open * (1 + slip)
                qty = _size(setup, fill, equity, s, max_qty)
                if qty > 0:
                    shift = fill - setup.entry
                    atr = atr_last(day[:i]) or setup.risk
                    pos = ScalpPosition(
                        symbol=symbol, strategy=setup.strategy, qty=qty, entry=fill,
                        stop=round(setup.stop + shift, 2), target=round(setup.target + shift, 2),
                        opened_at=c.start, atr=atr,
                        cost_per_share=round_trip_cost(fill, setup.target, qty) / qty)
                    pos_meta = (decided_at, tag)
                    entries += 1
            if pos is not None:
                reason, price = None, c.close
                if c.low <= pos.stop:
                    reason, price = exit_reason(pos, pos.stop, now, s.max_hold), pos.stop
                    price = min(price, c.open) if c.open < pos.stop else price
                elif c.high >= pos.target:
                    reason, price = exit_reason(pos, pos.target, now, s.max_hold), pos.target
                else:
                    update_trail(pos, c.high)
                    reason = exit_reason(pos, c.close, now, s.max_hold)
                if reason is None and now.time() >= s.flatten_at:
                    reason = "SCALP FLATTEN"
                if reason is None and i == len(day) - 1:
                    reason = "SCALP FLATTEN (end of data)"
                if reason:
                    pnl = _book(result, pos, price * (1 - slip), reason, now, *pos_meta)
                    day_pnl += pnl
                    if pnl < 0:
                        last_loss = now
                    pos = None
            if now.time() >= s.flatten_at:
                break
            # Entry decision on this close (filled at the next open)
            if pos is not None or pending is not None:
                continue
            if not (s.entry_start <= now.time() < s.entry_end):
                continue
            if entries >= s.max_trades_per_day or day_pnl <= -s.max_daily_loss:
                continue
            if last_loss is not None and now - last_loss < s.cooldown:
                continue
            names = [n for n in s.strategies if not (n == "orb" and orb_done)]
            setup = evaluate(day[:i + 1], names, s.params)
            if setup is None:
                continue
            qty = _size(setup, setup.entry, equity, s, max_qty)
            if qty <= 0:
                continue
            if setup.reward * qty < s.min_reward_to_cost * round_trip_cost(
                    setup.entry, setup.target, qty):
                result.skipped_cost += 1
                continue
            if i + 1 >= len(day):
                continue
            tag = True
            if on_decision is not None:
                tag = on_decision(day[:i + 1], setup, now)
                if tag is None or tag is False:
                    continue
            if setup.strategy == "orb":
                orb_done = True
            pending = (setup, now, tag)
    return result


def _size(setup, price: float, equity: float, s: ScalpSettings,
          max_qty: Optional[int]) -> int:
    if setup.risk <= 0 or price <= 0:
        return 0
    qty = math.floor(equity * s.risk_per_trade_pct / setup.risk)
    qty = min(qty, math.floor(s.max_position_value / price))
    if max_qty is not None:
        qty = min(qty, max_qty)
    return max(0, int(qty))


def _book(result: BacktestResult, pos: ScalpPosition, price: float, reason: str,
          now: datetime, decided_at: Optional[datetime] = None, tag: Any = None) -> float:
    cost = round_trip_cost(pos.entry, price, pos.qty)
    pnl = round((price - pos.entry) * pos.qty - cost, 2)
    result.trades.append(BacktestTrade(
        day=pos.opened_at.date().isoformat(), strategy=pos.strategy, qty=pos.qty,
        entry=round(pos.entry, 2), exit=round(price, 2), pnl=pnl, reason=reason,
        opened=pos.opened_at.strftime("%H:%M"), closed=now.strftime("%H:%M"),
        decided_at=decided_at.isoformat() if decided_at else "",
        tag=None if tag is True else tag))
    return pnl


async def fetch_days(client, symbol: str, days: int, *, today: Optional[datetime] = None
                     ) -> list[list[Candle]]:
    """The last ``days`` trading days' 1-minute candles from INDstocks (oldest first)."""
    from skopaq.broker.scrip_resolver import resolve_scrip_code

    scrip = await resolve_scrip_code(client, symbol)
    now = (today or datetime.now(_IST)).astimezone(_IST)
    out: list[list[Candle]] = []
    day = now.date()
    looked = 0
    while len(out) < days and looked < days * 2 + 10:
        looked += 1
        start = datetime.combine(day, datetime.min.time(), tzinfo=_IST).replace(hour=9,
                                                                                minute=15)
        end = start.replace(hour=15, minute=30)
        day -= timedelta(days=1)
        if start.weekday() >= 5 or start > now:
            continue
        rows = await client.get_historical(scrip, interval="1minute",
                                           start_time=int(start.timestamp() * 1000),
                                           end_time=int(min(end, now).timestamp() * 1000))
        candles = []
        for r in sorted(rows, key=lambda r: r.timestamp):
            ts = r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc)
            candles.append(Candle(ts.astimezone(_IST).replace(second=0, microsecond=0),
                                  r.open, r.high, r.low, r.close, r.volume or None))
        if candles:
            out.append(candles)
    out.reverse()
    return out
