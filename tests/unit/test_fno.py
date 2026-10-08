"""F&O trading (Phase 5): the derivative plumbing of the order pipeline (signal → order,
safety, paper engine, book-first reads, sellable, trade rows) and the options engine
(skopaq/scalping/fno_rules.py, fno_engine.py) end to end on the paper engine."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from skopaq.broker.book_snapshot import read_broker_snapshot
from skopaq.broker.models import (
    ExecutionResult,
    Funds,
    OrderRequest,
    OrderType,
    Position,
    Product,
    Quote,
    Segment,
    Side,
    TradingSignal,
    derivative_scrip_code,
    is_derivative_segment,
)
from skopaq.broker.order_status import OrderSnapshot, OrderState
from skopaq.broker.paper_engine import PaperEngine
from skopaq.constants import SafetyRules
from skopaq.execution.executor import Executor, _cost_basis
from skopaq.execution.order_router import OrderRouter
from skopaq.execution.safety_checker import SafetyChecker
from skopaq.execution.sellable import SellContext, sellable_quantity
from skopaq.market.candles import Candle
from skopaq.options.chain import OptionChainData, OptionContract
from skopaq.scalping.fno_engine import FnoEngine, underlying_code
from skopaq.scalping.fno_rules import (
    FnoPosition,
    FnoSettings,
    bearish,
    exit_reason,
    fno_round_trip_cost,
    inversion_constant,
    invert_candles,
    pick_option,
    size_lots,
    update_trail,
)
from skopaq.scalping.strategies import Setup

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 8, 9, 15, tzinfo=IST)
SCALE = 250.0
ORB_FIRST = [100, 100.4, 100.8, 100.2, 99.9, 100.3, 100.6, 100.1, 100.0, 100.5, 100.7,
             100.2, 100.4, 100.6, 100.3]
RULES = SafetyRules(market_hours_only=False, require_stop_loss=True,
                    max_order_value_inr=10_000_000, max_position_pct=1.0)
CONTRACT = "NIFTY-OCT2026-25400-CE"


def mk(closes, spread=0.1, vol=None, start=T0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(start + timedelta(minutes=i), prev, max(prev, c) + spread,
                          min(prev, c) - spread, c, vol))
        prev = c
    return out


def fno_signal(action="BUY", qty=75, **kw) -> TradingSignal:
    base = dict(symbol=CONTRACT, action=action, entry_price=150.0,
                order_type=OrderType.MARKET, quantity=Decimal(qty), stop_loss=112.5,
                product=Product.INTRADAY, segment=Segment.DERIVATIVE, security_id="45110",
                lot_size=75, confidence=70)
    base.update(kw)
    return TradingSignal(**base)


# ── Signal → order ───────────────────────────────────────────────────────────


def test_executor_builds_a_derivative_order_with_the_contract():
    executor = Executor(MagicMock(), SafetyChecker(rules=RULES))
    order = executor._build_order(fno_signal())
    assert order.segment is Segment.DERIVATIVE and order.security_id == "45110"
    assert order.lot_size == 75 and order.lots == 1 and order.product is Product.INTRADAY
    assert order.trigger_price is None          # the engine exits on its own rules
    # CNC is never sent for F&O; a contract without its id or a broken lot is not ordered
    assert executor._build_order(fno_signal(product=Product.CNC)).product is Product.INTRADAY
    assert executor._build_order(fno_signal(security_id="")) is None
    assert executor._build_order(fno_signal(qty=100)) is None


async def test_executor_never_prices_a_contract_from_yahoo(monkeypatch):
    router = MagicMock(mode="paper")
    router.get_all_positions = AsyncMock(return_value=[])
    router.get_funds = AsyncMock(return_value=Funds(available_margin=100_000))
    router.execute = AsyncMock(return_value=ExecutionResult(success=True, fill_price=150))
    router.sell_lock = MagicMock(return_value=None)
    executor = Executor(router, SafetyChecker(rules=RULES), position_sizer=MagicMock())
    fetch = MagicMock(return_value=999.0)
    monkeypatch.setattr(Executor, "_fetch_current_price", staticmethod(fetch))
    monkeypatch.setattr("skopaq.notifications.notify_trade_event", AsyncMock(),
                        raising=False)
    result = await executor.execute_signal(fno_signal(entry_price=None))
    assert result.success
    fetch.assert_not_called()
    executor._sizer.compute_size.assert_not_called()
    router.get_all_positions.assert_awaited()   # equity + F&O rows for the limits


def test_cost_basis_of_a_contract_goes_by_security_id():
    rows = [Position(symbol="NIFTY 25400 CE", security_id="45110", quantity=75,
                     average_price=140.0),
            Position(symbol=CONTRACT, security_id="99", quantity=75, average_price=1.0)]
    assert _cost_basis(CONTRACT, rows, [], security_id="45110") == 140.0
    assert _cost_basis(CONTRACT, rows, []) == 1.0      # without an id: by symbol


# ── Safety ───────────────────────────────────────────────────────────────────


def _order(side=Side.BUY, qty=75, price=150.0, symbol=CONTRACT):
    return OrderRequest(symbol=symbol, side=side, quantity=Decimal(qty), price=price,
                        segment=Segment.DERIVATIVE, product=Product.INTRADAY, lot_size=75,
                        security_id="45110")


def test_an_option_buy_is_paid_from_the_option_buying_balance():
    checker = SafetyChecker(rules=RULES)
    rejections: list[str] = []
    checker._check_sufficient_funds(
        _order(), Funds(available_margin=1_000_000, option_buy_available=5_000), rejections)
    assert rejections and "option-buying balance" in rejections[0]
    rejections = []
    checker._check_sufficient_funds(
        _order(), Funds(available_margin=1_000_000, option_buy_available=20_000), rejections)
    assert rejections == []
    # No segment balance (paper): the general margin
    checker._check_sufficient_funds(_order(), Funds(available_margin=20_000), rejections)
    assert rejections == []


def test_a_future_buy_needs_an_estimated_margin_not_the_notional():
    checker = SafetyChecker(rules=RULES)
    fut = _order(price=25_000.0, symbol="NIFTY-OCT2026-FUT")
    rejections: list[str] = []
    checker._check_sufficient_funds(fut, Funds(futures_available=400_000), rejections)
    assert rejections == []                       # 20 % of ₹18.75 L = ₹3.75 L
    checker._check_sufficient_funds(fut, Funds(futures_available=300_000), rejections)
    assert "futures margin" in rejections[0]


def test_an_index_future_lot_is_refused_by_the_order_value_cap():
    checker = SafetyChecker()                     # the immutable defaults: ₹5 L per order
    rejections: list[str] = []
    checker._check_order_value(_order(price=25_000.0, symbol="NIFTY-OCT2026-FUT"),
                               rejections)
    assert rejections and "exceeds max" in rejections[0]


def test_one_lot_passes_the_position_percentage_on_a_small_account():
    checker = SafetyChecker()
    rejections: list[str] = []
    checker._check_position_size(_order(), 50_000, rejections)     # 22 % of ₹50k
    assert rejections == []
    checker._check_position_size(_order(qty=150), 50_000, rejections)
    assert rejections


# ── Paper engine ─────────────────────────────────────────────────────────────


def test_paper_never_writes_an_option_and_keeps_the_contract_on_its_row():
    paper = PaperEngine(initial_capital=100_000)
    paper.update_quote(Quote(symbol=CONTRACT, ltp=150, bid=149.9, ask=150.1))
    sell = _order(side=Side.SELL, price=None)
    sell.order_type = OrderType.MARKET
    refused = paper.execute_order(sell)
    assert not refused.success and "No short F&O" in refused.rejection_reason
    buy = _order(price=None)
    buy.order_type = OrderType.MARKET
    assert paper.execute_order(buy).success
    [row] = paper.get_positions()
    assert row.security_id == "45110" and is_derivative_segment(row.segment)
    assert row.product == "INTRADAY" and row.quantity == 75
    assert paper.execute_order(sell).success
    assert paper.get_positions() == []


async def test_router_lists_paper_derivative_positions_apart():
    paper = PaperEngine(initial_capital=100_000)
    paper.update_quote(Quote(symbol=CONTRACT, ltp=150, bid=149.9, ask=150.1))
    paper.update_quote(Quote(symbol="TCS", ltp=4000, bid=3999, ask=4001))
    buy = _order(price=None)
    buy.order_type = OrderType.MARKET
    paper.execute_order(buy)
    paper.execute_order(OrderRequest(symbol="TCS", side=Side.BUY, quantity=Decimal(1),
                                     order_type=OrderType.MARKET))
    router = OrderRouter(SimpleNamespace(trading_mode="paper"), paper)
    assert [p.symbol for p in await router.get_derivative_positions()] == [CONTRACT]
    assert len(await router.get_all_positions()) == 2


# ── Live reads ───────────────────────────────────────────────────────────────


async def test_a_derivative_snapshot_reads_fno_positions_and_no_holdings():
    client = MagicMock()
    client.get_order_book = AsyncMock(return_value=[])
    client.get_positions = AsyncMock(return_value=[Position(symbol="TCS", quantity=1)])
    client.get_derivative_positions = AsyncMock(return_value=[
        Position(symbol=CONTRACT, security_id="45110", quantity=75, product="INTRADAY")])
    client.get_holdings = AsyncMock(return_value=[])
    snap = await read_broker_snapshot(client, segment="DERIVATIVE")
    assert [p.symbol for p in snap.positions] == [CONTRACT]
    assert is_derivative_segment(snap.positions[0].segment)
    client.get_positions.assert_not_awaited()
    client.get_holdings.assert_not_awaited()
    equity = await read_broker_snapshot(client)
    assert [p.symbol for p in equity.positions] == ["TCS"]


def _sell_row(segment, order_id="1", security_id="45110"):
    return OrderSnapshot(
        order_id=order_id, status="OPEN", status_raw="OPEN", state=OrderState.WORKING,
        side="SELL", security_id=security_id, symbol="", name="", exchange="NSE",
        segment=segment, product="INTRADAY", order_type="MARKET", validity="DAY",
        requested_qty=Decimal(75), traded_qty=Decimal(0), traded_price=None,
        exch_order_id="", message="", remarks="", created_at=None, updated_at=None)


def test_sellable_leaves_out_book_rows_of_the_other_segment():
    position = Position(symbol=CONTRACT, security_id="45110", quantity=75, product="INTRADAY")

    def view(rows, segment):
        context = SellContext(orders=tuple(rows), read_at=T0)
        return sellable_quantity(symbol=CONTRACT, security_id="45110", product="INTRADAY",
                                 positions=[position], holdings=[], context=context,
                                 order_qty=Decimal(75), exchange="NSE", segment=segment)

    # An equity SELL of security id 45110 is another instrument
    assert view([_sell_row("EQUITY")], "DERIVATIVE").sellable == 75
    assert view([_sell_row("DERIVATIVE")], "DERIVATIVE").sellable == 0
    assert view([_sell_row("")], "DERIVATIVE").sellable == 0          # unknown: counts


def test_derivative_scrip_codes():
    assert derivative_scrip_code("NSE", "45110") == "NFO_45110"
    assert derivative_scrip_code("BSE", "8") == "BFO_8"
    und = SimpleNamespace(is_index=True, exchange="NSE", security_id="40000001")
    assert underlying_code(und) == "NIDX_40000001"
    stock = SimpleNamespace(is_index=False, exchange="NSE", security_id="2885")
    assert underlying_code(stock) == "NSE_2885"


def test_trade_rows_use_the_tables_product_and_carry_the_contract():
    from skopaq.cli.main import _build_trade_record

    signal = fno_signal()
    execution = ExecutionResult(success=True, fill_price=150.5, mode="paper")
    result = SimpleNamespace(signal=signal, execution=execution, symbol=CONTRACT,
                             cache_hits=0, cache_misses=0, duration_seconds=0)
    row = _build_trade_record(result, SimpleNamespace(asset_class="equity"))
    assert row.product == "MIS" and row.exchange == "NSE"
    assert row.model_signals["fno"] == {"segment": "DERIVATIVE", "security_id": "45110",
                                        "lot_size": 75, "lots": 1}


# ── Rules ────────────────────────────────────────────────────────────────────


def test_inverted_candles_turn_a_falling_day_into_a_rising_one():
    real = mk([SCALE * 100 * 100 / x for x in ORB_FIRST + [100.5, 101.5]], spread=5)
    k = inversion_constant(real)
    inverted = invert_candles(real, k)
    assert inverted[-1].close > inverted[-2].close          # the fall is a rise
    assert all(c.high >= c.low for c in inverted)
    s = bearish(Setup("orb", 25_375.0, 25_300.0, 25_525.0, "breakout"), k)
    assert s.direction == -1 and s.stop > s.entry > s.target


def _chain(spot=25_380.0, spread=0.5):
    def leg(strike, kind):
        intrinsic = max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
        price = round(intrinsic + 120, 2)
        return OptionContract(
            tradingsymbol=f"NIFTY-Oct2026-{int(strike)}-{kind}",
            security_id=f"{int(strike)}{1 if kind == 'CE' else 2}", exchange="NFO",
            strike=strike, option_type=kind, expiry=date(2026, 10, 13), lot_size=75,
            ltp=price, bid=price - spread / 2, ask=price + spread / 2, delta=0.5)
    strikes = [25_200.0 + 50 * i for i in range(9)]
    return OptionChainData(symbol="NIFTY", spot_price=spot, expiry=date(2026, 10, 13),
                           calls=[leg(k, "CE") for k in strikes],
                           puts=[leg(k, "PE") for k in strikes], lot_size=75,
                           expiries=["2026-10-13", "2026-10-20"])


def test_pick_option_strikes_and_liquidity():
    chain = _chain()
    assert pick_option(chain, 1, 0, 0.03).strike == 25_400       # ATM call
    assert pick_option(chain, 1, -1, 0.03).strike == 25_350      # one ITM call
    assert pick_option(chain, -1, 0, 0.03).strike == 25_400      # ATM put
    assert pick_option(chain, -1, -1, 0.03).strike == 25_450     # one ITM put
    assert pick_option(chain, -1, 1, 0.03).strike == 25_350      # one OTM put
    assert pick_option(_chain(spread=20), 1, 0, 0.03) is None    # too wide


def test_size_lots_and_charges():
    assert size_lots(unit_risk=37.5, unit_cost=150, lot=75, risk_inr=1_000, max_lots=3,
                     max_outlay=50_000) == 0                     # one lot risks ₹2,812
    assert size_lots(unit_risk=37.5, unit_cost=150, lot=75, risk_inr=10_000, max_lots=3,
                     max_outlay=50_000) == 3
    assert size_lots(unit_risk=37.5, unit_cost=150, lot=75, risk_inr=10_000, max_lots=5,
                     max_outlay=50_000, safety_max_lots=2) == 2
    assert size_lots(unit_risk=37.5, unit_cost=150, lot=75, risk_inr=10_000, max_lots=5,
                     max_outlay=20_000) == 1                     # premium cap
    assert size_lots(unit_risk=37.5, unit_cost=150, lot=75, risk_inr=10_000, max_lots=5,
                     max_outlay=50_000, equity=50_000, max_position_pct=0.15) == 1
    cost = fno_round_trip_cost(150, 180, 75)
    assert 20 < cost < 60                     # ₹20 brokerage + GST + STT on the premium
    assert fno_round_trip_cost(25_000, 25_100, 75, future=True) > cost


def test_exit_rules_premium_underlying_trail_and_time():
    pos = FnoPosition(symbol=CONTRACT, underlying="NIFTY", kind="CE", direction=1,
                      security_id="45110", exchange="NSE", scrip_code="NFO_45110",
                      lot_size=75, qty=75, entry=150.0, stop=112.5, opened_at=T0,
                      strategy="orb", und_stop=25_300.0, und_target=25_525.0,
                      cost_per_unit=0.5)
    hold = timedelta(minutes=30)
    assert "PREMIUM STOP" in exit_reason(pos, 112.0, 25_350, T0, hold)
    assert "FNO STOP" in exit_reason(pos, 140.0, 25_299, T0, hold)
    assert "FNO TARGET" in exit_reason(pos, 200.0, 25_530, T0, hold)
    assert exit_reason(pos, 150.0, 25_380, T0, hold) is None
    assert "TIME STOP" in exit_reason(pos, 150.0, 25_380, T0 + hold, hold)
    update_trail(pos, 190.0, 0.15)                        # +1 R: breakeven, then trail
    assert pos.breakeven and pos.stop == pytest.approx(161.5)
    put = FnoPosition(symbol="P", underlying="NIFTY", kind="PE", direction=-1,
                      security_id="2", exchange="NSE", scrip_code="NFO_2", lot_size=75,
                      qty=75, entry=150.0, stop=112.5, opened_at=T0, strategy="orb",
                      und_stop=25_450.0, und_target=25_225.0)
    assert "FNO STOP" in exit_reason(put, 140.0, 25_451, T0, hold)
    assert "FNO TARGET" in exit_reason(put, 200.0, 25_220, T0, hold)


def test_settings_defaults_and_bad_values():
    s = FnoSettings.from_config(SimpleNamespace())
    assert s.underlyings == ("NIFTY",) and s.instrument == "options" and s.max_lots == 1
    bad = FnoSettings.from_config(SimpleNamespace(
        fno_instrument="naked-puts", fno_max_lots="lots", fno_entry_start="15:00",
        fno_entry_end="10:00", fno_premium_stop_pct=5.0))
    assert bad.instrument == "options" and bad.max_lots == 1
    assert bad.entry_start.hour == 9 and bad.premium_stop_pct == 0.9


# ── The engine on the paper engine ───────────────────────────────────────────


class Wall:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class Client:
    """Index history before now; REST quotes of the index and the contracts from a path
    of the wall clock."""

    def __init__(self, wall, history, index_path):
        self.wall, self.history, self.index_path = wall, history, index_path

    async def get_historical(self, scrip, interval, start_time, end_time):
        assert scrip == "NIDX_40000001"
        return [SimpleNamespace(timestamp=c.start, open=c.open, high=c.high, low=c.low,
                                close=c.close, volume=c.volume) for c in self.history]

    async def get_quotes(self, codes, symbols=None):
        index = self.index_path(self.wall())
        out = []
        for code in codes:
            if code == "NIDX_40000001":
                out.append(Quote(symbol=code, ltp=index))
            else:                                  # the ATM call: half the index move
                out.append(Quote(symbol=code, ltp=round(120 + (index - 25_380) * 0.5, 2)))
        return out


async def _resolve(client, name):
    return SimpleNamespace(symbol="NIFTY", exchange="NSE", segment="INDEX",
                           security_id="40000001", is_index=True)


def _paper_engine(config, wall, client, chain=None):
    paper = PaperEngine(initial_capital=1_000_000)
    router = OrderRouter(config, paper)
    executor = Executor(router, SafetyChecker(rules=RULES))
    trades = []

    async def on_trade(signal, execution):
        trades.append(signal)

    async def chain_loader(client, name, expiry_index, strike_count):
        return chain or _chain()

    engine = FnoEngine(config, executor, client, router, on_trade=on_trade, wall=wall,
                       sleep=wall.sleep, halted=lambda: False, max_lots=5,
                       resolver=_resolve, chain_loader=chain_loader)
    return engine, paper, trades


async def test_engine_buys_the_atm_call_on_a_breakout_and_sells_it_at_the_target(
        monkeypatch):
    monkeypatch.setattr("skopaq.notifications.notify", AsyncMock())
    history = mk([SCALE * x for x in ORB_FIRST + [100.5, 100.6, 100.7]], spread=50)
    start = T0 + timedelta(minutes=18, seconds=5)                    # 09:33:05

    def path(now):
        minutes = (now - T0).total_seconds() / 60
        if minutes < 19:
            return 25_380.0                    # the 09:33 candle closes above the range
        return min(26_000.0, 25_380.0 + (minutes - 19) * 60)

    wall = Wall(start)
    config = SimpleNamespace(
        trading_mode="paper", fno_underlyings="NIFTY", fno_strategies="orb",
        fno_flatten_at="09:50", fno_entry_start="09:30", fno_entry_end="09:45",
        fno_rest_poll_seconds=1, fno_risk_per_trade_inr=5_000, fno_max_lots=2,
        max_sector_concentration_pct=1.0)
    engine, paper, trades = _paper_engine(config, wall, Client(wall, history, path))
    report = await asyncio.wait_for(engine.run(asyncio.Event()), 10)

    assert [t.action for t in trades] == ["BUY", "SELL"]
    buy, sell = trades
    assert buy.symbol == "NIFTY-OCT2026-25400-CE" and buy.security_id == "254001"
    assert buy.segment is Segment.DERIVATIVE and buy.product is Product.INTRADAY
    assert buy.lot_size == 75 and buy.quantity % 75 == 0 and buy.stop_loss > 0
    assert "FNO TARGET" in sell.reasoning and sell.quantity == buy.quantity
    [trade] = report.trades
    assert trade.pnl > 0 and trade.strategy == "orb"
    assert paper.get_positions() == []


async def test_engine_buys_a_put_on_a_bearish_breakdown():
    real = mk([SCALE * 100 * 100 / x for x in ORB_FIRST + [100.5, 100.6, 100.7, 101.5]],
              spread=5)
    engine = FnoEngine(SimpleNamespace(fno_strategies="orb"), AsyncMock(), AsyncMock(),
                       SimpleNamespace(mode="paper"), halted=lambda: False)
    from skopaq.market.candles import LiveSeries
    from skopaq.scalping.fno_engine import _Underlying

    und = _Underlying("NIFTY", "NSE", "NIDX_1", LiveSeries())
    und.series.seed(real)
    signal = engine.find_signal(und)
    assert signal is not None and signal.direction == -1 and signal.stop > signal.entry
    futures = FnoEngine(SimpleNamespace(fno_strategies="orb", fno_instrument="futures"),
                        AsyncMock(), AsyncMock(), SimpleNamespace(mode="paper"))
    assert futures.find_signal(und) is None            # futures: long only


async def test_an_unconfirmed_option_buy_blocks_the_underlying_and_limits_apply():
    executor = AsyncMock()
    executor.execute_signal = AsyncMock(return_value=ExecutionResult(
        success=False, mode="live", remaining_open=True, rejection_reason="may be working"))
    router = SimpleNamespace(mode="live", get_funds=AsyncMock(return_value=Funds(
        total_collateral=1_000_000, available_cash=1_000_000)))

    async def chain_loader(*_a):
        return _chain()

    engine = FnoEngine(SimpleNamespace(fno_risk_per_trade_inr=10_000, fno_max_lots=2),
                       executor, AsyncMock(), router, halted=lambda: False,
                       chain_loader=chain_loader, max_lots=5)
    from skopaq.market.candles import LiveSeries
    from skopaq.scalping.fno_engine import _Underlying
    from skopaq.scalping.fno_rules import Signal

    und = _Underlying("NIFTY", "NSE", "NIDX_1", LiveSeries())
    await engine._enter(und, Signal(1, "orb", 25_380, 25_300, 25_540, "x"),
                        T0.replace(hour=10))
    assert "NIFTY" in engine._blocked and engine.positions == {}
    signal = executor.execute_signal.await_args.args[0]
    assert signal.segment is Segment.DERIVATIVE and signal.quantity == Decimal(150)
    assert engine._can_enter(T0.replace(hour=10)) is None
    engine.report.entries = 4
    assert engine._can_enter(T0.replace(hour=10)) == "max F&O trades today"


async def test_dashboard_fno_closes_and_status(tmp_path):
    from skopaq.execution.control import ControlChannel

    channel = ControlChannel(tmp_path / "control")
    executor = AsyncMock()
    executor.execute_signal = AsyncMock(return_value=ExecutionResult(
        success=True, mode="paper", fill_price=160.0))
    engine = FnoEngine(SimpleNamespace(), executor, AsyncMock(), SimpleNamespace(mode="paper"),
                       control=channel, halted=lambda: False, wall=lambda: T0.replace(hour=10))
    engine.positions[CONTRACT] = FnoPosition(
        symbol=CONTRACT, underlying="NIFTY", kind="CE", direction=1, security_id="45110",
        exchange="NSE", scrip_code="NFO_45110", lot_size=75, qty=75, entry=150.0,
        stop=112.5, opened_at=T0, strategy="orb")
    engine.prices["NFO_45110"] = 160.0
    engine._publish()
    status = channel.read_status("fno")
    assert status["positions"][0]["pnl"] == 750.0 and status["positions"][0]["lots"] == 1

    scalper_cmd = channel.submit("close_all", {}, "a@x", target="scalper")   # not ours
    cmd = channel.submit("close", {"symbol": CONTRACT}, "a@x", target="fno")
    await engine._service_control()
    assert channel.result(cmd)["ok"] and engine.positions == {}
    assert channel.result(scalper_cmd) is None
    signal = executor.execute_signal.await_args.args[0]
    assert signal.action == "SELL" and signal.segment is Segment.DERIVATIVE
    assert signal.security_id == "45110" and signal.position_only


# ── The daemon ───────────────────────────────────────────────────────────────


async def test_the_daemon_starts_no_fno_engine_unless_enabled_and_reports_its_day():
    from skopaq.execution.daemon import DaemonSessionReport, TradingDaemon
    from skopaq.scalping.fno_engine import FnoReport

    off = SimpleNamespace(_config=SimpleNamespace(fno_enabled=False), _fno=None)
    await TradingDaemon._start_fno(off)
    assert off._fno is None

    report = FnoReport(left_open=[CONTRACT])

    async def done():
        return report

    feed = SimpleNamespace(stop=AsyncMock())
    daemon = SimpleNamespace(_fno=asyncio.ensure_future(done()), _fno_feed=feed)
    session = DaemonSessionReport()
    await TradingDaemon._finish_fno(daemon, session)
    assert session.fno_summary.startswith("F&O") and session.positions_left == [
        f"{CONTRACT} (F&O)"]
    feed.stop.assert_awaited_once()
    assert daemon._fno is None and daemon._fno_feed is None
