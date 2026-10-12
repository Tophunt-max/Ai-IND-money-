"""MCP Server for SkopaqTrader — exposes broker, data, swing-model and backtest tools.

Run as a standalone MCP server via stdio transport::

    python -m skopaq.mcp_server

Or add to Claude Desktop / Claude Code config::

    {
      "mcpServers": {
        "skopaq": {
          "command": "python3",
          "args": ["-m", "skopaq.mcp_server"]
        }
      }
    }

Any MCP-compatible client (Claude Desktop, Cursor, Windsurf) will
automatically discover these tools and make them available to the AI.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd
from mcp.server import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="SkopaqTrader",
    instructions=(
        "SkopaqTrader MCP server for Indian equity and crypto trading. "
        "Provides real-time quotes, historical data, portfolio management, "
        "the Adaptive-AI swing ideas, backtests, and trade execution. "
        "Paper trading mode is the default — safe for experimentation."
    ),
)

# ── Lazy infrastructure ──────────────────────────────────────────────────────
# Built on first tool call to avoid import-time overhead.

_infra_cache: dict = {}


def _get_config():
    if "config" not in _infra_cache:
        from skopaq.config import SkopaqConfig

        _infra_cache["config"] = SkopaqConfig()
    return _infra_cache["config"]


def _get_router():
    if "router" not in _infra_cache:
        from skopaq.broker.paper_engine import PaperEngine
        from skopaq.execution.order_router import OrderRouter

        config = _get_config()
        paper = PaperEngine(initial_capital=config.initial_paper_capital)
        router = OrderRouter(config, paper)
        _infra_cache["router"] = router
        _infra_cache["paper"] = paper
    return _infra_cache["router"]


# ── Market Data Tools ────────────────────────────────────────────────────────


@mcp.tool()
async def get_quote(symbol: str) -> str:
    """Get a real-time market quote for a stock symbol.

    Returns LTP, OHLC, bid/ask, volume, and change %.

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS, INFY).
    """
    config = _get_config()

    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.scrip_resolver import resolve_scrip_code
    from skopaq.broker.token_manager import TokenManager

    token_mgr = TokenManager()
    async with INDstocksClient(config, token_mgr) as client:
        scrip_code = await resolve_scrip_code(client, symbol)
        q = await client.get_quote(scrip_code, symbol=symbol)

    return json.dumps({
        "symbol": q.symbol,
        "exchange": q.exchange,
        "ltp": q.ltp,
        "open": q.open,
        "high": q.high,
        "low": q.low,
        "close": q.close,
        "bid": q.bid,
        "ask": q.ask,
        "volume": q.volume,
        "change_pct": q.change_pct,
    })


@mcp.tool()
async def get_historical(
    symbol: str,
    days: int = 5,
    resolution: int = 1,
) -> str:
    """Fetch historical OHLCV candles for a symbol.

    Args:
        symbol: Stock symbol (e.g. RELIANCE).
        days: Number of days of history (default 5).
        resolution: Candle resolution in minutes (1, 5, 15, 60).
    """
    config = _get_config()

    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.scrip_resolver import resolve_scrip_code
    from skopaq.broker.token_manager import TokenManager

    now = datetime.now(timezone.utc)
    from_ts = int((now.timestamp() - days * 86400) * 1000)
    to_ts = int(now.timestamp() * 1000)

    # Map numeric resolution to INDstocks interval string
    interval_map = {1: "1minute", 5: "5minute", 15: "15minute", 30: "30minute", 60: "60minute"}
    interval = interval_map.get(resolution, f"{resolution}minute")

    token_mgr = TokenManager()
    async with INDstocksClient(config, token_mgr) as client:
        scrip_code = await resolve_scrip_code(client, symbol)
        candles = await client.get_historical(
            scrip_code, interval=interval, start_time=from_ts, end_time=to_ts,
        )

    return json.dumps({
        "symbol": symbol,
        "count": len(candles),
        "resolution_min": resolution,
        "candles": [
            {"ts": c.timestamp.isoformat(), "o": c.open, "h": c.high,
             "l": c.low, "c": c.close, "v": c.volume}
            for c in candles[-20:]  # Last 20 candles
        ],
    })


# ── Portfolio Tools ──────────────────────────────────────────────────────────


@mcp.tool()
async def get_positions() -> str:
    """Get open positions with P&L."""
    router = _get_router()
    positions = await router.get_positions()
    return json.dumps([
        {
            "symbol": p.symbol,
            "exchange": p.exchange,
            "quantity": str(p.quantity),
            "avg_price": p.average_price,
            "ltp": p.last_price,
            "pnl": p.pnl,
        }
        for p in positions
    ])


@mcp.tool()
async def get_holdings() -> str:
    """Get delivery holdings."""
    router = _get_router()
    holdings = await router.get_holdings()
    return json.dumps([
        {
            "symbol": h.symbol,
            "quantity": str(h.quantity),
            "avg_price": h.average_price,
            "ltp": h.last_price,
            "pnl": h.pnl,
        }
        for h in holdings
    ])


@mcp.tool()
async def get_funds() -> str:
    """Get available cash, margin, and collateral."""
    router = _get_router()
    funds = await router.get_funds()
    return json.dumps({
        "available_cash": funds.available_cash,
        "used_margin": funds.used_margin,
        "available_margin": funds.available_margin,
        "total_collateral": funds.total_collateral,
    })


@mcp.tool()
async def get_orders() -> str:
    """Get today's orders with status."""
    router = _get_router()
    orders = await router.get_orders()
    return json.dumps([
        {"order_id": o.order_id, "status": o.status, "message": o.message}
        for o in orders
    ])


# ── Analysis Tools ───────────────────────────────────────────────────────────


@mcp.tool()
async def check_safety(
    symbol: str,
    quantity: int = 1,
    price: float = 0,
    side: str = "BUY",
) -> str:
    """Validate a hypothetical order against safety rules.

    Returns pass/fail with rejection reasons.

    Args:
        symbol: Stock symbol.
        quantity: Number of shares.
        price: Order price in INR.
        side: BUY or SELL.
    """
    from decimal import Decimal

    from skopaq.broker.models import OrderRequest, Side, TradingSignal

    router = _get_router()

    from skopaq.constants import PAPER_SAFETY_RULES
    from skopaq.execution.safety_checker import SafetyChecker

    safety = SafetyChecker(rules=PAPER_SAFETY_RULES)
    config = _get_config()
    from skopaq.execution.pnl_history import seed_safety_checker
    seed_safety_checker(safety, config)

    order = OrderRequest(
        symbol=symbol,
        side=Side(side.upper()),
        quantity=Decimal(str(quantity)),
        price=price if price else None,
    )
    signal = TradingSignal(
        symbol=symbol, action=side.upper(), confidence=50,
        entry_price=price if price else None, reasoning="Safety check",
    )
    # A live SELL is checked against the broker's open orders (read before
    # positions); None in paper, where today's reads apply
    inputs = await router.sell_inputs(order) if order.side == Side.SELL else None
    if inputs is None:
        positions = await router.get_positions()
        holdings = await router.get_settled_holdings()
    else:
        positions, holdings = inputs.positions, inputs.holdings
    funds = await router.get_funds()

    result = safety.validate(
        order, signal, positions, funds,
        funds.available_cash + funds.used_margin,
        holdings=holdings,
        sell_context=inputs.context if inputs is not None else None,
    )

    return json.dumps({
        "passed": result.passed,
        "rejections": result.rejections,
    })


# ── System Tools ─────────────────────────────────────────────────────────────


@mcp.tool()
async def system_status() -> str:
    """Check SkopaqTrader system health: version, mode, token status, the swing model."""
    from skopaq import __version__
    from skopaq.broker.token_manager import TokenManager

    config = _get_config()
    health = TokenManager().get_health()

    from skopaq.execution import kill_switch
    from skopaq.learning.registry import model_dir
    from skopaq.swing.model import swing_registry

    reg = swing_registry(model_dir(config))
    champ, shadow = reg.champion(), reg.shadow()

    return json.dumps({
        "version": __version__,
        "mode": config.trading_mode,
        "asset_class": config.asset_class,
        "token_valid": health.valid,
        "swing_model": {"champion": champ["version"] if champ else None,
                        "shadow": shadow["version"] if shadow else None},
        "paper_capital": config.initial_paper_capital,
        "trading": kill_switch.status().describe(),
    })


@mcp.tool()
async def performance_report(days: int = 90) -> str:
    """Track record: the engines' signals (qualified, executed, closed net), closed
    trades, confidence calibration.

    Forward results from the signal journal and the trades table, in the current trading
    mode. Below 30 closed signals the win rate is mostly noise (``enough_data`` false).

    Args:
        days: How many days back to include (default 90).
    """
    from dataclasses import asdict

    from skopaq.learning.report import build_report

    report = build_report(_get_config(), days=days)
    return json.dumps({
        "days": report.days,
        "mode": report.mode,
        "signals": {**asdict(report.signals), "win_rate": report.signals.win_rate,
                    "enough_data": report.signals.enough_data},
        "trades": {**asdict(report.trades), "win_rate": report.trades.win_rate},
        "calibration": [
            {"confidence": b.label, "trades": b.trades, "win_rate": b.win_rate}
            for b in report.calibration
        ],
        "sources": report.sources,
    })


@mcp.tool()
async def halt_trading(reason: str = "halted from Claude Code") -> str:
    """Kill switch: reject every BUY everywhere until resume_trading.

    Applies to the daemon, the CLI, MCP and chat. SELLs stay allowed so open
    positions can still be protected.

    Args:
        reason: Why trading is being halted (shown wherever a BUY is refused).
    """
    from skopaq.execution import kill_switch

    where = kill_switch.halt(reason, by="mcp")
    return json.dumps({
        "halted": True,
        "reason": reason,
        "recorded_in": where,
        "every_process": "supabase:system_flags" in where,
    })


@mcp.tool()
async def resume_trading() -> str:
    """Lift the kill switch set by halt_trading or `skopaq halt`."""
    from skopaq.execution import kill_switch

    cleared = kill_switch.resume(by="mcp")
    after = kill_switch.status(use_cache=False)
    return json.dumps({"halted": after.halted, "status": after.describe(), "cleared": cleared})


# ── Order Execution ──────────────────────────────────────────────────────────


@mcp.tool()
async def place_order(
    symbol: str,
    side: str = "BUY",
    quantity: int = 1,
    price: float = 0,
    order_type: str = "MARKET",
) -> str:
    """Place a paper or live order through the safety checker and order router.

    Paper mode is the default. Live mode requires explicit config change.
    All orders pass through the SafetyChecker before execution. This server's
    order router has no live INDstocks client, so its orders go to the paper
    engine (no SELL lock, no order-book read); with a live client a SELL would
    hold its symbol's lock and be checked against the broker's open orders.

    Args:
        symbol: Stock symbol (e.g. RELIANCE).
        side: BUY or SELL.
        quantity: Number of shares.
        price: Limit price (0 = market order).
        order_type: MARKET or LIMIT.
    """
    import contextlib
    from decimal import Decimal

    from skopaq.broker.models import (
        OrderRequest,
        OrderType,
        Side,
        TradingSignal,
        filled_quantity_of,
        is_remaining_open,
        order_ids_of,
        outcome_of,
    )
    from skopaq.constants import PAPER_SAFETY_RULES, SAFETY_RULES
    from skopaq.execution.executor import alert_sell_refused
    from skopaq.execution.safety_checker import SafetyChecker
    from skopaq.execution.sell_lock import SellLockBusy

    config = _get_config()
    router = _get_router()

    rules = PAPER_SAFETY_RULES if config.trading_mode == "paper" else SAFETY_RULES
    safety = SafetyChecker(
        rules=rules,
        max_sector_concentration_pct=config.max_sector_concentration_pct,
    )
    from skopaq.execution.pnl_history import seed_safety_checker
    seed_safety_checker(safety, config)

    # Inject a quote for paper fill simulation
    if config.trading_mode == "paper" and price > 0:
        from skopaq.broker.models import Quote

        _infra_cache["paper"].update_quote(
            Quote(symbol=symbol, ltp=price, open=price, high=price,
                  low=price, close=price, bid=price, ask=price)
        )

    order = OrderRequest(
        symbol=symbol,
        side=Side(side.upper()),
        quantity=Decimal(str(quantity)),
        price=price if price > 0 else None,
        order_type=OrderType(order_type.upper()),
    )
    signal = TradingSignal(
        symbol=symbol,
        action=side.upper(),
        confidence=70,
        entry_price=price if price > 0 else None,
        quantity=Decimal(str(quantity)),
        reasoning="Placed via Claude Code MCP",
    )

    # Safety check first, then execute. A live SELL holds its symbol's lock from
    # the order-book read until the broker's answer is final (as the Executor)
    lock = router.sell_lock(order) if order.side == Side.SELL else None
    try:
        async with lock or contextlib.nullcontext():
            inputs = await router.sell_inputs(order) if order.side == Side.SELL else None
            if inputs is None:
                positions = await router.get_positions()
                holdings = await router.get_settled_holdings()
            else:
                positions, holdings = inputs.positions, inputs.holdings
            funds = await router.get_funds()
            safety_result = safety.validate(
                order, signal, positions, funds,
                funds.available_cash + funds.used_margin,
                holdings=holdings,
                sell_context=inputs.context if inputs is not None else None,
            )

            if not safety_result.passed:
                if inputs is not None:
                    alert_sell_refused(
                        order, safety_result.codes[0] if safety_result.codes else "safety",
                        safety_result.reason, safety_result.blocking_order_ids)
                return json.dumps({
                    "success": False,
                    "reason": f"Safety check failed: {safety_result.reason}",
                })

            result = await router.execute(order, signal)
    except SellLockBusy:
        reason = f"Another Skopaq process is already selling {symbol} — not sending a second SELL"
        alert_sell_refused(order, "lock-busy", reason)
        return json.dumps({"success": False, "reason": reason})

    filled = filled_quantity_of(result, order.quantity if result.success else Decimal(0))
    return json.dumps({
        "success": result.success,
        "mode": result.mode,
        "fill_price": result.fill_price,
        "slippage": result.slippage,
        "rejection_reason": result.rejection_reason or "",
        # What the broker confirmed (paper: the whole order, or nothing)
        "filled_quantity": int(filled) if filled == int(filled) else float(filled),
        "outcome": outcome_of(result),
        "order_ids": order_ids_of(result),
        "remaining_open": is_remaining_open(result),
        "fill_unconfirmed": getattr(result, "fill_unconfirmed", False) is True,
    })


# ── Options Tools ─────────────────────────────────────────────────────────────


@mcp.tool()
async def get_option_chain(
    symbol: str = "NIFTY",
    expiry_index: int = 0,
) -> str:
    """Fetch the option chain for NIFTY, BANKNIFTY, or any stock.

    Returns all calls and puts with strike, premium, OI, volume,
    and distance from spot. Used by the AI for options strategy selection.

    Args:
        symbol: Underlying (NIFTY, BANKNIFTY, RELIANCE, etc.)
        expiry_index: 0 = nearest expiry, 1 = next week, etc.
    """
    try:
        from skopaq.options.chain import load_option_chain

        chain = await load_option_chain(symbol, expiry_index, config=_get_config())

        return json.dumps({
            "symbol": chain.symbol,
            "spot_price": chain.spot_price,
            "expiry": str(chain.expiry),
            "lot_size": chain.lot_size,
            "calls_count": len(chain.calls),
            "puts_count": len(chain.puts),
            "calls": [
                {
                    "strike": c.strike, "ltp": c.ltp, "oi": c.oi,
                    "volume": c.volume, "distance_pct": round(c.distance_pct, 2),
                    "tradingsymbol": c.tradingsymbol,
                }
                for c in chain.calls if c.ltp > 0
            ][:15],  # Top 15
            "puts": [
                {
                    "strike": p.strike, "ltp": p.ltp, "oi": p.oi,
                    "volume": p.volume, "distance_pct": round(p.distance_pct, 2),
                    "tradingsymbol": p.tradingsymbol,
                }
                for p in chain.puts if p.ltp > 0
            ][:15],
        })

    except Exception as exc:
        logger.exception("Option chain fetch failed")
        return json.dumps({"error": str(exc)})


@mcp.tool()
async def suggest_option_trade(
    symbol: str = "NIFTY",
    strategy: str = "SHORT_PUT",
    expiry_index: int = 0,
) -> str:
    """Rule-based option trade suggestion. Analyzes the chain and recommends
    the optimal strike to sell with full risk metrics.

    Strategies:
    - SHORT_PUT: Sell OTM put (bullish view, profit from theta)
    - SHORT_CALL: Sell OTM call (bearish view, profit from theta)
    - SHORT_STRANGLE: Sell OTM put + call (neutral view, max theta)

    Args:
        symbol: Underlying (NIFTY, BANKNIFTY, RELIANCE, etc.)
        strategy: SHORT_PUT, SHORT_CALL, or SHORT_STRANGLE
        expiry_index: 0 = nearest expiry, 1 = next week
    """
    try:
        from skopaq.options.chain import load_option_chain
        from skopaq.options.strategy import (
            format_trade_for_telegram,
            select_short_call,
            select_short_put,
            select_short_strangle,
        )

        chain = await load_option_chain(symbol, expiry_index, config=_get_config())

        if strategy == "SHORT_PUT":
            trade = select_short_put(chain)
        elif strategy == "SHORT_CALL":
            trade = select_short_call(chain)
        elif strategy == "SHORT_STRANGLE":
            trade = select_short_strangle(chain)
        else:
            return json.dumps({"error": f"Unknown strategy: {strategy}"})

        if not trade:
            return json.dumps({"error": f"No suitable {strategy} trade found for {symbol}"})

        return format_trade_for_telegram(trade)

    except Exception as exc:
        logger.exception("Option trade suggestion failed")
        return json.dumps({"error": str(exc)})


# ── Data Gathering Tools ─────────────────────────────────────────────────────
# Raw market, news, fundamentals and social data for an MCP client to read.


def _setup_dataflow_config():
    """Ensure the upstream dataflow config is set for route_to_vendor."""
    from tradingagents.dataflows.config import set_config

    config = _get_config()
    is_crypto = config.asset_class == "crypto"
    if is_crypto:  # crypto is not on INDstocks (experimental asset class)
        upstream = {"data_vendors": {"core_stock_apis": "yfinance"},
                    "yfinance_symbol_suffix": ""}
    else:  # NSE/BSE: the INDstocks API only
        upstream = {"data_vendors": {"core_stock_apis": "indstocks"}}
    set_config(upstream)
    return upstream


def _indicator_report(candles: list[dict]) -> dict:
    """RSI, MACD, Bollinger Bands, SMA 50/200, EMA 10, ATR and VWMA 20 of the last daily
    candle, computed from INDstocks candles (``skopaq.market.indicators``)."""
    from types import SimpleNamespace

    from skopaq.market import indicators as ind

    bars = [SimpleNamespace(open=float(c["o"]), high=float(c["h"]), low=float(c["l"]),
                            close=float(c["c"]), volume=float(c.get("v") or 0))
            for c in candles if c.get("c")]
    closes = [b.close for b in bars]

    def r(x):
        return None if x is None else round(float(x), 4)

    macd = ind.macd(closes)
    boll = ind.bollinger(closes)
    tail = bars[-20:]
    vol = sum(b.volume for b in tail)
    return {
        "rsi": r(ind.rsi(closes)),
        "macd": r(macd.macd) if macd else None,
        "macds": r(macd.signal) if macd else None,
        "macdh": r(macd.histogram) if macd else None,
        "boll": r(boll.middle) if boll else None,
        "boll_ub": r(boll.upper) if boll else None,
        "boll_lb": r(boll.lower) if boll else None,
        "atr": r(ind.atr(bars)),
        "close_50_sma": r(ind.sma(closes, 50)),
        "close_200_sma": r(ind.sma(closes, 200)),
        "close_10_ema": r(ind.ema(closes, 10)),
        "vwma": r(sum(b.close * b.volume for b in tail) / vol) if len(tail) == 20 and vol
        else None,
    }


@mcp.tool()
async def gather_market_data(symbol: str, date: str = "") -> str:
    """Daily OHLCV and the key technical indicators for a symbol, from the INDstocks API.

    Returns the last 60 daily candles + RSI, MACD, Bollinger Bands, SMA, EMA, ATR, VWMA
    (computed from INDstocks candles), for your own review.

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS).
        date: Kept for compatibility; the data is always the latest INDstocks history.
    """
    from skopaq.broker import live_quotes

    try:
        data = await live_quotes.get_history(symbol, "1y")
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": str(e), "source": "indstocks"})
    candles = data["candles"]
    ist = timezone(__import__("datetime").timedelta(hours=5, minutes=30))
    rows = ["Date,Open,High,Low,Close,Volume"] + [
        f"{datetime.fromtimestamp(c['t'], ist):%Y-%m-%d},{c['o']},{c['h']},{c['l']},"
        f"{c['c']},{c.get('v') or 0}"
        for c in candles[-60:]
    ]
    return json.dumps({
        "symbol": symbol,
        "date": date or datetime.now(ist).strftime("%Y-%m-%d"),
        "source": "indstocks",
        "ohlcv": "\n".join(rows)[:3000],
        "indicators": _indicator_report(candles),
    })


_NOT_ON_INDSTOCKS = ("not available: all data comes from the INDstocks API, which "
                     "provides no {what}")


@mcp.tool()
async def gather_news_data(symbol: str, date: str = "") -> str:
    """News and insider transactions: not available (the INDstocks API has no news).

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS).
        date: Unused.
    """
    return json.dumps({
        "symbol": symbol,
        "company_news": _NOT_ON_INDSTOCKS.format(what="news"),
        "global_news": _NOT_ON_INDSTOCKS.format(what="news"),
        "insider_transactions": _NOT_ON_INDSTOCKS.format(what="insider data"),
    })


@mcp.tool()
async def gather_fundamentals_data(symbol: str, date: str = "") -> str:
    """Company financials: not available (the INDstocks API has no fundamentals).

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS).
        date: Unused.
    """
    na = _NOT_ON_INDSTOCKS.format(what="fundamentals")
    return json.dumps({"symbol": symbol, "fundamentals": na, "balance_sheet": na,
                       "cash_flow": na, "income_statement": na})


@mcp.tool()
async def gather_social_data(symbol: str, date: str = "") -> str:
    """Social sentiment: not available (the INDstocks API has no news or social data).

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS).
        date: Unused.
    """
    return json.dumps({"symbol": symbol,
                       "social_sentiment_news": _NOT_ON_INDSTOCKS.format(what="social data")})


@mcp.tool()
async def gather_all_analysis_data(symbol: str, date: str = "") -> str:
    """One-shot data gathering as one bundle: INDstocks market data and indicators (news,
    fundamentals and social data are not on the INDstocks API and say so).

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS).
        date: Trading date YYYY-MM-DD (defaults to today).
    """
    import asyncio

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Fetch all data in parallel
    market_task = gather_market_data(symbol, date)
    news_task = gather_news_data(symbol, date)
    fundamentals_task = gather_fundamentals_data(symbol, date)
    social_task = gather_social_data(symbol, date)

    market, news, fundamentals, social = await asyncio.gather(
        market_task,
        news_task,
        fundamentals_task,
        social_task,
        return_exceptions=True,
    )

    # Convert exceptions to error strings
    for name, val in [
        ("market", market),
        ("news", news),
        ("fundamentals", fundamentals),
        ("social", social),
    ]:
        if isinstance(val, Exception):
            locals()[name] = json.dumps({"error": str(val)})

    return json.dumps(
        {
            "symbol": symbol,
            "date": date,
            "market_data": json.loads(market)
            if isinstance(market, str)
            else {"error": str(market)},
            "news_data": json.loads(news) if isinstance(news, str) else {"error": str(news)},
            "fundamentals_data": json.loads(fundamentals)
            if isinstance(fundamentals, str)
            else {"error": str(fundamentals)},
            "social_data": json.loads(social)
            if isinstance(social, str)
            else {"error": str(social)},
        }
    )


# ── Swing book (Adaptive AI) ─────────────────────────────────────────────────


@mcp.tool()
async def swing_ideas(limit: int = 10, symbols: str = "") -> str:
    """Today's swing ideas: daily setups ranked by the swing model (no orders).

    Args:
        limit: How many ideas.
        symbols: Comma-separated NSE symbols (default the configured universe / NIFTY 50).
    """
    from skopaq.cli.main import _swing_symbols
    from skopaq.swing.engine import SwingEngine

    config = _get_config()
    engine = SwingEngine.from_config(config, paper=config.trading_mode != "live")
    ideas = await engine.scan(_swing_symbols(config, symbols), max(1, min(limit, 50)))
    return json.dumps(
        {
            "model": getattr(engine.model, "version", None),
            "model_state": engine.model_state,
            "threshold": getattr(engine.model, "threshold", None),
            "ideas": [i.as_dict() for i in ideas],
        },
        default=str,
    )


@mcp.tool()
async def swing_idea(symbol: str) -> str:
    """One symbol's swing setup on its last closed daily bar and the model's probability
    (null setup: none fired today).

    Args:
        symbol: NSE symbol (e.g. RELIANCE).
    """
    from skopaq.swing.engine import SwingEngine

    config = _get_config()
    engine = SwingEngine.from_config(config, paper=config.trading_mode != "live")
    idea = await engine.idea(symbol.strip().upper())
    return json.dumps(
        {
            "symbol": symbol.upper(),
            "model": getattr(engine.model, "version", None),
            "threshold": getattr(engine.model, "threshold", None),
            "idea": idea.as_dict() if idea else None,
        },
        default=str,
    )


# ── Strategy Refinement ──────────────────────────────────────────────────────


@mcp.tool()
async def backtest_strategy(
    symbol: str,
    days: int = 365,
    stop_loss_pct: float = 3.0,
    target_pct: float = 6.0,
) -> str:
    """Backtest the AI trading strategy on historical data.

    Runs signals against OHLCV history with realistic slippage and commission.
    Returns Sharpe, Sortino, Calmar, max drawdown, win rate, and profit factor.

    Args:
        symbol: Stock symbol (e.g., RELIANCE, TCS).
        days: Days of history to backtest (default 365).
        stop_loss_pct: Stop-loss percentage (default 3%).
        target_pct: Target percentage (default 6%).
    """
    import asyncio

    try:
        from skopaq.backtest.engine import BacktestConfig, format_backtest_report, run_backtest
        from tradingagents.dataflows.router import route_to_vendor

        _setup_dataflow_config()

        # Fetch historical data
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
        ).strftime("%Y-%m-%d")

        ohlcv_text = await asyncio.to_thread(
            route_to_vendor, "get_stock_data", symbol, start_date, end_date
        )

        # Parse CSV to DataFrame
        import io

        lines = [ln for ln in ohlcv_text.strip().split("\n") if not ln.startswith("#")]
        df = pd.read_csv(io.StringIO("\n".join(lines)))

        if len(df) < 20:
            return f"Insufficient data for {symbol} ({len(df)} bars)"

        # Generate RSI mean-reversion signals
        # Buy when oversold (RSI < 35), sell when overbought (RSI > 65)
        df["RSI"] = _compute_rsi(df["Close"], 14)
        df["SMA_20"] = df["Close"].rolling(20).mean()

        signals = pd.DataFrame(
            {
                "date": df["Date"],
                "signal": 0,
                "confidence": 50,
            }
        )

        for i in range(20, len(df)):
            rsi = df.iloc[i]["RSI"]
            if pd.isna(rsi):
                continue
            if rsi < 35:
                signals.iloc[i, signals.columns.get_loc("signal")] = 1
                signals.iloc[i, signals.columns.get_loc("confidence")] = int(70 + (35 - rsi))
            elif rsi > 65:
                signals.iloc[i, signals.columns.get_loc("signal")] = -1

        config = BacktestConfig(
            stop_loss_pct=stop_loss_pct / 100,
            target_pct=target_pct / 100,
        )

        result = run_backtest(signals, df, config, symbol)
        return format_backtest_report(result)

    except Exception as exc:
        logger.exception("Backtest failed")
        return f"Backtest error: {exc}"


@mcp.tool()
async def run_monte_carlo_test(
    symbol: str,
    days: int = 365,
    simulations: int = 1000,
) -> str:
    """Run Monte Carlo simulation on a strategy's historical trades.

    Shuffles trade order 1000+ times to test if the edge is real or
    just luck from sequence. Shows worst-case drawdown and probability of ruin.

    Args:
        symbol: Stock symbol.
        days: Days of history.
        simulations: Number of scenarios (default 1000).
    """
    try:
        # First run backtest to get trades
        import asyncio
        import io

        from skopaq.backtest.engine import BacktestConfig, run_backtest
        from skopaq.backtest.monte_carlo import format_monte_carlo_report, run_monte_carlo
        from tradingagents.dataflows.router import route_to_vendor

        _setup_dataflow_config()

        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
        ).strftime("%Y-%m-%d")

        ohlcv_text = await asyncio.to_thread(
            route_to_vendor, "get_stock_data", symbol, start_date, end_date
        )

        lines = [ln for ln in ohlcv_text.strip().split("\n") if not ln.startswith("#")]
        df = pd.read_csv(io.StringIO("\n".join(lines)))

        if len(df) < 20:
            return f"Insufficient data for {symbol}"

        # Generate signals + run backtest
        df["SMA_20"] = df["Close"].rolling(20).mean()
        df["RSI"] = _compute_rsi(df["Close"], 14)

        signals = pd.DataFrame({"date": df["Date"], "signal": 0, "confidence": 50})
        for i in range(20, len(df)):
            if df.iloc[i]["RSI"] < 35:
                signals.iloc[i, signals.columns.get_loc("signal")] = 1
            elif df.iloc[i]["RSI"] > 65:
                signals.iloc[i, signals.columns.get_loc("signal")] = -1

        bt_result = run_backtest(signals, df, BacktestConfig(), symbol)

        if len(bt_result.trades) < 5:
            return f"Too few trades ({len(bt_result.trades)}) for Monte Carlo"

        # Run Monte Carlo on trade P&Ls
        trade_pnls = [t.pnl for t in bt_result.trades]
        mc_result = run_monte_carlo(trade_pnls, n_simulations=simulations)

        return format_monte_carlo_report(mc_result)

    except Exception as exc:
        logger.exception("Monte Carlo failed")
        return f"Monte Carlo error: {exc}"


@mcp.tool()
async def get_learning_insights() -> str:
    """Get AI learning insights from past trading performance.

    Analyzes all recorded trades to find:
    - Which stocks the AI is best/worst at trading
    - Confidence calibration (is the AI overconfident or underconfident?)
    - Sector performance (which sectors have edge?)
    - Stop-loss effectiveness (too tight or too loose?)
    - Timing patterns (when do entries work best?)

    These insights should inform future trading decisions.
    """
    try:
        from skopaq.learning.tracker import (
            generate_learning_insights,
            get_confidence_calibration,
            get_sector_performance,
            get_stop_loss_analysis,
            get_timing_patterns,
        )

        insights = generate_learning_insights()

        # Add detailed breakdowns
        sections = [insights, ""]

        calibration = get_confidence_calibration()
        if calibration:
            sections.append("Confidence Calibration:")
            for c in calibration:
                sections.append(
                    f"  {c['confidence_range']}%: stated={c['stated_confidence']:.0f}% "
                    f"actual={c['actual_win_rate']:.0f}% gap={c['calibration_gap']:+.0f}%"
                )

        sectors = get_sector_performance()
        if sectors:
            sections.append("\nSector Performance:")
            for s in sectors:
                sections.append(
                    f"  {s['sector']}: {s['win_rate']:.0f}% win ({s['trades']} trades) "
                    f"P&L={s['total_pnl']:+,.0f}"
                )

        timing = get_timing_patterns()
        if timing:
            sections.append("\nTiming Patterns:")
            for t in timing:
                sections.append(f"  {t['hour']}: {t['win_rate']:.0f}% win ({t['trades']} trades)")

        sl = get_stop_loss_analysis()
        if sl:
            sections.append(f"\nStop-Loss: {sl.get('stop_hit_rate', 0):.0f}% hit rate")

        return "\n".join(sections)

    except Exception as exc:
        return f"Learning insights error: {exc}"


@mcp.tool()
async def get_symbol_stats(symbol: str) -> str:
    """Get AI trading performance stats for a specific symbol.

    Shows win rate, average P&L, confidence accuracy, and holding period
    based on all past trades for this symbol.

    Args:
        symbol: Stock symbol to check performance for.
    """
    try:
        from skopaq.learning.tracker import get_symbol_accuracy

        stats = get_symbol_accuracy(symbol)
        if not stats or stats.get("total_trades", 0) == 0:
            return f"No trading history for {symbol}"

        return (
            f"AI Performance: {symbol}\n\n"
            f"Total Trades: {stats['total_trades']}\n"
            f"Win Rate: {stats['win_rate']:.1f}%\n"
            f"Avg P&L: Rs {stats['avg_pnl']:+,.2f} ({stats['avg_pnl_pct']:+.2f}%)\n"
            f"Avg Confidence: {stats['avg_confidence']:.0f}%\n"
            f"Avg Holding: {stats['avg_holding_days']:.1f} days"
        )

    except Exception as exc:
        return f"Stats error: {exc}"


@mcp.tool()
async def evolve_strategy(symbol: str, days: int = 180) -> str:
    """Run a complete self-evolving strategy cycle.

    Backtests → validates (WFO + Monte Carlo) → adapts parameters →
    deploys if improved → persists results to Postgres → sends Telegram alert.

    This is the AI learning loop — every run makes the strategy smarter.

    Args:
        symbol: Stock symbol to evolve strategy for.
        days: Days of history for backtesting.
    """
    try:
        from skopaq.backtest.evolve import format_evolution_report, run_evolution_cycle

        report = await run_evolution_cycle(symbol, days)
        return format_evolution_report(report)

    except Exception as exc:
        logger.exception("Strategy evolution failed")
        return f"Evolution error: {exc}"


def _compute_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI indicator."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from skopaq import env_overrides

    env_overrides.apply()  # settings saved from the dashboard
    mcp.run(transport="stdio")
