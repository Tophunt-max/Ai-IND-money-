"""F&O intraday rules: settings, charges, contract choice, sizing and exits.

Pure functions of their inputs, shared by the live options engine
(``skopaq/scalping/fno_engine.py``) and its tests.

- **Direction.** The scalping strategies are long-only setups on candles. A bullish setup
  on the underlying's candles buys a CE (or a future); the same strategies on the
  *inverted* candles (each price ``p`` becomes ``K / p``) find bearish setups, which buy a
  PE. Skopaq only ever BUYS options and futures: it never writes an option nor shorts a
  future (the safety checker refuses any F&O SELL that is not the exit of a long).
- **Contract.** The nearest expiry (``fno_expiry_index``; on its expiry day the next one,
  ``fno_avoid_expiry_day``), the strike ``fno_strike_offset`` steps from the money (0 ATM,
  -1 one step in the money, +1 one out), with a price and a bid-ask spread no wider than
  ``fno_max_spread_pct``.
- **Size.** Whole lots: ``fno_risk_per_trade_inr`` over the premium at risk per lot (the
  premium stop), capped by ``fno_max_lots``, the safety rules' lot cap and
  ``fno_max_premium_inr`` of premium per trade.
- **Exits.** The underlying reaching the setup's stop or target; the premium falling
  ``fno_premium_stop_pct`` (moved to breakeven once the premium gained as much, then
  trailing ``fno_trail_pct`` below its high); a time stop after ``fno_max_hold_minutes``
  without profit; everything at ``fno_flatten_at``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Optional, Sequence

from skopaq.constants import GST_RATE, INDSTOCKS_BROKERAGE_PER_ORDER_INR
from skopaq.market.candles import Candle
from skopaq.scalping.settings import _hhmm, _list, _num
from skopaq.scalping.strategies import STRATEGY_NAMES, Setup, StrategyParams

INSTRUMENTS = ("options", "futures")

# NSE F&O charges (statutory rates; the broker's contract note is exact)
OPT_STT_SELL = 0.001             # 0.1 % of the premium, sell side
OPT_EXCHANGE_TXN = 0.0003503     # NSE options transaction charge, each side
FUT_STT_SELL = 0.0002            # 0.02 % of the notional, sell side
FUT_EXCHANGE_TXN = 0.0000173     # NSE futures transaction charge, each side
SEBI_FEE = 0.000001              # ₹10 per crore, each side
OPT_STAMP_BUY = 0.00003          # 0.003 % buy side
FUT_STAMP_BUY = 0.00002          # 0.002 % buy side


def fno_round_trip_cost(entry: float, exit_price: float, qty: int, *,
                        future: bool = False) -> float:
    """₹ charges of buying ``qty`` units at ``entry`` and selling at ``exit_price``."""
    if qty <= 0:
        return 0.0
    buy, sell = entry * qty, exit_price * qty
    stt, txn, stamp = ((FUT_STT_SELL, FUT_EXCHANGE_TXN, FUT_STAMP_BUY) if future
                       else (OPT_STT_SELL, OPT_EXCHANGE_TXN, OPT_STAMP_BUY))
    brokerage = 2 * INDSTOCKS_BROKERAGE_PER_ORDER_INR
    exchange = (buy + sell) * txn
    sebi = (buy + sell) * SEBI_FEE
    gst = GST_RATE * (brokerage + exchange + sebi)
    return round(brokerage + exchange + sebi + gst + sell * stt + buy * stamp, 2)


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FnoSettings:
    underlyings: tuple[str, ...]
    instrument: str               # options | futures
    strategies: tuple[str, ...]
    allow_bearish: bool           # buy PEs on bearish setups (options only)
    expiry_index: int
    avoid_expiry_day: bool
    strike_offset: int
    max_spread_pct: float
    risk_per_trade: float         # ₹
    max_lots: int
    max_premium: float            # ₹ premium (or futures margin estimate) per trade
    premium_stop_pct: float
    trail_pct: float
    max_trades_per_day: int
    max_open: int
    max_daily_loss: float
    cooldown: timedelta
    entry_start: time
    entry_end: time
    flatten_at: time
    max_hold: timedelta
    min_reward_to_cost: float
    candle_seconds: int
    tick_max_age_s: float
    rest_poll_s: float
    params: StrategyParams

    @property
    def futures(self) -> bool:
        return self.instrument == "futures"

    @classmethod
    def from_config(cls, config: Any) -> "FnoSettings":
        g = lambda name, default=None: getattr(config, name, default)  # noqa: E731
        strategies = tuple(s.lower() for s in _list(g("fno_strategies"),
                                                     ",".join(STRATEGY_NAMES))
                           if s.lower() in STRATEGY_NAMES) or STRATEGY_NAMES
        instrument = str(g("fno_instrument", "options") or "options").strip().lower()
        if instrument not in INSTRUMENTS:
            instrument = "options"
        entry_start = _hhmm(g("fno_entry_start"), time(9, 30))
        entry_end = _hhmm(g("fno_entry_end"), time(14, 30))
        flatten_at = _hhmm(g("fno_flatten_at"), time(15, 10))
        if not (entry_start < entry_end <= flatten_at <= time(15, 15)):
            entry_start, entry_end, flatten_at = time(9, 30), time(14, 30), time(15, 10)
        return cls(
            underlyings=_list(g("fno_underlyings"), "NIFTY")[:5],
            instrument=instrument,
            strategies=strategies,
            allow_bearish=g("fno_allow_bearish", True) is not False,
            expiry_index=int(_num(g("fno_expiry_index"), 0, 0, 3)),
            avoid_expiry_day=g("fno_avoid_expiry_day", True) is not False,
            strike_offset=int(_num(g("fno_strike_offset"), 0, -5, 5)),
            max_spread_pct=_num(g("fno_max_spread_pct"), 0.03, 0.001, 0.5),
            risk_per_trade=_num(g("fno_risk_per_trade_inr"), 3_000, 50, 1e7),
            max_lots=int(_num(g("fno_max_lots"), 1, 1, 50)),
            max_premium=_num(g("fno_max_premium_inr"), 25_000, 500, 1e8),
            premium_stop_pct=_num(g("fno_premium_stop_pct"), 0.25, 0.05, 0.9),
            trail_pct=_num(g("fno_trail_pct"), 0.15, 0.02, 0.9),
            max_trades_per_day=int(_num(g("fno_max_trades_per_day"), 4, 1, 100)),
            max_open=int(_num(g("fno_max_open"), 1, 1, 10)),
            max_daily_loss=_num(g("fno_max_daily_loss_inr"), 6_000, 100, 1e8),
            cooldown=timedelta(minutes=_num(g("fno_cooldown_minutes"), 10, 0, 240)),
            entry_start=entry_start, entry_end=entry_end, flatten_at=flatten_at,
            max_hold=timedelta(minutes=_num(g("fno_max_hold_minutes"), 30, 1, 375)),
            min_reward_to_cost=_num(g("fno_min_reward_to_cost"), 3.0, 0.5, 50),
            candle_seconds=int(_num(g("fno_candle_seconds"), 60, 15, 900)),
            tick_max_age_s=_num(g("ws_tick_max_age_seconds"), 5.0, 1, 60),
            rest_poll_s=_num(g("fno_rest_poll_seconds"), 3.0, 1, 60),
            params=StrategyParams(
                rr=_num(g("fno_rr"), 2.0, 0.5, 10),
                orb_minutes=int(_num(g("fno_orb_minutes"), 15, 5, 120)),
            ),
        )


# ── Direction: inverted candles ──────────────────────────────────────────────


def inversion_constant(candles: Sequence[Candle]) -> float:
    """``K`` of the day's inversion: the square of the first open, so inverted prices sit
    near the real ones (and every inversion of the day uses the same ``K``)."""
    if not candles or candles[0].open <= 0:
        return 0.0
    return candles[0].open ** 2


def invert_candles(candles: Sequence[Candle], k: float) -> list[Candle]:
    """Each price ``p`` becomes ``k / p``: a falling market rises, its highs become lows.
    The long-only strategies find bearish setups on these."""
    if k <= 0:
        return []
    out = []
    for c in candles:
        if min(c.open, c.high, c.low, c.close) <= 0:
            return []
        out.append(Candle(c.start, k / c.open, k / c.low, k / c.high, k / c.close,
                          c.volume, c.ticks))
    return out


@dataclass(frozen=True)
class Signal:
    """A directional setup on the underlying, in real prices."""

    direction: int        # +1 bullish (CE / future), -1 bearish (PE)
    strategy: str
    entry: float          # underlying reference
    stop: float           # underlying: below entry (bullish) or above (bearish)
    target: float
    reason: str

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward(self) -> float:
        return abs(self.target - self.entry)


def bullish(setup: Setup) -> Signal:
    return Signal(1, setup.strategy, setup.entry, setup.stop, setup.target, setup.reason)


def bearish(setup: Setup, k: float) -> Signal:
    """A setup found on the inverted candles, back in real prices."""
    return Signal(-1, setup.strategy, k / setup.entry, k / setup.stop, k / setup.target,
                  f"bearish {setup.reason}")


# ── Contract choice and size ─────────────────────────────────────────────────


def pick_option(chain: Any, direction: int, offset: int, max_spread_pct: float) -> Any:
    """The CE (bullish) or PE (bearish) ``offset`` strikes from the money (0 ATM, -1 one
    in the money, +1 one out of the money), with a price and a spread within
    ``max_spread_pct`` of it; None when there is none."""
    legs = sorted(chain.calls if direction > 0 else chain.puts, key=lambda c: c.strike)
    if not legs or not chain.spot_price:
        return None
    atm = min(range(len(legs)), key=lambda i: abs(legs[i].strike - chain.spot_price))
    # A call further out of the money has a higher strike; a put a lower one
    i = atm + offset if direction > 0 else atm - offset
    if not 0 <= i < len(legs):
        return None
    leg = legs[i]
    if not leg.security_id or leg.ltp <= 0:
        return None
    if leg.bid > 0 and leg.ask > 0:
        mid = (leg.bid + leg.ask) / 2
        if mid <= 0 or (leg.ask - leg.bid) / mid > max_spread_pct:
            return None
    return leg


def option_entry_price(leg: Any) -> float:
    """What a MARKET BUY may pay: the ask when there is one, else the LTP."""
    return float(leg.ask if getattr(leg, "ask", 0) > 0 else leg.ltp)


def size_lots(*, unit_risk: float, unit_cost: float, lot: int, risk_inr: float,
              max_lots: int, max_outlay: float, safety_max_lots: Optional[int] = None,
              equity: float = 0.0, max_position_pct: float = 0.0) -> int:
    """Whole lots: ``risk_inr`` over the risk per lot, capped by ``max_lots``, the safety
    lot cap, ``max_outlay`` of premium (or margin) and ``max_position_pct`` of
    ``equity``. 0 when not even one lot fits the risk or the outlay."""
    if lot <= 0 or unit_risk <= 0 or unit_cost <= 0:
        return 0
    per_lot_risk = unit_risk * lot
    per_lot_cost = unit_cost * lot
    lots = math.floor(risk_inr / per_lot_risk)
    lots = min(lots, max_lots, math.floor(max_outlay / per_lot_cost))
    if safety_max_lots:
        lots = min(lots, safety_max_lots)
    if equity > 0 and max_position_pct > 0 and lots > 1:
        # One lot is allowed past the percentage (the safety rules' small-account rule)
        lots = max(1, min(lots, math.floor(equity * max_position_pct / per_lot_cost)))
    return max(0, int(lots))


def expected_reward_per_unit(signal: Signal, delta: float, *, future: bool) -> float:
    """The premium (or future price) gain if the underlying reaches the target: the
    underlying move × |delta| (0.5 when the chain sends no delta)."""
    if future:
        return signal.reward
    d = abs(delta) if delta else 0.5
    return signal.reward * min(1.0, max(0.05, d))


# ── Open positions and their exits ───────────────────────────────────────────


@dataclass
class FnoPosition:
    symbol: str             # the contract's trading symbol
    underlying: str
    kind: str               # CE | PE | FUT
    direction: int          # +1 / -1 (the underlying move it profits from)
    security_id: str
    exchange: str           # NSE / BSE (orders); data uses NFO_/BFO_ codes
    scrip_code: str         # the contract's market-data code
    lot_size: int
    qty: int                # units
    entry: float            # premium (or future price) paid
    stop: float             # premium stop
    opened_at: datetime
    strategy: str
    und_stop: Optional[float] = None    # None: adopted after a restart
    und_target: Optional[float] = None
    und_entry: Optional[float] = None
    cost_per_unit: float = 0.0
    high: float = 0.0
    breakeven: bool = False
    initial_stop: float = field(default=0.0)
    exiting: bool = False
    expiry: Optional[date] = None

    def __post_init__(self) -> None:
        if self.high <= 0:
            self.high = self.entry
        if self.initial_stop <= 0:
            self.initial_stop = self.stop

    @property
    def lots(self) -> int:
        return self.qty // max(1, self.lot_size)

    @property
    def risk(self) -> float:
        return max(self.entry - self.initial_stop, 0.05)


def update_trail(pos: FnoPosition, price: float, trail_pct: float) -> None:
    """Raise the high; at +1 R the stop moves to entry + charges, then trails
    ``trail_pct`` below the high (an option's premium) or 1 R below it (a future)."""
    if price > pos.high:
        pos.high = price
    if not pos.breakeven and pos.high - pos.entry >= pos.risk:
        pos.breakeven = True
        pos.stop = max(pos.stop, round(pos.entry + pos.cost_per_unit, 2))
    if pos.breakeven:
        trail = pos.high - pos.risk if pos.kind == "FUT" else pos.high * (1 - trail_pct)
        pos.stop = max(pos.stop, round(trail, 2))


def exit_reason(pos: FnoPosition, price: float, und_price: Optional[float], now: datetime,
                max_hold: timedelta) -> Optional[str]:
    """Why to sell now (``price``: the contract's; ``und_price``: the underlying's)."""
    if price <= pos.stop:
        kind = "TRAIL" if pos.breakeven else "PREMIUM STOP"
        return f"FNO {kind}: {price:.2f} <= {pos.stop:.2f} ({pos.strategy})"
    if und_price and pos.und_stop:
        if (pos.direction > 0 and und_price <= pos.und_stop) or \
                (pos.direction < 0 and und_price >= pos.und_stop):
            return (f"FNO STOP: {pos.underlying} {und_price:.2f} reached the stop "
                    f"{pos.und_stop:.2f} ({pos.strategy})")
    if und_price and pos.und_target:
        if (pos.direction > 0 and und_price >= pos.und_target) or \
                (pos.direction < 0 and und_price <= pos.und_target):
            return (f"FNO TARGET: {pos.underlying} {und_price:.2f} reached the target "
                    f"{pos.und_target:.2f} ({pos.strategy})")
    if now - pos.opened_at >= max_hold and price <= pos.entry + pos.cost_per_unit:
        return f"FNO TIME STOP: no profit after {int(max_hold.total_seconds() // 60)} min"
    return None
