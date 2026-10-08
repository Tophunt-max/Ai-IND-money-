"""Dashboard API for the web frontend (``frontend/``).

Every endpoint needs a logged-in user (``skopaq/api/dashboard_auth.py``): a Supabase Auth
session of an email listed in ``SKOPAQ_DASHBOARD_USERS``, or ``SKOPAQ_API_TOKEN``. Without
either configured the dashboard answers 503. ``viewer`` accounts may only read; starting
jobs, the kill switch and chat need ``admin``.

- ``GET  /api/dashboard/me``                  the logged-in user and role
- ``GET  /api/dashboard/auth/logins``         login history (own; ``scope=all`` for admins)
- ``POST /api/dashboard/chat``                the AI chat agent (admin)
- ``GET  /api/dashboard/overview``            mode, kill switch, open positions, P&L
- ``GET  /api/dashboard/trades``              recent trades from Supabase
- ``GET  /api/dashboard/report?days=``        track record (``skopaq report``)
- ``GET  /api/dashboard/kill-switch``         halt status
- ``POST /api/dashboard/kill-switch/halt``    stop new BUYs everywhere
- ``POST /api/dashboard/kill-switch/resume``  lift the dashboard/CLI halt
- ``GET  /api/dashboard/settings/env``        SKOPAQ_* settings and their source (admin)
- ``POST /api/dashboard/settings/env``        set / remove dashboard overrides (admin)
- ``POST /api/dashboard/jobs``                start an ``analyze``, paper ``trade``, ``scan``,
  ``backtest``, ``montecarlo`` or ``settle`` job
- ``GET  /api/dashboard/jobs``                recent jobs
- ``GET  /api/dashboard/jobs/{id}``           one job (poll until done)
- ``GET  /api/dashboard/market/{quotes,indices,history,watchlist}``  Yahoo Finance prices
- ``GET  /api/dashboard/pnl-history``         realized P&L per day
- ``GET  /api/dashboard/scheduler[/log]``     today's plan, session markers and logs

Analyze and scan never place orders: they run the same code as ``skopaq analyze`` and
``skopaq scan``. ``trade`` runs ``skopaq trade`` (analysis, safety checks, execution) and
is refused (403) unless the server is in paper mode. One job runs at a time (they make
many LLM calls).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from skopaq.api.dashboard_auth import DashboardUser, current_user, require_admin
from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


router = APIRouter(
    prefix="/api/dashboard", tags=["dashboard"],
    dependencies=[Depends(current_user)],
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Decimals, UUIDs, datetimes, dataclasses and models → plain JSON values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _trade_repository(config: SkopaqConfig):
    if not config.supabase_url or not config.supabase_service_key.get_secret_value():
        raise HTTPException(503, "Supabase is not configured on the server")
    from supabase import create_client

    from skopaq.db.repositories import TradeRepository

    try:
        client = create_client(config.supabase_url, config.supabase_service_key.get_secret_value())
    except Exception as exc:
        raise HTTPException(503, f"Supabase: {exc}") from exc
    return TradeRepository(client)


def _trade_dict(t) -> dict[str, Any]:
    decision = t.agent_decision or {}
    return {
        "id": str(t.id) if t.id else None,
        "symbol": t.symbol,
        "exchange": t.exchange,
        "side": t.side,
        "quantity": float(t.quantity),
        "price": float(t.fill_price or t.price) if (t.fill_price or t.price) else None,
        "status": t.status,
        "is_paper": t.is_paper,
        "pnl": float(t.pnl) if t.pnl is not None else None,
        "confidence": decision.get("confidence"),
        "reason": t.entry_reason or t.exit_reason or "",
        "opening_trade_id": str(t.opening_trade_id) if t.opening_trade_id else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _halt_dict() -> dict[str, Any]:
    from skopaq.execution import kill_switch

    s = kill_switch.status(use_cache=False)
    return {"halted": s.halted, "reason": s.reason, "since": s.since, "source": s.source,
            "text": s.describe()}


# ── Basic ─────────────────────────────────────────────────────────────────────


@router.get("/me")
async def me(user: DashboardUser = Depends(current_user)) -> dict:
    config = SkopaqConfig()
    return {"ok": True, "mode": config.trading_mode, "user": user.public()}


@router.get("/overview")
async def overview() -> dict:
    """Mode, kill switch, open positions (at cost) and realized P&L from Supabase."""
    config = SkopaqConfig()
    is_paper = config.trading_mode != "live"
    out: dict[str, Any] = {
        "mode": config.trading_mode,
        "kill_switch": _halt_dict(),
        "initial_paper_capital": config.initial_paper_capital,
        "positions": [],
        "realized_pnl": 0.0,
        "closed_trades": 0,
        "today_trades": 0,
        "database": "ok",
    }
    try:
        repo = _trade_repository(config)
        recent = [t for t in repo.get_recent(limit=500, is_paper=is_paper)]
    except HTTPException as exc:
        out["database"] = str(exc.detail)
        return out
    except Exception as exc:
        logger.warning("overview: trades not readable", exc_info=True)
        out["database"] = f"error: {exc}"
        return out

    today = datetime.now(timezone.utc).date()
    for t in recent:
        if t.side == "BUY" and t.closed_at is None and t.status == "COMPLETE":
            price = float(t.fill_price or t.price or 0)
            out["positions"].append({
                "symbol": t.symbol, "quantity": float(t.quantity), "entry_price": price,
                "cost": round(price * float(t.quantity), 2),
                "opened_at": t.created_at.isoformat() if t.created_at else None,
                "stop_loss": (t.agent_decision or {}).get("stop_loss"),
                "target": (t.agent_decision or {}).get("target"),
            })
        if t.side == "BUY" and t.closed_at is not None and t.pnl is not None:
            out["realized_pnl"] += float(t.pnl)
            out["closed_trades"] += 1
        if t.created_at and t.created_at.date() == today:
            out["today_trades"] += 1
    out["realized_pnl"] = round(out["realized_pnl"], 2)
    out["invested"] = round(sum(p["cost"] for p in out["positions"]), 2)
    out["unrealized_pnl"] = None
    out["market_value"] = None
    if out["positions"]:
        await _mark_to_market(out)
    return out


async def _mark_to_market(out: dict[str, Any]) -> None:
    """Add prices (INDstocks live, else Yahoo) and unrealized P&L to overview positions."""
    from skopaq.broker import live_quotes

    quotes, errors = await live_quotes.get_quotes(sorted({p["symbol"] for p in out["positions"]}))
    total_value = total_pnl = 0.0
    priced = 0
    for p in out["positions"]:
        q = quotes.get(p["symbol"])
        if not q or q.get("ltp") is None:
            p["ltp"] = p["market_value"] = p["unrealized_pnl"] = p["unrealized_pct"] = None
            p["day_change_pct"] = None
            continue
        value = q["ltp"] * p["quantity"]
        pnl = value - p["cost"]
        p.update(ltp=q["ltp"], market_value=round(value, 2), unrealized_pnl=round(pnl, 2),
                 unrealized_pct=(pnl / p["cost"]) if p["cost"] else None,
                 day_change_pct=q.get("change_pct"))
        total_value += value
        total_pnl += pnl
        priced += 1
    if priced:
        out["market_value"] = round(total_value, 2)
        out["unrealized_pnl"] = round(total_pnl, 2)
    out["price_source"] = live_quotes.source_label(quotes)
    if errors:
        out["price_errors"] = errors


# ── Market data (INDstocks live, Yahoo Finance fallback) ───────────────────────────────────────────────


@router.get("/market/quotes")
async def market_quotes(symbols: str = Query(..., max_length=400)) -> dict:
    """Quotes for up to 25 comma-separated symbols (NSE, or Yahoo indices like ^NSEI)."""
    from skopaq.broker import yahoo_quotes

    wanted = [s.strip() for s in symbols.split(",") if s.strip()][:25]
    if not wanted:
        raise HTTPException(422, "Give at least one symbol")
    bad = []
    for s in wanted:
        try:
            yahoo_quotes.normalize(s)
        except ValueError:
            bad.append(s)
    if bad:
        raise HTTPException(422, f"Not NSE symbols: {', '.join(bad)}")
    from skopaq.broker import live_quotes

    quotes, errors = await live_quotes.get_quotes(wanted)
    return {"quotes": quotes, "errors": errors, "source": live_quotes.source_label(quotes)}


@router.get("/market/indices")
async def market_indices() -> dict:
    """NIFTY 50, Bank NIFTY and India VIX: INDstocks live with a token, else Yahoo."""
    from skopaq.broker import live_quotes, yahoo_quotes

    quotes, errors = await live_quotes.get_quotes(list(yahoo_quotes.INDICES.values()))
    return {"indices": [{"name": name, **quotes[t]} for name, t in yahoo_quotes.INDICES.items()
                        if t in quotes], "errors": errors,
            "source": live_quotes.source_label(quotes)}


@router.get("/market/history")
async def market_history(symbol: str, range: str = "3mo") -> dict:  # noqa: A002
    """Chart candles: INDstocks with a token (live, the current candle included), else
    Yahoo Finance with today's candle moved to the live price when one is known."""
    from skopaq.broker import live_quotes, yahoo_quotes

    if range not in yahoo_quotes.RANGES:
        raise HTTPException(422, f"range must be one of {', '.join(yahoo_quotes.RANGES)}")
    try:
        data = await live_quotes.get_history(symbol, range)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Price history unavailable: {exc}") from exc
    if not data["candles"]:
        raise HTTPException(404, f"No price history for {symbol}")
    if data.get("source") == "indstocks":
        return {**data, "live": True, "ltp": data["candles"][-1]["c"]}
    return await _with_live_candle(data)


async def _with_live_candle(data: dict[str, Any]) -> dict[str, Any]:
    """Move today's last candle to the live INDstocks price (best effort), so the chart
    follows the market between Yahoo refreshes. Yahoo-only quotes change nothing."""
    from skopaq.broker import live_quotes

    data = {**data, "candles": [dict(c) for c in data["candles"]], "live": False}
    symbol = data.get("symbol", "")
    if not symbol:
        return data
    try:
        quotes, _ = await live_quotes.get_quotes([symbol])
    except Exception:
        return data
    q = quotes.get(symbol)
    if not q or q.get("source") != "indstocks" or not q.get("ltp"):
        return data
    ltp = float(q["ltp"])
    last = data["candles"][-1]
    today = datetime.now(_IST).date()
    if datetime.fromtimestamp(last["t"], _IST).date() == today:
        last["c"] = ltp
        last["h"] = max(v for v in (last.get("h"), ltp) if v is not None)
        last["l"] = min(v for v in (last.get("l"), ltp) if v is not None)
    elif data.get("interval") == "1d" and q.get("open"):
        # Yahoo has no bar for today yet: add one from the live quote
        start = datetime.combine(today, datetime.min.time(), tzinfo=_IST)
        data["candles"].append({
            "t": int(start.timestamp()), "o": q["open"], "h": q.get("high") or ltp,
            "l": q.get("low") or ltp, "c": ltp, "v": q.get("volume") or 0})
    else:
        return data
    data["live"] = True
    data["ltp"] = ltp
    return data


@router.get("/market/watchlist")
async def market_watchlist() -> dict:
    from skopaq.scanner.watchlist import NIFTY_50

    return {"symbols": sorted(NIFTY_50)}


# ── P&L history ───────────────────────────────────────────────────────────────


@router.get("/pnl-history")
async def pnl_history(days: int = Query(90, ge=1, le=3650)) -> dict:
    """Realized P&L per day (closed positions) and its running total."""

    config = SkopaqConfig()
    repo = _trade_repository(config)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        closed = repo.get_closed_since(since, is_paper=config.trading_mode != "live")
    except Exception as exc:
        raise HTTPException(502, f"Could not read trades: {exc}") from exc
    by_day: dict[str, float] = {}
    for t in closed:
        if t.closed_at is None or t.pnl is None:
            continue
        day = t.closed_at.astimezone(_IST).date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + float(t.pnl)
    points, running = [], 0.0
    for day in sorted(by_day):
        running += by_day[day]
        points.append({"date": day, "pnl": round(by_day[day], 2), "cumulative": round(running, 2)})
    return {"days": days, "points": points, "total": round(running, 2)}


# ── Scheduler ─────────────────────────────────────────────────────────────────


def _schedule_settings():
    from skopaq.config import SkopaqConfig as _Config
    from skopaq.execution.scheduler import ScheduleSettings

    return ScheduleSettings.from_config(_Config())


@router.get("/scheduler")
async def scheduler_status() -> dict:
    """Today's plan (as `skopaq schedule --check`) and the recent session markers."""

    from skopaq.execution.scheduler import SchedulerState, describe
    from skopaq.risk import calendar as nse_calendar

    try:
        settings = _schedule_settings()
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "lines": [], "days": []}
    now = nse_calendar.now_ist()
    state = SchedulerState(settings.state_dir, settings.log_dir)
    days = []
    for back in range(0, 10):
        day = now.date() - timedelta(days=back)
        jobs = {}
        for job in ("daemon", "monitor", "settle"):
            if state.started(job, day):
                jobs[job] = {"started": state.started_note(job, day),
                             "rc": state.last_exit(job, day)}
        has_log = settings.log_dir is not None and any(
            (settings.log_dir / f"{j}-{day.isoformat()}.log").exists()
            for j in ("daemon", "settle"))
        if jobs or has_log:
            days.append({"date": day.isoformat(), "jobs": jobs, "has_log": has_log})
    return {
        "ok": True,
        "enabled": settings.enabled,
        "mode": settings.mode,
        "lines": describe(settings, now, state),
        "days": days,
    }


@router.get("/scheduler/log")
async def scheduler_log(day: str, job: Literal["daemon", "settle"] = "daemon",
                        lines: int = Query(300, ge=10, le=2000)) -> dict:
    """The last *lines* of one session log (logs/daemon/<job>-<day>.log)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(422, "day must be YYYY-MM-DD")
    try:
        settings = _schedule_settings()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    path = settings.log_dir / f"{job}-{day}.log"
    if not path.is_file():
        raise HTTPException(404, f"No {job} log for {day}")
    from collections import deque

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        tail = list(deque(fh, maxlen=lines))
    return {"day": day, "job": job, "lines": [line.rstrip("\n") for line in tail]}


@router.get("/trades")
async def trades(limit: int = Query(100, ge=1, le=500),
                 mode: Literal["current", "paper", "live", "all"] = "current") -> dict:
    config = SkopaqConfig()
    is_paper: Optional[bool]
    if mode == "current":
        is_paper = config.trading_mode != "live"
    else:
        is_paper = {"paper": True, "live": False, "all": None}[mode]
    repo = _trade_repository(config)
    try:
        rows = repo.get_recent(limit=limit, is_paper=is_paper)
    except Exception as exc:
        raise HTTPException(502, f"Could not read trades: {exc}") from exc
    return {"trades": [_trade_dict(t) for t in rows]}


@router.get("/report")
async def report(days: int = Query(90, ge=1, le=3650)) -> dict:
    from skopaq.learning.report import MIN_SAMPLE, build_report

    try:
        rep = await asyncio.to_thread(build_report, SkopaqConfig(), days)
    except Exception as exc:
        logger.exception("report failed")
        raise HTTPException(500, f"Report failed: {exc}") from exc
    data = _jsonable(rep)
    data["min_sample"] = MIN_SAMPLE
    return data


# ── Kill switch ───────────────────────────────────────────────────────────────


class HaltRequest(BaseModel):
    reason: str = Field("halted from the dashboard", max_length=300)


@router.get("/kill-switch")
async def kill_switch_status() -> dict:
    return _halt_dict()


@router.post("/kill-switch/halt")
async def kill_switch_halt(body: HaltRequest,
                           user: DashboardUser = Depends(require_admin)) -> dict:
    from skopaq.execution import kill_switch

    where = kill_switch.halt(body.reason or "halted from the dashboard",
                             by=f"dashboard:{user.email}")
    if not where:
        raise HTTPException(500, "Could not write the halt anywhere")
    return {"written": where, **_halt_dict()}


@router.post("/kill-switch/resume")
async def kill_switch_resume(user: DashboardUser = Depends(require_admin)) -> dict:
    from skopaq.execution import kill_switch

    cleared = kill_switch.resume(by=f"dashboard:{user.email}")
    after = _halt_dict()
    if after["halted"]:
        after["warning"] = ("Still halted: SKOPAQ_TRADING_HALTED is set on the server "
                            "(remove it from ENV_FILE and redeploy)")
    return {"cleared": cleared, **after}


# ── Environment settings (skopaq/env_overrides.py) ───────────────────────────


class EnvChangeRequest(BaseModel):
    set: dict[str, str] = Field(default_factory=dict, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)
    # Required when the change turns real-money trading on
    confirm_live: bool = False


def _env_payload() -> dict[str, Any]:
    from skopaq import env_overrides

    return {
        "settings": env_overrides.describe(),
        "file": str(env_overrides.overrides_file()),
        "history": env_overrides.history(20),
    }


@router.get("/settings/env")
async def env_settings(user: DashboardUser = Depends(require_admin)) -> dict:
    """Every SKOPAQ_* setting, its value (never a secret's) and its source (admin)."""
    return await asyncio.to_thread(_env_payload)


@router.post("/settings/env")
async def env_settings_change(body: EnvChangeRequest,
                              user: DashboardUser = Depends(require_admin)) -> dict:
    """Add, change or remove dashboard overrides (admin). Turning live trading on needs
    ``confirm_live``; every change is logged and sent to Telegram."""
    from skopaq import env_overrides

    by = f"dashboard:{user.email}"
    try:
        result = await asyncio.to_thread(env_overrides.change, body.set, body.remove,
                                         by=by, confirm_live=body.confirm_live)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        logger.exception("Could not save the dashboard settings")
        raise HTTPException(500, f"Could not save the settings: {exc}") from exc

    if result.set or result.removed:
        parts = []
        if result.set:
            parts.append("set " + ", ".join(result.set))
        if result.removed:
            parts.append("removed " + ", ".join(result.removed))
        text = f"⚙️ Settings changed from the dashboard by {user.email}: {'; '.join(parts)}"
        if result.live:
            text = ("🔴 LIVE TRADING (real money) turned on from the dashboard by "
                    f"{user.email}: {', '.join(result.live)}\n" + text)
        try:
            from skopaq.notifications import notify

            await asyncio.wait_for(notify(text), 15)
        except Exception:
            logger.warning("Settings-change notification failed", exc_info=True)

    payload = await asyncio.to_thread(_env_payload)
    return {"changed": result.set, "removed": result.removed, "live": result.live, **payload}


# ── Jobs (analyze, scan) ──────────────────────────────────────────────────────

_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}(\.(NS|BO))?$")
_jobs: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 30
_lock = asyncio.Lock()


class JobRequest(BaseModel):
    kind: Literal["analyze", "trade", "scan", "backtest", "montecarlo", "settle"]
    symbol: str = ""
    max_candidates: int = Field(5, ge=1, le=20)
    # backtest / montecarlo
    days: int = Field(365, ge=60, le=1825)
    stop_loss_pct: float = Field(3.0, gt=0, le=50)
    target_pct: float = Field(6.0, gt=0, le=100)
    simulations: int = Field(1000, ge=100, le=10000)


_SYMBOL_KINDS = ("analyze", "trade", "backtest", "montecarlo")


def _analysis_dict(result) -> dict[str, Any]:
    sig = result.signal
    reports = {}
    for key, value in (result.agent_state or {}).items():
        if isinstance(value, str) and value.strip():
            reports[key] = value[:20000]
        elif isinstance(value, dict):
            for sub in ("judge_decision", "history"):
                text = value.get(sub)
                if isinstance(text, str) and text.strip():
                    reports[f"{key}.{sub}"] = text[:20000]
    return {
        "symbol": result.symbol,
        "trade_date": result.trade_date,
        "error": result.error,
        "duration_seconds": round(result.duration_seconds or 0, 1),
        "signal": None if sig is None else {
            "action": sig.action, "confidence": sig.confidence,
            "entry_price": sig.entry_price, "stop_loss": sig.stop_loss,
            "target": sig.target, "reasoning": (sig.reasoning or "")[:5000],
        },
        "decision": (result.raw_decision or "")[:20000],
        "reports": reports,
        "execution": _execution_dict(getattr(result, "execution", None)),
    }


def _execution_dict(ex) -> Optional[dict[str, Any]]:
    if ex is None:
        return None
    order = getattr(ex, "order", None)
    return {
        "success": bool(ex.success), "mode": ex.mode, "safety_passed": ex.safety_passed,
        "rejection_reason": ex.rejection_reason or "", "fill_price": ex.fill_price,
        "quantity": _jsonable(getattr(ex.signal, "quantity", None)) if ex.signal else None,
        "order_id": getattr(order, "order_id", None) if order else None,
        "brokerage": ex.brokerage,
    }


async def _run_job(job: dict[str, Any]) -> None:
    from skopaq.cli import main as cli

    async with _lock:
        job["status"] = "running"
        job["started_at"] = time.time()
        try:
            # Each job gets its own event loop in a worker thread: the upstream graph's
            # propagate() is synchronous and would block the API for minutes.
            if job["kind"] in ("analyze", "trade"):
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                runner = cli._run_trade if job["kind"] == "trade" else cli._run_analyze
                result = await asyncio.to_thread(asyncio.run, runner(job["symbol"], date))
                job["result"] = _analysis_dict(result)
                job["status"] = "failed" if result.error else "done"
                if result.error:
                    job["error"] = result.error
            elif job["kind"] in ("backtest", "montecarlo", "settle"):
                from skopaq.api import dashboard_tools as tools

                p = job["params"]
                if job["kind"] == "backtest":
                    result = await asyncio.to_thread(
                        tools.run_backtest_job, job["symbol"], p["days"],
                        p["stop_loss_pct"], p["target_pct"])
                elif job["kind"] == "montecarlo":
                    result = await asyncio.to_thread(
                        tools.run_montecarlo_job, job["symbol"], p["days"], p["simulations"])
                else:
                    result = await asyncio.to_thread(tools.run_settle_job)
                job["result"] = result
                job["status"] = "done"
            else:
                candidates = await asyncio.to_thread(
                    asyncio.run, cli._run_scan(job["max_candidates"]))
                job["result"] = {"candidates": [c.to_dict() for c in (candidates or [])]}
                job["status"] = "done"
        except Exception as exc:
            logger.exception("dashboard job %s failed", job["id"])
            job["status"] = "failed"
            job["error"] = str(exc)
        finally:
            job["finished_at"] = time.time()


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k != "task"}


@router.post("/jobs", status_code=202)
async def start_job(body: JobRequest, user: DashboardUser = Depends(require_admin)) -> dict:
    if any(j["status"] in ("queued", "running") for j in _jobs.values()):
        raise HTTPException(409, "Another job (analysis, scan, backtest, settle) is still "
                                 "running: wait for it")
    symbol = body.symbol.strip().upper()
    if body.kind in _SYMBOL_KINDS and not _SYMBOL.match(symbol):
        raise HTTPException(422, "Give an NSE symbol, e.g. RELIANCE or TCS")
    if body.kind == "trade" and SkopaqConfig().trading_mode != "paper":
        raise HTTPException(403, "Dashboard trades are paper only (the server is in live mode)")
    job = {
        "id": uuid.uuid4().hex[:12], "kind": body.kind,
        "symbol": symbol if body.kind in _SYMBOL_KINDS else "",
        "max_candidates": body.max_candidates, "status": "queued",
        "params": {"days": body.days, "stop_loss_pct": body.stop_loss_pct,
                   "target_pct": body.target_pct, "simulations": body.simulations},
        "by": user.email,
        "created_at": time.time(), "started_at": None, "finished_at": None,
        "result": None, "error": None,
    }
    _jobs[job["id"]] = job
    for old in sorted(_jobs.values(), key=lambda j: j["created_at"])[:-_MAX_JOBS]:
        _jobs.pop(old["id"], None)
    job["task"] = asyncio.create_task(_run_job(job))
    return _public(job)


@router.get("/jobs")
async def list_jobs() -> dict:
    jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return {"jobs": [{k: v for k, v in _public(j).items() if k != "result"} for j in jobs]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (the API may have restarted)")
    return _public(job)


# ── Chat (admin) ──────────────────────────────────────────────────────────────


@router.post("/chat")
async def chat(body: dict, user: DashboardUser = Depends(require_admin)) -> dict:
    """The chat agent of ``/api/chat/message``, behind the dashboard login."""
    from skopaq.chat.bridge import ChatMessageRequest, send_message

    try:
        req = ChatMessageRequest(**body)
    except Exception as exc:
        raise HTTPException(422, f"Bad chat request: {exc}") from exc
    res = await send_message(req)
    return res.model_dump()


# ── Login history ─────────────────────────────────────────────────────────────


@router.get("/auth/logins")
async def logins(limit: int = Query(30, ge=1, le=200),
                 scope: Literal["mine", "all"] = "mine",
                 user: DashboardUser = Depends(current_user)) -> dict:
    """Recent dashboard sign-ins (``dashboard_logins``): your own, or everyone's for admins."""
    if scope == "all" and not user.is_admin:
        raise HTTPException(403, "View-only account: this needs an admin")
    config = SkopaqConfig()
    if not config.supabase_url or not config.supabase_service_key.get_secret_value():
        raise HTTPException(503, "Supabase is not configured on the server")

    def read():
        from supabase import create_client

        client = create_client(config.supabase_url, config.supabase_service_key.get_secret_value())
        q = (client.table("dashboard_logins")
             .select("email,role,status,provider,ip,user_agent,created_at")
             .order("created_at", desc=True).limit(limit))
        if scope == "mine":
            q = q.eq("email", user.email)
        return q.execute().data or []

    try:
        rows = await asyncio.to_thread(read)
    except Exception as exc:
        raise HTTPException(
            502, f"Login history unavailable (run supabase/migrations/004_dashboard_logins.sql): "
                 f"{exc}") from exc
    return {"logins": rows}
