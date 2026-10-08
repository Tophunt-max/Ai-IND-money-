"""Intraday scalping engine: live ticks → candles → strategies → INTRADAY orders.

It runs beside the daemon's swing trading (``scalp_enabled``) or alone
(``skopaq scalp``), from now until ``scalp_flatten_at`` (15:10 IST) or a stop:

1. Seeds today's 1-minute candles of every ``scalp_symbols`` instrument from REST, so the
   indicators are ready at once, and subscribes them to the price feed (REST quotes,
   batched, when the feed is down).
2. On every closed candle, between ``scalp_entry_start`` and ``scalp_entry_end``, asks the
   strategies (``skopaq/scalping/strategies.py``) for a setup. A setup is taken when the
   day's limits allow it (trades, open positions, daily loss, cool-down after a loss, the
   kill switch) and its target pays ``scalp_min_reward_to_cost`` × the round-trip charges.
   Size: ``scalp_risk_per_trade_pct`` of equity over the stop distance, capped at
   ``scalp_max_position_value_inr`` (and by the safety rules).
3. On every price: stop-loss, target, breakeven at 1 R then a one-ATR trail, a time stop
   after ``scalp_max_hold_minutes`` without profit, and at the flatten time everything.

Orders are MARKET, product INTRADAY, through the same Executor → SafetyChecker → order
router as every other order (live: the order worker confirms fills, works exits until
filled, and the SELL lock and journal apply). The scalper owns its INTRADAY positions:
the swing monitor and CLOSING manage CNC rows only, and the scalper never trades a symbol
held in CNC. Its status (positions, trades, P&L per strategy) goes to the dashboard's
control channel as ``scalper``, and the dashboard's scalp closes come to it from there.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

from skopaq.broker.models import (
    OrderType,
    Product,
    Quote,
    TradingSignal,
    filled_quantity_of,
    is_unconfirmed,
)
from skopaq.broker.order_status import is_non_cnc_product
from skopaq.market.candles import Candle, LiveSeries
from skopaq.scalping.costs import round_trip_cost
from skopaq.scalping.rules import ScalpPosition, exit_reason, update_trail
from skopaq.scalping.settings import ScalpSettings
from skopaq.scalping.strategies import Setup, atr_last, evaluate

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_LOOP_S = 1.0
OnTrade = Callable[[TradingSignal, Any], Awaitable[Any]]


def _now_ist() -> datetime:
    return datetime.now(_IST)


@dataclass
class ScalpTrade:
    symbol: str
    strategy: str
    qty: int
    entry: float
    exit: float
    pnl: float           # after estimated charges
    reason: str
    opened_at: str
    closed_at: str


@dataclass
class ScalpReport:
    trades: list[ScalpTrade] = field(default_factory=list)
    entries: int = 0
    rejected: int = 0
    skipped_cost: int = 0
    left_open: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return round(sum(t.pnl for t in self.trades), 2)

    def by_strategy(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for t in self.trades:
            row = out.setdefault(t.strategy, {"trades": 0, "wins": 0, "pnl": 0.0})
            row["trades"] += 1
            row["wins"] += t.pnl > 0
            row["pnl"] = round(row["pnl"] + t.pnl, 2)
        return out

    def summary(self) -> str:
        if not self.trades and not self.entries:
            return "Scalper: no trades"
        wins = sum(t.pnl > 0 for t in self.trades)
        parts = [f"Scalper: {len(self.trades)} trade(s), {wins} win(s), net ₹{self.net_pnl:,.2f}"]
        for name, row in self.by_strategy().items():
            parts.append(f"  {name}: {row['trades']} ({row['wins']} won) ₹{row['pnl']:,.2f}")
        if self.left_open:
            parts.append("  still open: " + ", ".join(self.left_open))
        return "\n".join(parts)


class ScalpEngine:
    def __init__(
        self,
        config: Any,
        executor: Any,
        client: Any,
        router: Any,
        *,
        feed: Any = None,
        settings: Optional[ScalpSettings] = None,
        on_trade: Optional[OnTrade] = None,
        control: Any = None,
        wall: Callable[[], datetime] = _now_ist,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        halted: Optional[Callable[[], bool]] = None,
        max_qty: Optional[int] = None,
    ) -> None:
        """``max_qty``: the safety rules' share cap (``max_shares_per_position``) — a bigger
        order would only be refused, so scalps are sized within it."""
        self.config = config
        self.settings = settings or ScalpSettings.from_config(config)
        self._executor = executor
        self._client = client
        self._router = router
        self._feed = feed
        self._on_trade = on_trade
        self._control = control
        self._wall = wall
        self._sleep = sleep
        self._halted = halted or _kill_switch_on
        self._paper = getattr(router, "mode", "paper") != "live"
        self.series: dict[str, LiveSeries] = {}
        self.codes: dict[str, str] = {}           # symbol -> scrip code
        self.prices: dict[str, float] = {}
        self.positions: dict[str, ScalpPosition] = {}
        self.report = ScalpReport()
        self._pending: set[str] = set()           # symbols with a closed candle to evaluate
        self._blocked: set[str] = set()           # unconfirmed orders: no more trades today
        self._orb_done: set[str] = set()
        self._last_loss_at: Optional[datetime] = None
        self._rest_at = -math.inf
        self._max_qty = max_qty if isinstance(max_qty, int) and max_qty > 0 else None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def run(self, stop: asyncio.Event) -> ScalpReport:
        s = self.settings
        logger.info("Scalper: %s on %s (%s–%s, flatten %s)", ",".join(s.strategies),
                    ",".join(s.symbols), s.entry_start, s.entry_end, s.flatten_at)
        try:
            await self._prepare()
            while not stop.is_set():
                now = self._wall().astimezone(_IST)
                if now.time() >= s.flatten_at:
                    break
                await self._refresh_prices()
                await self._service_control()
                await self._manage_exits(now)
                await self._entries(now)
                self._publish()
                await self._sleep(_LOOP_S)
        except Exception as exc:
            logger.exception("Scalper failed")
            self.report.errors.append(str(exc))
        finally:
            await self._flatten("SCALP FLATTEN" if not stop.is_set() else "SCALP STOP")
            self.report.left_open = sorted(self.positions)
            self._publish(ended=True)
        logger.info(self.report.summary())
        return self.report

    async def _prepare(self) -> None:
        from skopaq.broker.scrip_resolver import resolve_scrip_code

        for symbol in self.settings.symbols:
            try:
                self.codes[symbol] = await resolve_scrip_code(self._client, symbol)
            except Exception:
                logger.warning("Scalper: %s not found — skipped", symbol)
                continue
            self.series[symbol] = LiveSeries(self.settings.candle_seconds)
            await self._seed(symbol)
        if self._feed is not None:
            listen = getattr(self._feed, "add_listener", None)
            if listen is not None:
                listen(self._on_tick)
            try:
                await self._feed.subscribe(list(self.codes.values()))
            except Exception:
                logger.warning("Scalper: price feed subscription failed — REST quotes",
                               exc_info=True)
        await self._adopt()

    async def _seed(self, symbol: str) -> None:
        """Today's candles so far (1-minute history from REST)."""
        if self.settings.candle_seconds != 60:
            return
        now = self._wall().astimezone(_IST)
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now <= start:
            return
        try:
            rows = await self._client.get_historical(
                self.codes[symbol], interval="1minute",
                start_time=int(start.timestamp() * 1000), end_time=int(now.timestamp() * 1000))
        except Exception:
            logger.warning("Scalper: no history for %s — indicators warm up live", symbol)
            return
        candles = []
        for r in sorted(rows, key=lambda r: r.timestamp):
            ts = r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc)
            candles.append(Candle(ts.astimezone(_IST).replace(second=0, microsecond=0),
                                  r.open, r.high, r.low, r.close, r.volume or None))
        # The minute in progress is built from ticks
        self.series[symbol].seed([c for c in candles
                                  if c.start + timedelta(minutes=1) <= now])

    async def _adopt(self) -> None:
        """INTRADAY positions already held in the watchlist (a restart): manage them with
        a conservative stop and target."""
        try:
            rows = await self._router.get_positions()
        except Exception:
            return
        now = self._wall().astimezone(_IST)
        for row in rows:
            symbol = (row.symbol or "").upper()
            product = (getattr(row, "product", "") or "").upper()
            if symbol not in self.codes or product != "INTRADAY" or row.quantity <= 0:
                continue
            entry = float(row.average_price or 0)
            if entry <= 0:
                continue
            atr = atr_last(self.series[symbol].candles) or entry * 0.003
            self.positions[symbol] = ScalpPosition(
                symbol=symbol, strategy="adopted", qty=int(row.quantity), entry=entry,
                stop=round(entry - max(atr, entry * 0.004), 2),
                target=round(entry + 1.5 * max(atr, entry * 0.004), 2), opened_at=now,
                atr=atr, scrip_code=self.codes[symbol],
                cost_per_share=round_trip_cost(entry, entry, int(row.quantity))
                / max(1, int(row.quantity)))
            logger.warning("Scalper: adopted %s %s @ %.2f", row.quantity, symbol, entry)

    # ── Prices and candles ───────────────────────────────────────────────

    def _on_tick(self, tick) -> None:
        symbol = next((s for s, c in self.codes.items() if c == tick.scrip_code), None)
        if symbol is None:
            return
        ts = tick.timestamp or self._wall()
        self._price(symbol, tick.ltp, ts, tick.volume)

    def _price(self, symbol: str, price: float, ts: datetime,
               volume: Optional[int] = None) -> None:
        self.prices[symbol] = price
        closed = self.series[symbol].add(price, ts, volume)
        if closed is not None:
            self._pending.add(symbol)

    async def _refresh_prices(self) -> None:
        """Fresh feed ticks are used as they come; otherwise one batched REST quote call
        every ``scalp_rest_poll_seconds``."""
        stale = [s for s, c in self.codes.items()
                 if self._feed is None or not self._feed.ltp(c, self.settings.tick_max_age_s)]
        if not stale:
            return
        wall_now = self._wall().timestamp()
        if wall_now - self._rest_at < self.settings.rest_poll_s:
            return
        self._rest_at = wall_now
        try:
            quotes = await self._client.get_quotes([self.codes[s] for s in stale],
                                                   symbols=stale)
        except Exception:
            logger.warning("Scalper: REST quotes failed", exc_info=True)
            return
        now = self._wall()
        for q in quotes:
            if q.symbol in self.series and q.ltp > 0:
                self._price(q.symbol, float(q.ltp), now,
                            int(q.volume) if getattr(q, "volume", None) else None)

    # ── Exits ────────────────────────────────────────────────────────────

    async def _manage_exits(self, now: datetime) -> None:
        for symbol, pos in list(self.positions.items()):
            price = self.prices.get(symbol)
            if not price or pos.exiting:
                continue
            update_trail(pos, price)
            reason = exit_reason(pos, price, now, self.settings.max_hold)
            if reason:
                await self._exit(pos, price, reason)

    async def _flatten(self, reason: str) -> None:
        for pos in list(self.positions.values()):
            price = self.prices.get(pos.symbol) or pos.entry
            for _ in range(2):          # a short fill: try the rest once more
                if pos.symbol not in self.positions:
                    break
                await self._exit(pos, price, reason)

    async def _exit(self, pos: ScalpPosition, price: float, reason: str) -> None:
        pos.exiting = True
        signal = self._signal(pos.symbol, "SELL", price, pos.qty, reason)
        try:
            result = await self._execute(signal, price)
        finally:
            pos.exiting = False
        if not result.success:
            logger.error("Scalper: exit of %s refused: %s", pos.symbol, result.rejection_reason)
            return
        sold = int(filled_quantity_of(result, pos.qty))
        fill = float(result.fill_price or price)
        cost = round_trip_cost(pos.entry, fill, sold)
        pnl = round((fill - pos.entry) * sold - cost, 2)
        now = self._wall().astimezone(_IST)
        self.report.trades.append(ScalpTrade(
            pos.symbol, pos.strategy, sold, pos.entry, fill, pnl, reason,
            pos.opened_at.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
        if pnl < 0:
            self._last_loss_at = now
        pos.qty -= sold
        if pos.qty <= 0:
            self.positions.pop(pos.symbol, None)
        logger.info("Scalper: SOLD %d %s @ %.2f — %s (net ₹%.2f)", sold, pos.symbol, fill,
                    reason, pnl)

    # ── Entries ──────────────────────────────────────────────────────────

    def _daily_pnl(self) -> float:
        return sum(t.pnl for t in self.report.trades)

    def _can_enter(self, now: datetime) -> Optional[str]:
        s = self.settings
        if not (s.entry_start <= now.time() < s.entry_end):
            return "outside the entry window"
        if self.report.entries >= s.max_trades_per_day:
            return "max trades today"
        if len(self.positions) >= s.max_open:
            return "max open scalps"
        if self._daily_pnl() <= -s.max_daily_loss:
            return "daily loss limit"
        if self._last_loss_at is not None and now - self._last_loss_at < s.cooldown:
            return "cool-down after a loss"
        if self._halted():
            return "trading is paused (kill switch)"
        return None

    async def _entries(self, now: datetime) -> None:
        pending, self._pending = self._pending, set()
        if not pending or self._can_enter(now) is not None:
            return
        swing = await self._swing_symbols()
        for symbol in sorted(pending):
            if self._can_enter(now) is not None:
                return
            if symbol in self.positions or symbol in self._blocked or symbol in swing:
                continue
            names = [n for n in self.settings.strategies
                     if not (n == "orb" and symbol in self._orb_done)]
            setup = evaluate(self.series[symbol].candles, names, self.settings.params)
            if setup is not None:
                await self._enter(symbol, setup, now)

    async def _swing_symbols(self) -> set[str]:
        """Symbols held in CNC (the swing book's): the scalper keeps out of them."""
        try:
            rows = await self._router.get_positions()
        except Exception:
            return set()
        return {(r.symbol or "").upper() for r in rows
                if r.quantity > 0 and not is_non_cnc_product(getattr(r, "product", ""))}

    async def _size(self, setup: Setup) -> int:
        try:
            funds = await self._router.get_funds()
            equity = float(funds.total_collateral or funds.available_cash or 0)
        except Exception:
            equity = 0.0
        if equity <= 0 or setup.risk <= 0:
            return 0
        qty = math.floor(equity * self.settings.risk_per_trade_pct / setup.risk)
        qty = min(qty, math.floor(self.settings.max_position_value / setup.entry))
        if self._max_qty is not None:
            qty = min(qty, self._max_qty)
        return max(0, int(qty))

    async def _enter(self, symbol: str, setup: Setup, now: datetime) -> None:
        qty = await self._size(setup)
        if qty <= 0:
            return
        cost = round_trip_cost(setup.entry, setup.target, qty)
        reward = setup.reward * qty
        if reward < self.settings.min_reward_to_cost * cost:
            self.report.skipped_cost += 1
            logger.info("Scalper: %s %s skipped — target ₹%.0f < %.1f × charges ₹%.0f",
                        setup.strategy, symbol, reward, self.settings.min_reward_to_cost,
                        cost)
            return
        price = self.prices.get(symbol) or setup.entry
        signal = self._signal(symbol, "BUY", price, qty,
                              f"SCALP {setup.strategy}: {setup.reason}", stop=setup.stop)
        result = await self._execute(signal, price)
        if setup.strategy == "orb":
            self._orb_done.add(symbol)
        if not result.success:
            self.report.rejected += 1
            if is_unconfirmed(result):
                self._blocked.add(symbol)
                logger.error("Scalper: BUY %s unconfirmed — no more %s scalps today", symbol,
                             symbol)
            else:
                logger.info("Scalper: BUY %s refused: %s", symbol, result.rejection_reason)
            return
        filled = int(filled_quantity_of(result, qty))
        if filled <= 0:
            return
        fill = float(result.fill_price or price)
        shift = fill - setup.entry                 # keep the setup's distances from the fill
        atr = atr_last(self.series[symbol].candles) or setup.risk
        self.positions[symbol] = ScalpPosition(
            symbol=symbol, strategy=setup.strategy, qty=filled, entry=fill,
            stop=round(setup.stop + shift, 2), target=round(setup.target + shift, 2),
            opened_at=now, atr=atr, scrip_code=self.codes[symbol],
            cost_per_share=round(round_trip_cost(fill, setup.target, filled) / filled, 2))
        self.report.entries += 1
        logger.info("Scalper: BOUGHT %d %s @ %.2f (%s) stop %.2f target %.2f", filled, symbol,
                    fill, setup.strategy, setup.stop + shift, setup.target + shift)

    # ── Orders ───────────────────────────────────────────────────────────

    def _signal(self, symbol: str, side: str, price: float, qty: int, reason: str, *,
                stop: Optional[float] = None) -> TradingSignal:
        return TradingSignal(symbol=symbol, action=side, confidence=70, entry_price=price,
                             order_type=OrderType.MARKET, quantity=Decimal(qty),
                             stop_loss=stop, reasoning=reason, product=Product.INTRADAY)

    async def _execute(self, signal: TradingSignal, price: float):
        if self._paper:
            paper = getattr(self._router, "_paper", None)
            if paper is not None:
                paper.update_quote(Quote(symbol=signal.symbol, ltp=price, bid=price * 0.9995,
                                         ask=price * 1.0005))
        result = await self._executor.execute_signal(signal)
        if result.success and self._on_trade is not None:
            try:
                await self._on_trade(signal, result)
            except Exception:
                logger.warning("Scalper: trade row of %s not written", signal.symbol,
                               exc_info=True)
        return result

    # ── Dashboard ────────────────────────────────────────────────────────

    async def _service_control(self) -> None:
        if self._control is None:
            return
        try:
            commands = self._control.claim(target="scalper")
        except Exception:
            return
        for cmd in commands:
            kind = cmd.get("kind")
            symbol = str((cmd.get("payload") or {}).get("symbol") or "").upper()
            by = cmd.get("by") or "dashboard"
            if kind == "close_all":
                n = len(self.positions)
                for pos in list(self.positions.values()):
                    await self._exit(pos, self.prices.get(pos.symbol) or pos.entry,
                                     f"SCALP MANUAL CLOSE ALL ({by})")
                ok, msg = True, f"closed {n} scalp(s)"
            elif kind == "close" and symbol in self.positions:
                pos = self.positions[symbol]
                await self._exit(pos, self.prices.get(symbol) or pos.entry,
                                 f"SCALP MANUAL CLOSE ({by})")
                ok, msg = symbol not in self.positions, f"close {symbol}: " + (
                    "done" if symbol not in self.positions else "not fully sold")
            else:
                ok, msg = False, f"no open scalp {symbol}" if kind == "close" else \
                    f"the scalper does not take {kind!r}"
            self._control.complete(str(cmd.get("id")), ok=ok, message=msg)

    def _publish(self, *, ended: bool = False) -> None:
        if self._control is None:
            return
        rows = []
        for pos in self.positions.values():
            price = self.prices.get(pos.symbol)
            rows.append({
                "symbol": pos.symbol, "strategy": pos.strategy, "quantity": pos.qty,
                "entry_price": pos.entry, "ltp": price, "stop_loss": pos.stop,
                "target": pos.target, "breakeven": pos.breakeven, "high": pos.high,
                "pnl": round((price - pos.entry) * pos.qty, 2) if price else None,
                "opened_at": pos.opened_at.isoformat(timespec="seconds"),
            })
        s = self.settings
        self._control.write_status("scalper", {
            "ended": ended, "mode": "paper" if self._paper else "live",
            "positions": rows, "trades": [t.__dict__ for t in self.report.trades[-30:]],
            "entries": self.report.entries, "net_pnl": self.report.net_pnl,
            "by_strategy": self.report.by_strategy(),
            "skipped_cost": self.report.skipped_cost, "rejected": self.report.rejected,
            "symbols": list(self.codes), "strategies": list(s.strategies),
            "window": f"{s.entry_start:%H:%M}–{s.entry_end:%H:%M}, flatten {s.flatten_at:%H:%M}",
            "limits": {"max_trades": s.max_trades_per_day, "max_open": s.max_open,
                       "max_daily_loss": s.max_daily_loss},
            "blocked": self._can_enter(self._wall().astimezone(_IST)),
        })


def _kill_switch_on() -> bool:
    try:
        from skopaq.execution import kill_switch

        return kill_switch.status().halted
    except Exception:
        return False
