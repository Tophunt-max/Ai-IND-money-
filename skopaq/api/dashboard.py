"""Dashboard API for the web frontend (``frontend/``).

Every endpoint needs ``SKOPAQ_API_TOKEN``: unlike the optional guard on ``/api/chat``,
the dashboard refuses to run (503) when no token is configured, because it exposes
the portfolio, trades, the kill switch and LLM-backed jobs.

- ``GET  /api/dashboard/me``                  token check for the login screen
- ``GET  /api/dashboard/overview``            mode, kill switch, open positions, P&L
- ``GET  /api/dashboard/trades``              recent trades from Supabase
- ``GET  /api/dashboard/report?days=``        track record (``skopaq report``)
- ``GET  /api/dashboard/kill-switch``         halt status
- ``POST /api/dashboard/kill-switch/halt``    stop new BUYs everywhere
- ``POST /api/dashboard/kill-switch/resume``  lift the dashboard/CLI halt
- ``POST /api/dashboard/jobs``                start an ``analyze`` or ``scan`` job
- ``GET  /api/dashboard/jobs``                recent jobs
- ``GET  /api/dashboard/jobs/{id}``           one job (poll until done)

Analyze and scan never place orders: they run the same code as ``skopaq analyze`` and
``skopaq scan``. One job runs at a time (they make many LLM calls).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hmac
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)


def require_dashboard_token(authorization: str = Header(default="")) -> None:
    """401 without the bearer token; 503 when SKOPAQ_API_TOKEN is not configured."""
    expected = SkopaqConfig().api_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Dashboard disabled: set SKOPAQ_API_TOKEN on the server")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        value.strip().encode(), expected.encode()
    ):
        raise HTTPException(
            401, "Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(
    prefix="/api/dashboard", tags=["dashboard"],
    dependencies=[Depends(require_dashboard_token)],
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
async def me() -> dict:
    config = SkopaqConfig()
    return {"ok": True, "mode": config.trading_mode}


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
    return out


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
async def kill_switch_halt(body: HaltRequest) -> dict:
    from skopaq.execution import kill_switch

    where = kill_switch.halt(body.reason or "halted from the dashboard", by="dashboard")
    if not where:
        raise HTTPException(500, "Could not write the halt anywhere")
    return {"written": where, **_halt_dict()}


@router.post("/kill-switch/resume")
async def kill_switch_resume() -> dict:
    from skopaq.execution import kill_switch

    cleared = kill_switch.resume(by="dashboard")
    after = _halt_dict()
    if after["halted"]:
        after["warning"] = ("Still halted: SKOPAQ_TRADING_HALTED is set on the server "
                            "(remove it from ENV_FILE and redeploy)")
    return {"cleared": cleared, **after}


# ── Jobs (analyze, scan) ──────────────────────────────────────────────────────

_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}(\.(NS|BO))?$")
_jobs: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 30
_lock = asyncio.Lock()


class JobRequest(BaseModel):
    kind: Literal["analyze", "scan"]
    symbol: str = ""
    max_candidates: int = Field(5, ge=1, le=20)


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
    }


async def _run_job(job: dict[str, Any]) -> None:
    from skopaq.cli import main as cli

    async with _lock:
        job["status"] = "running"
        job["started_at"] = time.time()
        try:
            # Each job gets its own event loop in a worker thread: the upstream graph's
            # propagate() is synchronous and would block the API for minutes.
            if job["kind"] == "analyze":
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                result = await asyncio.to_thread(
                    asyncio.run, cli._run_analyze(job["symbol"], date))
                job["result"] = _analysis_dict(result)
                job["status"] = "failed" if result.error else "done"
                if result.error:
                    job["error"] = result.error
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
async def start_job(body: JobRequest) -> dict:
    if any(j["status"] in ("queued", "running") for j in _jobs.values()):
        raise HTTPException(409, "Another analysis or scan is still running: wait for it")
    symbol = body.symbol.strip().upper()
    if body.kind == "analyze" and not _SYMBOL.match(symbol):
        raise HTTPException(422, "Give an NSE symbol, e.g. RELIANCE or TCS")
    job = {
        "id": uuid.uuid4().hex[:12], "kind": body.kind, "symbol": symbol,
        "max_candidates": body.max_candidates, "status": "queued",
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
