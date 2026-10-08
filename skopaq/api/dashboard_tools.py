"""More dashboard endpoints: broker connection, broker portfolio, scanner status, AI
learning and memory, the option chain, plus the backtest / Monte Carlo / settle job
runners used by ``POST /api/dashboard/jobs`` (``skopaq/api/dashboard.py``).

INDstocks is the only broker. Everything here **reads** except setting / clearing the
INDstocks token (admin). No endpoint places, modifies or cancels an order.

- ``GET    /api/dashboard/broker``                  INDstocks token health
- ``POST   /api/dashboard/broker/indstocks-token``  store today's INDstocks token (admin)
- ``DELETE /api/dashboard/broker/indstocks-token``  delete the stored token (admin)
- ``GET    /api/dashboard/portfolio``               positions (equity, F&O), holdings, funds
- ``GET    /api/dashboard/scanner/status``          background scanner status (not drained)
- ``GET    /api/dashboard/learning``                win rates, calibration, sectors, timing
- ``GET    /api/dashboard/learning/symbol``         one symbol's track record
- ``GET    /api/dashboard/memory``                  agent memories and reflections for a query
- ``GET    /api/dashboard/options/expiries``        upcoming F&O expiries of an underlying
- ``GET    /api/dashboard/options/chain``           option chain with IV and Greeks
- ``GET    /api/dashboard/options/suggest``         an option-selling idea (no order)
- ``POST   /api/dashboard/llm/check``               test the custom AI endpoint (admin)
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from skopaq.api.dashboard_auth import DashboardUser, current_user, require_admin
from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/dashboard", tags=["dashboard"],
    dependencies=[Depends(current_user)],
)

_BROKER_TIMEOUT_S = 20.0


def _jsonable(value: Any) -> Any:
    from skopaq.api.dashboard import _jsonable as base

    if hasattr(value, "to_dict") and hasattr(value, "index"):  # pandas Series / DataFrame
        return None
    out = base(value)
    return _finite(out)


def _finite(value: Any) -> Any:
    """NaN / inf → None (JSON has neither)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


# ── Broker connections ────────────────────────────────────────────────────────


def _indstocks_status() -> dict[str, Any]:
    from skopaq.broker.token_manager import TOKEN_FILE, TokenManager

    health = TokenManager().get_health(notify=False)
    stored = TOKEN_FILE.exists()
    env_token = bool(TokenManager._env_token())
    if not health.valid:
        source = "none"
    elif not stored or "SKOPAQ_INDSTOCKS_TOKEN" in (health.warning or ""):
        source = "env"  # no expiry is known for an env token
    else:
        source = "stored"
    return {
        "valid": health.valid,
        "expires_at": (health.expires_at.isoformat()
                       if health.expires_at and source == "stored" else None),
        "remaining_seconds": (int(health.remaining.total_seconds())
                              if health.remaining and source == "stored" else None),
        "warning": health.warning or "",
        "source": source,
        "stored": stored,
        "env_token": env_token,
        "auto": _auto_status(),
    }


def _auto_status() -> dict[str, Any]:
    """Automatic (TOTP) token: set up or not, and the last generation (no secrets)."""
    from skopaq.broker import auto_token

    config = SkopaqConfig()
    return {"configured": auto_token.configured(config), **auto_token.generation_state()}


@router.get("/broker")
async def broker_status() -> dict:
    """INDstocks token health."""
    config = SkopaqConfig()
    indstocks = await asyncio.to_thread(_indstocks_status)
    return {"mode": config.trading_mode, "indstocks": indstocks}


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=4096)
    ttl_hours: float = Field(24.0, gt=0, le=72)


@router.post("/broker/indstocks-token")
async def set_indstocks_token(body: TokenRequest,
                              user: DashboardUser = Depends(require_admin)) -> dict:
    """Store today's INDstocks token (like ``skopaq token set``)."""
    token = body.token.strip()
    if any(c.isspace() for c in token):
        raise HTTPException(422, "The token must not contain spaces or line breaks")
    from skopaq.broker.token_manager import TokenManager

    try:
        await asyncio.to_thread(TokenManager().set_token, token, body.ttl_hours)
    except Exception as exc:
        logger.exception("Could not store the INDstocks token")
        raise HTTPException(500, f"Could not store the token: {exc}") from exc
    logger.warning("INDstocks token stored from the dashboard by %s", user.email)
    return await broker_status()


class AutoTokenRequest(BaseModel):
    force: bool = False      # a new token even if the stored one looks good (revoked?)


@router.post("/broker/indstocks-token/auto")
async def auto_indstocks_token(body: AutoTokenRequest,
                               user: DashboardUser = Depends(require_admin)) -> dict:
    """Make today's token from TOTP now (like ``skopaq token auto``)."""
    from datetime import datetime, timezone

    from skopaq.broker import auto_token
    from skopaq.execution.readiness import session_end

    config = SkopaqConfig()
    if not auto_token.configured(config):
        raise HTTPException(409, "Automatic token is off: set SKOPAQ_INDSTOCKS_CLIENT_ID, "
                                 "SKOPAQ_INDSTOCKS_MPIN and SKOPAQ_INDSTOCKS_TOTP_SECRET in "
                                 "the server's .env")
    if body.force:
        try:
            await auto_token.generate_token(config)
            message = "A new token was generated with TOTP"
        except auto_token.AutoTokenError as exc:
            raise HTTPException(502, f"Token not made: {exc}") from exc
    else:
        result = await auto_token.ensure_token(config, session_end(datetime.now(timezone.utc)))
        if not result.ok:
            raise HTTPException(502, result.message)
        message = result.message
    logger.warning("INDstocks token (TOTP) requested from the dashboard by %s: %s",
                   user.email, message)
    return {**(await broker_status()), "message": message}


@router.delete("/broker/indstocks-token")
async def clear_indstocks_token(user: DashboardUser = Depends(require_admin)) -> dict:
    """Delete the stored token (like ``skopaq token clear``; an env token stays)."""
    from skopaq.broker.token_manager import TokenManager

    await asyncio.to_thread(TokenManager().clear)
    logger.warning("INDstocks token cleared from the dashboard by %s", user.email)
    return await broker_status()


# ── Broker portfolio (read only) ──────────────────────────────────────────────


def _rows(items) -> list[dict[str, Any]]:
    return [_jsonable(i) for i in (items or [])]


async def _indstocks_portfolio() -> dict[str, Any]:
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager

    manager = TokenManager()
    if not (await asyncio.to_thread(manager.get_health, False)).valid:
        return {"available": False, "error": "No valid INDstocks token: set it on the Broker page"}
    config = SkopaqConfig()
    async with INDstocksClient(config, manager) as client:
        results = await asyncio.gather(
            client.get_positions(), client.get_derivative_positions(), client.get_holdings(),
            client.get_funds(), client.get_order_book(), return_exceptions=True)
    names = ("positions", "fno_positions", "holdings", "funds", "orders")
    out: dict[str, Any] = {"available": True, "errors": {}}
    for name, value in zip(names, results):
        if isinstance(value, Exception):
            out["errors"][name] = str(value)
            out[name] = None if name == "funds" else []
        elif name == "funds":
            out[name] = _jsonable(value)
        else:
            out[name] = _rows(value)
    return out


async def _guard(coro, name: str) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(coro, _BROKER_TIMEOUT_S)
    except HTTPException as exc:
        return {"available": False, "error": exc.detail}
    except asyncio.TimeoutError:
        return {"available": False, "error": f"{name} did not answer in {_BROKER_TIMEOUT_S:.0f}s"}
    except Exception as exc:
        logger.warning("%s portfolio failed", name, exc_info=True)
        return {"available": False, "error": f"{name}: {exc}"}


@router.get("/portfolio")
async def portfolio() -> dict:
    """Positions (equity and F&O), holdings, funds and the order book at INDstocks."""
    indstocks = await _guard(_indstocks_portfolio(), "INDstocks")
    return {"mode": SkopaqConfig().trading_mode, "indstocks": indstocks,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


# ── Scanner ───────────────────────────────────────────────────────────────────


@router.get("/scanner/status")
async def scanner_status() -> dict:
    """The background scanner (SKOPAQ_SCANNER_ENABLED): status and last candidates."""
    from skopaq.api import server

    config = SkopaqConfig()
    engine = server._scanner_engine
    if engine is None:
        return {"enabled": config.scanner_enabled, "running": False, "last_candidates": []}
    status = _jsonable(engine.status)
    status["enabled"] = config.scanner_enabled
    status["last_candidates"] = [c.to_dict() for c in getattr(engine, "_last_candidates", [])]
    return status


# ── Learning & memory ─────────────────────────────────────────────────────────


def _learning() -> dict[str, Any]:
    import os

    from skopaq.learning import tracker

    if not os.environ.get("DATABASE_URL"):
        return {"available": False,
                "error": "Learning data needs DATABASE_URL (the Postgres signal tracker)"}
    return {
        "available": True,
        "insights": tracker.generate_learning_insights(),
        "calibration": tracker.get_confidence_calibration(),
        "sectors": tracker.get_sector_performance(),
        "regimes": tracker.get_regime_performance(),
        "timing": tracker.get_timing_patterns(),
        "stop_loss": tracker.get_stop_loss_analysis(),
    }


@router.get("/learning")
async def learning() -> dict:
    try:
        return _jsonable(await asyncio.to_thread(_learning))
    except Exception as exc:
        logger.warning("learning insights failed", exc_info=True)
        return {"available": False, "error": str(exc)}


@router.get("/learning/symbol")
async def learning_symbol(symbol: str = Query(..., min_length=1, max_length=25)) -> dict:
    from skopaq.learning import tracker

    try:
        return _jsonable(await asyncio.to_thread(tracker.get_symbol_accuracy,
                                                 symbol.strip().upper()))
    except Exception as exc:
        raise HTTPException(502, f"Symbol stats unavailable: {exc}") from exc


def _supabase():
    config = SkopaqConfig()
    if not config.supabase_url or not config.supabase_service_key.get_secret_value():
        raise HTTPException(503, "Supabase is not configured on the server")
    from supabase import create_client

    return config, create_client(config.supabase_url,
                                 config.supabase_service_key.get_secret_value())


@router.get("/memory")
async def memory(q: str = Query(..., min_length=2, max_length=500)) -> dict:
    """Past agent lessons (BM25 over the decision log) and trade reflections for *q*."""
    config, client = _supabase()

    def read() -> dict[str, Any]:
        from skopaq.memory.reflection import recall
        from skopaq.memory.store import MemoryStore

        out: dict[str, Any] = {"memories": {}, "reflections": [], "errors": {}}
        try:
            store = MemoryStore(client, max_entries=config.reflection_max_memory_entries)
            out["memories"] = store.recall(q, n_matches=3)
        except Exception as exc:
            out["errors"]["memories"] = str(exc)
        try:
            out["reflections"] = recall(client, q, limit=10)
        except Exception as exc:
            out["errors"]["reflections"] = str(exc)
        return out

    return _jsonable(await asyncio.to_thread(read))


# ── Options (INDstocks) ───────────────────────────────────────────────────────

_UNDERLYING = Query("NIFTY", min_length=2, max_length=20, pattern=r"^[A-Za-z0-9&\- ]+$")


def _require_token() -> None:
    from skopaq.broker.token_manager import TokenManager

    if not TokenManager().get_health(notify=False).valid:
        raise HTTPException(503, "No valid INDstocks token: set it on the Broker page")


async def _chain(symbol: str, expiry_index: int, strike_count: int = 15):
    from skopaq.options.chain import load_option_chain

    await asyncio.to_thread(_require_token)
    try:
        return await asyncio.wait_for(
            load_option_chain(symbol, expiry_index, strike_count=strike_count), 60)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "INDstocks did not answer in time") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Option chain unavailable: {exc}") from exc


@router.get("/options/expiries")
async def option_expiries(symbol: str = _UNDERLYING) -> dict:
    from skopaq.broker import fno
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager

    await asyncio.to_thread(_require_token)
    name = fno.canonical_underlying(symbol)
    try:
        async with INDstocksClient(SkopaqConfig(), TokenManager()) as client:
            expiries = await asyncio.wait_for(client.get_expiries(name), 30)
    except Exception as exc:
        raise HTTPException(502, f"Expiries unavailable: {exc}") from exc
    return {"symbol": name, "expiries": expiries}


@router.get("/options/chain")
async def option_chain(symbol: str = _UNDERLYING,
                       expiry_index: int = Query(0, ge=0, le=12),
                       strike_count: int = Query(15, ge=1, le=50)) -> dict:
    return _jsonable(await _chain(symbol, expiry_index, strike_count))


@router.get("/options/suggest")
async def option_suggest(
    symbol: str = _UNDERLYING,
    strategy: Literal["SHORT_PUT", "SHORT_CALL", "SHORT_STRANGLE"] = "SHORT_PUT",
    expiry_index: int = Query(0, ge=0, le=12),
) -> dict:
    """An option-selling idea from the chain (rule based, no LLM). Places nothing."""
    from skopaq.options.strategy import select_short_call, select_short_put, select_short_strangle

    chain = await _chain(symbol, expiry_index, 25)
    pick = {"SHORT_PUT": select_short_put, "SHORT_CALL": select_short_call,
            "SHORT_STRANGLE": select_short_strangle}[strategy]
    trade = pick(chain)
    return {"symbol": chain.symbol, "strategy": strategy, "spot_price": chain.spot_price,
            "expiry": _jsonable(chain.expiry), "trade": _jsonable(trade) if trade else None}


# ── Custom AI endpoint check ──────────────────────────────────────────────────


@router.post("/llm/check")
async def llm_check(user: DashboardUser = Depends(require_admin)) -> dict:
    """List the custom endpoint's models and send it one tiny prompt (admin). The key is
    never returned."""
    import httpx

    from skopaq.llm.model_tier import _create_llm, custom_endpoint

    custom = await asyncio.to_thread(custom_endpoint)
    if custom is None:
        raise HTTPException(422, "Set SKOPAQ_CUSTOM_LLM_BASE_URL, SKOPAQ_CUSTOM_LLM_API_KEY and "
                                 "SKOPAQ_CUSTOM_LLM_MODEL first (Environment page)")
    out: dict[str, Any] = {"base_url": custom["base_url"], "model": custom["model"],
                           "judge_model": custom["judge_model"], "models": None,
                           "models_error": None, "ok": False, "reply": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{custom['base_url']}/models",
                                 headers={"Authorization": f"Bearer {custom['api_key']}"})
        if r.status_code == 200:
            data = r.json().get("data", [])
            out["models"] = sorted(str(m.get("id")) for m in data if isinstance(m, dict))[:200]
        else:
            out["models_error"] = f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        out["models_error"] = str(exc)[:300]

    started = time.monotonic()
    try:
        llm = await asyncio.to_thread(_create_llm, "custom", custom["model"])
        msg = await asyncio.wait_for(llm.ainvoke("Reply with exactly: OK"), 60)
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        out.update(ok=True, reply=text.strip()[:200],
                   seconds=round(time.monotonic() - started, 1))
    except Exception as exc:
        out["error"] = str(exc)[:600].replace(custom["api_key"], "***")
    return out


# ── Job runners (backtest, Monte Carlo, settle) ───────────────────────────────


def _setup_dataflow() -> None:
    from tradingagents.dataflows.config import set_config

    is_crypto = SkopaqConfig().asset_class == "crypto"
    set_config({
        "data_vendors": {
            "core_stock_apis": "yfinance" if is_crypto else "indstocks,yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
        },
        "yfinance_symbol_suffix": "" if is_crypto else ".NS",
    })


def _rsi(prices, period: int = 14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    return 100 - (100 / (1 + gain / loss))


def _history(symbol: str, days: int):
    import pandas as pd

    from tradingagents.dataflows.router import route_to_vendor

    _setup_dataflow()
    now = datetime.now(timezone.utc)
    text = route_to_vendor("get_stock_data", symbol, (now - timedelta(days=days)).strftime(
        "%Y-%m-%d"), now.strftime("%Y-%m-%d"))
    lines = [ln for ln in str(text).strip().split("\n") if not ln.startswith("#")]
    df = pd.read_csv(io.StringIO("\n".join(lines)))
    if len(df) < 20 or "Close" not in df:
        raise ValueError(f"Not enough price history for {symbol} ({len(df)} bars)")
    return df


def _rsi_signals(df):
    """The MCP backtest's RSI mean-reversion signals: buy < 35, sell > 65."""
    import pandas as pd

    df = df.copy()
    df["RSI"] = _rsi(df["Close"], 14)
    signals = pd.DataFrame({"date": df["Date"], "signal": 0, "confidence": 50})
    for i in range(20, len(df)):
        rsi = df.iloc[i]["RSI"]
        if pd.isna(rsi):
            continue
        if rsi < 35:
            signals.iloc[i, signals.columns.get_loc("signal")] = 1
            signals.iloc[i, signals.columns.get_loc("confidence")] = int(70 + (35 - rsi))
        elif rsi > 65:
            signals.iloc[i, signals.columns.get_loc("signal")] = -1
    return df, signals


def _curve(series, points: int = 200) -> list[dict[str, Any]]:
    if series is None or len(series) == 0:
        return []
    step = max(1, math.ceil(len(series) / points))
    out = []
    for idx, value in list(series.items())[::step]:
        out.append({"date": str(idx)[:10], "value": round(float(value), 2)})
    last_idx, last = list(series.items())[-1]
    if out and out[-1]["date"] != str(last_idx)[:10]:
        out.append({"date": str(last_idx)[:10], "value": round(float(last), 2)})
    return out


def run_backtest_job(symbol: str, days: int, stop_loss_pct: float,
                     target_pct: float) -> dict[str, Any]:
    """RSI mean-reversion backtest (same as the MCP ``backtest_strategy``). CPU only."""
    from skopaq.backtest.engine import BacktestConfig, run_backtest

    df, signals = _rsi_signals(_history(symbol, days))
    result = run_backtest(signals, df, BacktestConfig(stop_loss_pct=stop_loss_pct / 100,
                                                      target_pct=target_pct / 100), symbol)
    metrics = {name: getattr(result, name) for name in (
        "total_return_pct", "annual_return_pct", "sharpe_ratio", "sortino_ratio",
        "calmar_ratio", "max_drawdown_pct", "max_drawdown_duration_days", "win_rate_pct",
        "profit_factor", "avg_win", "avg_loss", "total_trades", "winning_trades",
        "losing_trades", "var_95", "cvar_95", "total_bars")}
    return _finite({
        "symbol": symbol, "days": days, "strategy": "RSI(14) mean reversion: buy < 35, sell > 65",
        "stop_loss_pct": stop_loss_pct, "target_pct": target_pct,
        "start_date": str(result.start_date)[:10], "end_date": str(result.end_date)[:10],
        "metrics": _jsonable(metrics),
        "equity_curve": _curve(result.equity_curve),
        "trades": [_jsonable(t) for t in result.trades[-100:]],
    })


def run_montecarlo_job(symbol: str, days: int, simulations: int) -> dict[str, Any]:
    """Shuffle the backtest's trades *simulations* times (MCP ``run_monte_carlo_test``)."""
    from skopaq.backtest.engine import BacktestConfig, run_backtest
    from skopaq.backtest.monte_carlo import run_monte_carlo

    df, signals = _rsi_signals(_history(symbol, days))
    bt = run_backtest(signals, df, BacktestConfig(), symbol)
    if len(bt.trades) < 5:
        raise ValueError(f"Too few trades ({len(bt.trades)}) for Monte Carlo: try more days")
    mc = run_monte_carlo([t.pnl for t in bt.trades], n_simulations=simulations, seed=42)
    data = _jsonable(mc)
    finals = sorted(data.pop("all_final_returns", None) or [])
    data.pop("all_max_drawdowns", None)
    # A 20-bin histogram of the final returns instead of every simulation
    hist: list[dict[str, Any]] = []
    if finals:
        lo, hi = finals[0], finals[-1]
        if hi - lo < 0.01:  # every order of the same trades ends at the same return
            hist = [{"from": round(lo, 2), "to": round(hi, 2), "count": len(finals)}]
        else:
            width = (hi - lo) / 20
            counts = [0] * 20
            for v in finals:
                counts[min(19, int((v - lo) / width))] += 1
            hist = [{"from": round(lo + i * width, 2), "to": round(lo + (i + 1) * width, 2),
                     "count": c} for i, c in enumerate(counts)]
    return _finite({"symbol": symbol, "days": days, "trades": len(bt.trades),
                    "result": data, "histogram": hist})


def run_settle_job() -> dict[str, Any]:
    """Settle past decisions whose holding window has traded (``skopaq settle``).
    Makes LLM reflection calls and writes the decision log; places no orders."""
    from skopaq.cli import main as cli
    from skopaq.graph.skopaq_graph import SkopaqTradingGraph

    config = SkopaqConfig()
    graph = SkopaqTradingGraph(cli._build_upstream_config(config), executor=None,
                               memory_store=cli._create_memory_store(config))
    return {"settled": graph.settle_due()}

