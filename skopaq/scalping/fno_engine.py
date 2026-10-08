"""F&O intraday engine: index candles → strategies → option (or future) BUYs on INDstocks.

It runs beside the daemon's swing trading (``fno_enabled``) or alone (``skopaq fno``),
from now until ``fno_flatten_at`` (15:10 IST) or a stop:

1. Resolves every ``fno_underlyings`` name (NIFTY, BANKNIFTY, ... or an F&O stock) to its
   market-data code, seeds today's 1-minute candles from REST and subscribes the price
   feed (REST quotes, batched, when the feed is down).
2. On every closed candle, inside ``fno_entry_start``–``fno_entry_end``, asks the scalping
   strategies for a setup: a bullish one buys a CE (``fno_instrument=options``) or the
   near-month future (``futures``); a bearish one (the same strategies on the inverted
   candles, ``fno_allow_bearish``) buys a PE. Never a SELL to open: no option writing,
   no short futures (the safety checker refuses those too).
3. Picks the contract from the live option chain (``skopaq/scalping/fno_rules.py``), sizes
   it in whole lots and takes it when the day's limits allow it and the expected reward
   pays ``fno_min_reward_to_cost`` × the round-trip charges.
4. On every price: the underlying's stop and target, the premium stop (breakeven at +1 R,
   then trailing), a time stop, and at the flatten time everything.

Orders are MARKET, segment DERIVATIVE, product INTRADAY, with the contract's security id
and lot size, through the same Executor → SafetyChecker → order router as every other
order (live: the order worker confirms fills and works exits until filled; the SELL lock,
the order book check and the journal apply). Its status goes to the dashboard's control
channel as ``fno``; the dashboard's F&O closes come to it from there.

Futures: an index future's notional (NIFTY ≈ ₹18 lakh a lot) is far above the safety
rules' ``max_order_value_inr`` and ``max_position_pct``, which are immutable — such orders
are refused. Stock futures with a small lot value can pass.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

from skopaq.broker.models import (
    Exchange,
    OrderType,
    Product,
    Quote,
    Segment,
    TradingSignal,
    derivative_scrip_code,
    filled_quantity_of,
    is_unconfirmed,
)
from skopaq.execution.safety_checker import FUTURES_MARGIN_ESTIMATE
from skopaq.market.candles import Candle, LiveSeries
from skopaq.scalping.engine import ScalpReport, ScalpTrade, _kill_switch_on
from skopaq.scalping.fno_rules import (
    FnoPosition,
    FnoSettings,
    Signal,
    bearish,
    bullish,
    exit_reason,
    expected_reward_per_unit,
    fno_round_trip_cost,
    inversion_constant,
    invert_candles,
    option_entry_price,
    pick_option,
    size_lots,
    update_trail,
)
from skopaq.scalping.strategies import evaluate

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_LOOP_S = 1.0
_TICK = 0.05
OnTrade = Callable[[TradingSignal, Any], Awaitable[Any]]


def _now_ist() -> datetime:
    return datetime.now(_IST)


def _tick_down(price: float) -> float:
    return round(math.floor(price / _TICK + 1e-9) * _TICK, 2)


def underlying_code(underlying: Any) -> str:
    """Market-data code of an underlying: ``NIDX_<id>`` / ``BIDX_<id>`` for an index,
    ``NSE_<id>`` for a stock."""
    if underlying.is_index:
        prefix = "BIDX" if underlying.exchange == "BSE" else "NIDX"
    else:
        prefix = underlying.exchange
    return f"{prefix}_{underlying.security_id}"


@dataclass
class _Underlying:
    name: str
    exchange: str          # NSE / BSE (orders)
    code: str              # market data
    series: LiveSeries
    k: float = 0.0         # the day's inversion constant (bearish setups)


@dataclass
class FnoReport(ScalpReport):
    skipped_size: int = 0
    skipped_contract: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.trades and not self.entries:
            return "F&O: no trades" + (f" ({self.notes[-1]})" if self.notes else "")
        wins = sum(t.pnl > 0 for t in self.trades)
        parts = [f"F&O: {len(self.trades)} trade(s), {wins} win(s), net ₹{self.net_pnl:,.2f}"]
        for name, row in self.by_strategy().items():
            parts.append(f"  {name}: {row['trades']} ({row['wins']} won) ₹{row['pnl']:,.2f}")
        if self.left_open:
            parts.append("  still open: " + ", ".join(self.left_open))
        return "\n".join(parts)


class FnoEngine:
    def __init__(
        self,
        config: Any,
        executor: Any,
        client: Any,
        router: Any,
        *,
        feed: Any = None,
        settings: Optional[FnoSettings] = None,
        on_trade: Optional[OnTrade] = None,
        control: Any = None,
        wall: Callable[[], datetime] = _now_ist,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        halted: Optional[Callable[[], bool]] = None,
        max_lots: Optional[int] = None,
        max_position_pct: float = 0.0,
        resolver: Optional[Callable[..., Awaitable[Any]]] = None,
        chain_loader: Optional[Callable[..., Awaitable[Any]]] = None,
        future_loader: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
        """``max_lots``: the safety checker's lot limit (``max_lots``);
        ``max_position_pct``: theirs too — a bigger order would only be refused."""
        from skopaq.broker import fno
        from skopaq.options.chain import fetch_option_chain

        self.config = config
        self.settings = settings or FnoSettings.from_config(config)
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
        self._max_lots = max_lots if isinstance(max_lots, int) and max_lots > 0 else None
        self._max_position_pct = max_position_pct if max_position_pct > 0 else 0.0
        self._resolve = resolver or fno.resolve_underlying
        self._load_chain = chain_loader or fetch_option_chain
        self._load_future = future_loader or fno.nearest_future
        self.unders: dict[str, _Underlying] = {}
        self.prices: dict[str, float] = {}         # scrip code → last price
        self.positions: dict[str, FnoPosition] = {}
        self.report = FnoReport()
        self._pending: set[str] = set()            # underlyings with a closed candle
        self._blocked: set[str] = set()            # unconfirmed orders: none more today
        self._orb_done: set[tuple[str, int]] = set()
        self._last_loss_at: Optional[datetime] = None
        self._rest_at = -math.inf

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def run(self, stop: asyncio.Event) -> FnoReport:
        s = self.settings
        logger.info("F&O engine: %s %s on %s (%s–%s, flatten %s)", s.instrument,
                    ",".join(s.strategies), ",".join(s.underlyings), s.entry_start,
                    s.entry_end, s.flatten_at)
        if s.futures:
            self.report.notes.append(
                "futures: an index future's notional is above the safety rules' order-value "
                "and position limits — such orders are refused")
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
            logger.exception("F&O engine failed")
            self.report.errors.append(str(exc))
        finally:
            await self._flatten("FNO FLATTEN" if not stop.is_set() else "FNO STOP")
            self.report.left_open = sorted(self.positions)
            self._publish(ended=True)
        logger.info(self.report.summary())
        return self.report

    async def _prepare(self) -> None:
        for name in self.settings.underlyings:
            try:
                und = await self._resolve(self._client, name)
            except Exception as exc:
                logger.warning("F&O: underlying %s not found (%s) — skipped", name, exc)
                continue
            self.unders[und.symbol] = _Underlying(
                und.symbol, und.exchange, underlying_code(und),
                LiveSeries(self.settings.candle_seconds))
            await self._seed(self.unders[und.symbol])
        if self._feed is not None:
            listen = getattr(self._feed, "add_listener", None)
            if listen is not None:
                listen(self._on_tick)
            await self._subscribe([u.code for u in self.unders.values()])
        await self._adopt()

    async def _subscribe(self, codes: list[str]) -> None:
        if self._feed is None or not codes:
            return
        try:
            await self._feed.subscribe(codes)
        except Exception:
            logger.warning("F&O: price feed subscription failed — REST quotes",
                           exc_info=True)

    async def _seed(self, und: _Underlying) -> None:
        """Today's candles so far (1-minute history from REST)."""
        if self.settings.candle_seconds != 60:
            return
        now = self._wall().astimezone(_IST)
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now <= start:
            return
        try:
            rows = await self._client.get_historical(
                und.code, interval="1minute",
                start_time=int(start.timestamp() * 1000), end_time=int(now.timestamp() * 1000))
        except Exception:
            logger.warning("F&O: no history for %s — indicators warm up live", und.name)
            return
        candles = []
        for r in sorted(rows, key=lambda r: r.timestamp):
            ts = r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc)
            candles.append(Candle(ts.astimezone(_IST).replace(second=0, microsecond=0),
                                  r.open, r.high, r.low, r.close, r.volume or None))
        und.series.seed([c for c in candles if c.start + timedelta(minutes=1) <= now])

    async def _adopt(self) -> None:
        """INTRADAY F&O positions of the watched underlyings already held (a restart):
        managed with the premium stop only (the setup's levels are gone)."""
        getter = getattr(self._router, "get_derivative_positions", None)
        if getter is None:
            return
        try:
            rows = await getter()
        except Exception:
            logger.warning("F&O: positions unreadable — nothing adopted", exc_info=True)
            return
        now = self._wall().astimezone(_IST)
        for row in rows:
            symbol = (row.symbol or "").upper()
            product = (getattr(row, "product", "") or "").upper()
            sid = str(getattr(row, "security_id", "") or "")
            entry = float(row.average_price or 0)
            und = next((u for u in self.unders.values()
                        if re.match(rf"^{re.escape(u.name)}(?![A-Z])", symbol)), None)
            if und is None or product != "INTRADAY" or row.quantity <= 0 or entry <= 0 \
                    or not sid:
                continue
            kind = "CE" if symbol.endswith("CE") else "PE" if symbol.endswith("PE") else "FUT"
            lot = int(getattr(row, "lot_size", 0) or 0) or 1
            qty = int(row.quantity)
            stop = (entry * (1 - self.settings.premium_stop_pct) if kind != "FUT"
                    else entry * 0.99)
            self.positions[symbol] = FnoPosition(
                symbol=symbol, underlying=und.name, kind=kind,
                direction=-1 if kind == "PE" else 1, security_id=sid,
                exchange=und.exchange, scrip_code=derivative_scrip_code(und.exchange, sid),
                lot_size=lot, qty=qty, entry=entry, stop=_tick_down(stop), opened_at=now,
                strategy="adopted",
                cost_per_unit=fno_round_trip_cost(entry, entry, qty, future=kind == "FUT")
                / max(1, qty))
            logger.warning("F&O: adopted %s %s @ %.2f", qty, symbol, entry)
        await self._subscribe([p.scrip_code for p in self.positions.values()])

    # ── Prices and candles ───────────────────────────────────────────────

    def _on_tick(self, tick) -> None:
        code = tick.scrip_code
        und = next((u for u in self.unders.values() if u.code == code), None)
        if und is not None:
            self._und_price(und, tick.ltp, tick.timestamp or self._wall(), tick.volume)
        elif any(p.scrip_code == code for p in self.positions.values()):
            self.prices[code] = tick.ltp

    def _und_price(self, und: _Underlying, price: float, ts: datetime,
                   volume: Optional[int] = None) -> None:
        self.prices[und.code] = price
        closed = und.series.add(price, ts, volume)
        if closed is not None:
            self._pending.add(und.name)

    async def _refresh_prices(self) -> None:
        """Fresh feed ticks are used as they come; otherwise one batched REST quote call
        every ``fno_rest_poll_seconds`` for the underlyings and the open contracts."""
        codes = [u.code for u in self.unders.values()]
        codes += [p.scrip_code for p in self.positions.values()]
        stale = [c for c in dict.fromkeys(codes)
                 if self._feed is None or not self._feed.ltp(c, self.settings.tick_max_age_s)]
        if self._feed is not None:
            for c in codes:
                if c not in stale:
                    fresh = self._feed.ltp(c, self.settings.tick_max_age_s)
                    if fresh and all(u.code != c for u in self.unders.values()):
                        self.prices[c] = float(fresh)
        if not stale:
            return
        wall_now = self._wall().timestamp()
        if wall_now - self._rest_at < self.settings.rest_poll_s:
            return
        self._rest_at = wall_now
        try:
            quotes = await self._client.get_quotes(stale, symbols=stale)
        except Exception:
            logger.warning("F&O: REST quotes failed", exc_info=True)
            return
        now = self._wall()
        for q in quotes:
            if q.ltp <= 0:
                continue
            und = next((u for u in self.unders.values() if u.code == q.symbol), None)
            if und is not None:
                self._und_price(und, float(q.ltp), now)
            else:
                self.prices[q.symbol] = float(q.ltp)

    # ── Exits ────────────────────────────────────────────────────────────

    async def _manage_exits(self, now: datetime) -> None:
        for pos in list(self.positions.values()):
            price = self.prices.get(pos.scrip_code)
            if not price or pos.exiting:
                continue
            und = self.unders.get(pos.underlying)
            und_price = self.prices.get(und.code) if und else None
            update_trail(pos, price, self.settings.trail_pct)
            reason = exit_reason(pos, price, und_price, now, self.settings.max_hold)
            if reason:
                await self._exit(pos, price, reason)

    async def _flatten(self, reason: str) -> None:
        for pos in list(self.positions.values()):
            price = self.prices.get(pos.scrip_code) or pos.entry
            for _ in range(2):          # a short fill: try the rest once more
                if pos.symbol not in self.positions:
                    break
                await self._exit(pos, price, reason)

    async def _exit(self, pos: FnoPosition, price: float, reason: str) -> None:
        pos.exiting = True
        signal = self._signal(pos.symbol, "SELL", price, pos.qty, reason,
                              security_id=pos.security_id, lot=pos.lot_size,
                              exchange=pos.exchange)
        signal.position_only = True
        try:
            result = await self._execute(signal, price)
        finally:
            pos.exiting = False
        if not result.success:
            logger.error("F&O: exit of %s refused: %s", pos.symbol, result.rejection_reason)
            return
        sold = int(filled_quantity_of(result, pos.qty))
        fill = float(result.fill_price or price)
        cost = fno_round_trip_cost(pos.entry, fill, sold, future=pos.kind == "FUT")
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
        logger.info("F&O: SOLD %d %s @ %.2f — %s (net ₹%.2f)", sold, pos.symbol, fill,
                    reason, pnl)

    # ── Entries ──────────────────────────────────────────────────────────

    def _daily_pnl(self) -> float:
        return sum(t.pnl for t in self.report.trades)

    def _can_enter(self, now: datetime) -> Optional[str]:
        s = self.settings
        if not (s.entry_start <= now.time() < s.entry_end):
            return "outside the entry window"
        if self.report.entries >= s.max_trades_per_day:
            return "max F&O trades today"
        if len(self.positions) >= s.max_open:
            return "max open F&O positions"
        if self._daily_pnl() <= -s.max_daily_loss:
            return "daily loss limit"
        if self._last_loss_at is not None and now - self._last_loss_at < s.cooldown:
            return "cool-down after a loss"
        if self._halted():
            return "trading is paused (kill switch)"
        return None

    def find_signal(self, und: _Underlying) -> Optional[Signal]:
        """The bullish setup on the candles, else (options, ``fno_allow_bearish``) the
        bearish one on the inverted candles."""
        s = self.settings
        candles = und.series.candles
        names = [n for n in s.strategies if not (n == "orb" and (und.name, 1) in self._orb_done)]
        setup = evaluate(candles, names, s.params)
        if setup is not None:
            return bullish(setup)
        if s.futures or not s.allow_bearish:
            return None
        if und.k <= 0:
            und.k = inversion_constant(candles)
        inverted = invert_candles(candles, und.k)
        if not inverted:
            return None
        names = [n for n in s.strategies
                 if not (n == "orb" and (und.name, -1) in self._orb_done)]
        setup = evaluate(inverted, names, s.params)
        return bearish(setup, und.k) if setup is not None else None

    async def _entries(self, now: datetime) -> None:
        pending, self._pending = self._pending, set()
        if not pending or self._can_enter(now) is not None:
            return
        for name in sorted(pending):
            if self._can_enter(now) is not None:
                return
            if name in self._blocked or any(p.underlying == name
                                            for p in self.positions.values()):
                continue
            und = self.unders[name]
            sig = self.find_signal(und)
            if sig is not None:
                await self._enter(und, sig, now)

    async def _equity(self) -> float:
        try:
            funds = await self._router.get_funds()
            return float(funds.total_collateral or funds.available_cash or 0)
        except Exception:
            return 0.0

    async def _contract(self, und: _Underlying, sig: Signal) -> Optional[dict]:
        """The contract to buy for ``sig``: symbol, ids, lot, price, unit risk and cost,
        stop, delta. None (logged) when there is none fit to trade."""
        s = self.settings
        if s.futures:
            fut = await self._load_future(self._client, und.name)
            code = derivative_scrip_code(und.exchange, fut.security_id)
            price = float(await self._client.get_ltp(code) or 0)
            if price <= 0:
                return None
            return dict(symbol=fut.trading_symbol.upper(), security_id=fut.security_id,
                        lot=max(1, int(fut.lot_size or 1)), price=price, kind="FUT",
                        unit_risk=sig.risk, unit_cost=price * FUTURES_MARGIN_ESTIMATE,
                        stop=_tick_down(price - sig.risk), delta=1.0, code=code,
                        expiry=_date(fut.expiry))
        chain = await self._load_chain(self._client, und.name, s.expiry_index, 8)
        today = self._wall().astimezone(_IST).date()
        if s.avoid_expiry_day and chain.expiry == today and len(chain.expiries) > 1:
            chain = await self._load_chain(self._client, und.name, s.expiry_index + 1, 8)
        leg = pick_option(chain, sig.direction, s.strike_offset, s.max_spread_pct)
        if leg is None:
            return None
        price = option_entry_price(leg)
        risk = price * s.premium_stop_pct
        return dict(symbol=leg.tradingsymbol.upper(), security_id=leg.security_id,
                    lot=max(1, int(leg.lot_size or 1)), price=price, kind=leg.option_type,
                    unit_risk=risk, unit_cost=price, stop=_tick_down(price - risk),
                    delta=leg.delta, code=derivative_scrip_code(und.exchange, leg.security_id),
                    expiry=chain.expiry)

    async def _enter(self, und: _Underlying, sig: Signal, now: datetime) -> None:
        s = self.settings
        if sig.strategy == "orb":
            self._orb_done.add((und.name, sig.direction))
        try:
            c = await self._contract(und, sig)
        except Exception as exc:
            logger.warning("F&O: no contract for %s (%s)", und.name, exc)
            c = None
        if c is None:
            self.report.skipped_contract += 1
            logger.info("F&O: %s %s setup skipped — no liquid contract", sig.strategy,
                        und.name)
            return
        lots = size_lots(unit_risk=c["unit_risk"], unit_cost=c["unit_cost"], lot=c["lot"],
                         risk_inr=s.risk_per_trade, max_lots=s.max_lots,
                         max_outlay=s.max_premium, safety_max_lots=self._max_lots,
                         equity=await self._equity(),
                         max_position_pct=self._max_position_pct)
        if lots <= 0:
            self.report.skipped_size += 1
            logger.info("F&O: %s skipped — one lot (%d × %.2f) exceeds the risk ₹%.0f or "
                        "the outlay ₹%.0f", c["symbol"], c["lot"], c["price"],
                        s.risk_per_trade, s.max_premium)
            return
        qty = lots * c["lot"]
        reward = expected_reward_per_unit(sig, c["delta"], future=s.futures)
        cost = fno_round_trip_cost(c["price"], c["price"] + reward, qty, future=s.futures)
        if reward * qty < s.min_reward_to_cost * cost:
            self.report.skipped_cost += 1
            logger.info("F&O: %s skipped — expected ₹%.0f < %.1f × charges ₹%.0f",
                        c["symbol"], reward * qty, s.min_reward_to_cost, cost)
            return
        side = "CE" if sig.direction > 0 else "PE"
        what = "future" if s.futures else side
        signal = self._signal(
            c["symbol"], "BUY", c["price"], qty,
            f"FNO {sig.strategy} {what} on {und.name}: {sig.reason}", stop=c["stop"],
            security_id=c["security_id"], lot=c["lot"], exchange=und.exchange)
        self.prices[c["code"]] = c["price"]
        result = await self._execute(signal, c["price"])
        if not result.success:
            self.report.rejected += 1
            if is_unconfirmed(result):
                self._blocked.add(und.name)
                logger.error("F&O: BUY %s unconfirmed — no more %s trades today",
                             c["symbol"], und.name)
            else:
                logger.info("F&O: BUY %s refused: %s", c["symbol"], result.rejection_reason)
            return
        filled = int(filled_quantity_of(result, qty))
        if filled <= 0:
            return
        fill = float(result.fill_price or c["price"])
        stop = _tick_down(fill - c["unit_risk"]) if s.futures else \
            _tick_down(fill * (1 - s.premium_stop_pct))
        self.positions[c["symbol"]] = FnoPosition(
            symbol=c["symbol"], underlying=und.name, kind=c["kind"],
            direction=sig.direction, security_id=c["security_id"], exchange=und.exchange,
            scrip_code=c["code"], lot_size=c["lot"], qty=filled, entry=fill, stop=stop,
            opened_at=now, strategy=sig.strategy, und_stop=round(sig.stop, 2),
            und_target=round(sig.target, 2), und_entry=round(sig.entry, 2),
            cost_per_unit=round(fno_round_trip_cost(fill, fill + reward, filled,
                                                    future=s.futures) / filled, 2),
            expiry=c.get("expiry"))
        self.report.entries += 1
        await self._subscribe([c["code"]])
        logger.info("F&O: BOUGHT %d %s @ %.2f (%s, %s) premium stop %.2f; %s stop %.2f "
                    "target %.2f", filled, c["symbol"], fill, sig.strategy, what, stop,
                    und.name, sig.stop, sig.target)

    # ── Orders ───────────────────────────────────────────────────────────

    def _signal(self, symbol: str, side: str, price: float, qty: int, reason: str, *,
                security_id: str, lot: int, exchange: str,
                stop: Optional[float] = None) -> TradingSignal:
        return TradingSignal(
            symbol=symbol, exchange=Exchange.BSE if exchange == "BSE" else Exchange.NSE,
            action=side, confidence=70, entry_price=price, order_type=OrderType.MARKET,
            quantity=Decimal(qty), stop_loss=stop, reasoning=reason,
            product=Product.INTRADAY, segment=Segment.DERIVATIVE, security_id=security_id,
            lot_size=max(1, int(lot)))

    async def _execute(self, signal: TradingSignal, price: float):
        if self._paper:
            paper = getattr(self._router, "_paper", None)
            if paper is not None:
                paper.update_quote(Quote(symbol=signal.symbol, ltp=price, bid=price * 0.999,
                                         ask=price * 1.001))
        result = await self._executor.execute_signal(signal)
        if result.success and self._on_trade is not None:
            try:
                await self._on_trade(signal, result)
            except Exception:
                logger.warning("F&O: trade row of %s not written", signal.symbol,
                               exc_info=True)
        return result

    # ── Dashboard ────────────────────────────────────────────────────────

    async def _service_control(self) -> None:
        if self._control is None:
            return
        try:
            commands = self._control.claim(target="fno")
        except Exception:
            return
        for cmd in commands:
            kind = cmd.get("kind")
            symbol = str((cmd.get("payload") or {}).get("symbol") or "").upper()
            by = cmd.get("by") or "dashboard"
            if kind == "close_all":
                n = len(self.positions)
                for pos in list(self.positions.values()):
                    await self._exit(pos, self.prices.get(pos.scrip_code) or pos.entry,
                                     f"FNO MANUAL CLOSE ALL ({by})")
                ok, msg = True, f"closed {n} F&O position(s)"
            elif kind == "close" and symbol in self.positions:
                pos = self.positions[symbol]
                await self._exit(pos, self.prices.get(pos.scrip_code) or pos.entry,
                                 f"FNO MANUAL CLOSE ({by})")
                ok, msg = symbol not in self.positions, f"close {symbol}: " + (
                    "done" if symbol not in self.positions else "not fully sold")
            else:
                ok, msg = False, f"no open F&O position {symbol}" if kind == "close" else \
                    f"the F&O engine does not take {kind!r}"
            self._control.complete(str(cmd.get("id")), ok=ok, message=msg)

    def _publish(self, *, ended: bool = False) -> None:
        if self._control is None:
            return
        rows = []
        for pos in self.positions.values():
            price = self.prices.get(pos.scrip_code)
            und = self.unders.get(pos.underlying)
            rows.append({
                "symbol": pos.symbol, "underlying": pos.underlying, "kind": pos.kind,
                "strategy": pos.strategy, "quantity": pos.qty, "lots": pos.lots,
                "lot_size": pos.lot_size, "entry_price": pos.entry, "ltp": price,
                "stop_loss": pos.stop, "breakeven": pos.breakeven, "high": pos.high,
                "underlying_ltp": self.prices.get(und.code) if und else None,
                "underlying_stop": pos.und_stop, "underlying_target": pos.und_target,
                "pnl": round((price - pos.entry) * pos.qty, 2) if price else None,
                "opened_at": pos.opened_at.isoformat(timespec="seconds"),
                "expiry": pos.expiry.isoformat() if pos.expiry else None,
            })
        s = self.settings
        self._control.write_status("fno", {
            "ended": ended, "mode": "paper" if self._paper else "live",
            "instrument": s.instrument, "positions": rows,
            "trades": [t.__dict__ for t in self.report.trades[-30:]],
            "entries": self.report.entries, "net_pnl": self.report.net_pnl,
            "by_strategy": self.report.by_strategy(),
            "skipped_cost": self.report.skipped_cost, "skipped_size": self.report.skipped_size,
            "skipped_contract": self.report.skipped_contract,
            "rejected": self.report.rejected,
            "underlyings": {u.name: self.prices.get(u.code) for u in self.unders.values()},
            "strategies": list(s.strategies),
            "window": f"{s.entry_start:%H:%M}–{s.entry_end:%H:%M}, flatten {s.flatten_at:%H:%M}",
            "limits": {"max_trades": s.max_trades_per_day, "max_open": s.max_open,
                       "max_daily_loss": s.max_daily_loss, "max_lots": s.max_lots,
                       "risk_per_trade": s.risk_per_trade},
            "notes": self.report.notes[-3:],
            "blocked": self._can_enter(self._wall().astimezone(_IST)),
        })


def _date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
