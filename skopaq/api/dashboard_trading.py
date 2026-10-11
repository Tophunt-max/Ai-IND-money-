"""Dashboard endpoints for the trading pipeline's evidence and controls.

Reads (any signed-in user):

- ``GET  /api/dashboard/signals``                 signal journal: every candidate, its
                                                  evidence, decision, reasons, outcome
- ``GET  /api/dashboard/risk``                    hard ceilings, working limits, today's
                                                  realized P&L / loss streak, charge schedule
- ``GET  /api/dashboard/control/reconcile``       placements blocking new entries
- ``GET  /api/dashboard/market/analysis``         indicators, patterns, regime, data quality
                                                  of a symbol's candles (chart overlays)
- ``GET  /api/dashboard/learning/models``         model registry: champion, shadow,
                                                  candidates, out-of-sample reports, history

Actions (admins; logged and sent to Telegram):

- ``POST /api/dashboard/control/reconcile/{internal_id}/resolve``  after checking the broker
- ``POST /api/dashboard/learning/models/{version}/promote``        shadow → champion
- ``POST /api/dashboard/learning/rollback``                        previous champion back
- ``POST /api/dashboard/learning/regimes/approve``                  approve the regime map
- ``POST /api/dashboard/finance/contract-note``                     reconcile charges

More reads: ``/control/lifecycle`` (each order's states today), ``/learning/drift``,
``/learning/regimes``, ``/finance/reconciliation``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from skopaq.api.dashboard_auth import DashboardUser, current_user, require_admin
from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"],
                   dependencies=[Depends(current_user)])


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


async def _notify_audit(user: DashboardUser, action: str) -> None:
    from skopaq.api.dashboard_control import _audit, _notify

    await _notify(_audit(user, action))


# ── Signals ──────────────────────────────────────────────────────────────────


@router.get("/signals")
async def signals(days: int = Query(1, ge=1, le=30),
                  status: Optional[str] = Query(None, pattern="^(QUALIFIED|REJECTED|EXPIRED)$"),
                  limit: int = Query(200, ge=1, le=2000),
                  symbol: Optional[str] = Query(None, min_length=1, max_length=40)) -> dict:
    from skopaq.strategy.journal import SignalJournal

    journal = SignalJournal.from_config(SkopaqConfig())
    today = datetime.now(_IST).date()
    wanted = [today - timedelta(days=i) for i in range(days)]

    def read() -> list[dict]:
        rows = journal.signals(sorted(wanted))
        if status:
            rows = [r for r in rows if r.get("status") == status]
        if symbol:
            rows = [r for r in rows if str(r.get("symbol", "")).upper() == symbol.upper()]
        return rows[-limit:]

    rows = await asyncio.to_thread(read)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    return _finite({"signals": list(reversed(rows)), "counts": counts,
                    "mode": journal.mode, "days": days})


# ── Risk ─────────────────────────────────────────────────────────────────────


@router.get("/risk")
async def risk() -> dict:
    from dataclasses import asdict

    from skopaq.constants import DAEMON_SAFETY_RULES, SAFETY_RULES
    from skopaq.execution import kill_switch
    from skopaq.execution.pnl_history import load_realized_pnl
    from skopaq.finance.charges import CURRENT

    config = SkopaqConfig()
    realized = await asyncio.to_thread(load_realized_pnl, config)
    halt = kill_switch.status()
    return _finite({
        "mode": config.trading_mode,
        "kill_switch": {"halted": halt.halted, "reason": halt.describe() if halt.halted
                        else ""},
        "ceilings": asdict(SAFETY_RULES), "daemon_ceilings": asdict(DAEMON_SAFETY_RULES),
        "working": {"max_shares_per_order": config.max_shares_per_order,
                    "max_lots_per_order": config.max_lots_per_order,
                    "max_consecutive_losses": min(config.max_consecutive_losses,
                                                  SAFETY_RULES.max_consecutive_losses),
                    "entry_lock_wait_seconds": config.entry_lock_wait_seconds},
        "realized": None if realized is None else {
            "day": realized.day, "week": realized.week, "month": realized.month,
            "loss_streak": realized.loss_streak,
            "last_loss_at": realized.last_loss_at.isoformat() if realized.last_loss_at
            else None},
        "realized_source": "supabase" if realized is not None else "unavailable",
        "charges": {"schedule": CURRENT.version,
                    "effective_from": CURRENT.effective_from.isoformat(),
                    "source": CURRENT.source, "excluded": list(CURRENT.excluded),
                    "notes": CURRENT.notes},
    })


# ── Reconciliation ───────────────────────────────────────────────────────────


def _journal():
    from skopaq.execution.order_journal import OrderJournal

    return OrderJournal.from_config(SkopaqConfig())


@router.get("/control/reconcile")
async def reconcile_status() -> dict:
    journal = _journal()
    blockers = await asyncio.to_thread(journal.entry_blockers)
    return {"blocking_entries": bool(blockers), "blockers": blockers}


class ResolveBody(BaseModel):
    note: str = Field(..., min_length=5, max_length=300,
                      description="What the broker's order book showed")


@router.post("/control/reconcile/{internal_id}/resolve")
async def reconcile_resolve(internal_id: str, body: ResolveBody,
                            user: DashboardUser = Depends(require_admin)) -> dict:
    if not re.match(r"^[A-Za-z0-9\-]{8,64}$", internal_id):
        raise HTTPException(422, "Not an internal order id")
    journal = _journal()
    blockers = await asyncio.to_thread(journal.entry_blockers)
    if internal_id not in {b.get("internal_id") for b in blockers}:
        raise HTTPException(404, "No unresolved placement with that id today")
    ok = await asyncio.to_thread(journal.resolve, internal_id, by=user.email, note=body.note)
    if not ok:
        raise HTTPException(500, "The order journal could not be written")
    await _notify_audit(user, f"✅ Placement {internal_id[:12]} resolved: {body.note}")
    return {"ok": True, "blockers": await asyncio.to_thread(journal.entry_blockers)}


# ── Market analysis ──────────────────────────────────────────────────────────

_MARKER_PATTERNS = frozenset({
    "bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star", "inverted_hammer",
    "morning_star", "evening_star", "breakout_up", "breakout_down", "breakout_retest",
    "failed_breakout_up", "failed_breakout_down", "support_rejection", "resistance_rejection",
})
_INTERVAL_S = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600,
               "1h": 3600, "1d": 86400, "1wk": 604800}


def _analysis(candles: list[dict], interval: str, source: str) -> dict:
    from skopaq.market import indicators as ind
    from skopaq.market.candles import Candle
    from skopaq.market.patterns import detect
    from skopaq.market.quality import check_series
    from skopaq.market.regime import classify

    bars = [Candle(datetime.fromtimestamp(int(c["t"]), _IST), float(c["o"]), float(c["h"]),
                   float(c["l"]), float(c["c"]),
                   int(c["v"]) if c.get("v") is not None else None)
            for c in candles if all(c.get(k) is not None for k in ("t", "o", "h", "l", "c"))]
    if not bars:
        return {"bars": 0}
    closes = [b.close for b in bars]
    ema9, ema21 = ind.ema_series(closes, 9), ind.ema_series(closes, 21)
    step = _INTERVAL_S.get(interval, 86400)
    rsi = [ind.rsi(closes[:i + 1]) for i in range(len(closes))]
    atr = [ind.atr(bars[max(0, i - 60):i + 1]) for i in range(len(bars))]
    patterns = []
    for i in range(max(1, len(bars) - 120), len(bars)):     # markers for recent bars
        prior = bars[:i]
        lv = ind.support_resistance(prior) if len(prior) >= 5 else ind.Levels(None, None)
        res = detect(bars[:i + 1], support=lv.support, resistance=lv.resistance)
        # Chart markers: the reversal / breakout patterns only (inside bars, dojis and
        # plain pin bars are on most bars and would bury the chart)
        strong = [p.name for p in res.patterns if p.name in _MARKER_PATTERNS]
        if strong:
            patterns.append({"t": int(bars[i].start.timestamp()), "bias": res.bias,
                             "names": strong})
    # Daily bars are not checked for intraday staleness: only their validity/order
    quality = check_series(bars, step, bars[-1].start + timedelta(seconds=step),
                           max_missing=10 ** 6 if step >= 86400 else 3)
    snap = ind.snapshot(bars)
    return {
        "bars": len(bars), "interval": interval, "source": source,
        "last_bar": bars[-1].start.isoformat(),
        "overlays": [{"t": int(b.start.timestamp()), "ema9": ema9[i] if i >= 8 else None,
                      "ema21": ema21[i] if i >= 20 else None, "rsi": rsi[i], "atr": atr[i]}
                     for i, b in enumerate(bars)],
        "patterns": patterns[-40:],
        "indicators": snap.as_dict() if snap else {},
        "regime": classify(bars, data_ok=quality.ok).as_dict(),
        "data_quality": quality.as_dict(),
    }


@router.get("/market/analysis")
async def market_analysis(symbol: str = Query(..., min_length=1, max_length=40),
                          range: str = "3mo") -> dict:  # noqa: A002
    """Deterministic analysis of the chart's candles. Evidence only: no signal, no order."""
    from skopaq.broker import live_quotes, yahoo_quotes

    if range not in yahoo_quotes.RANGES:
        raise HTTPException(422, f"range must be one of {', '.join(yahoo_quotes.RANGES)}")
    try:
        data = await live_quotes.get_history(symbol, range)
    except ValueError as exc:
        raise HTTPException(422, "Unknown symbol or range") from exc
    except Exception as exc:
        logger.warning("analysis history of %s failed: %s", symbol, exc)
        raise HTTPException(502, "Price history unavailable") from exc
    if not data.get("candles"):
        raise HTTPException(404, f"No price history for {symbol}")
    source = data.get("source") or "unknown"
    label = "live" if source == "indstocks" else "delayed"
    out = await asyncio.to_thread(_analysis, data["candles"],
                                  str(data.get("interval") or "1d"), label)
    return _finite({"symbol": data.get("symbol", symbol), "provider": source, **out})


# ── Learning models ──────────────────────────────────────────────────────────


def _registry():
    from skopaq.db.mirror import mirror_from_config
    from skopaq.learning.registry import ModelRegistry, model_dir

    config = SkopaqConfig()
    reg = ModelRegistry(model_dir(config))
    try:
        reg.mirror = mirror_from_config(config)
    except Exception:
        reg.mirror = None
    return reg


@router.get("/learning/models")
async def learning_models() -> dict:
    from skopaq.learning.registry import Gates

    reg = _registry()

    def read() -> dict:
        champ, shadow = reg.champion(), reg.shadow()
        for m in (champ, shadow):
            if m:
                m.pop("model", None)
        gates = Gates()
        return {"champion": champ, "shadow": shadow, "versions": reg.versions(),
                "history": list(reversed(reg.history(50))),
                "gates": gates.__dict__, "directory": str(reg.dir)}

    return _finite(await asyncio.to_thread(read))


class PromoteBody(BaseModel):
    reason: str = Field(..., min_length=5, max_length=300)


@router.post("/learning/models/{version}/promote")
async def learning_promote(version: str, body: PromoteBody,
                           user: DashboardUser = Depends(require_admin)) -> dict:
    if not re.match(r"^[A-Za-z0-9_\-]{3,80}$", version):
        raise HTTPException(422, "Not a model version")
    reg = _registry()
    try:
        entry = await asyncio.to_thread(reg.promote, version, approved_by=user.email,
                                        reason=body.reason)
    except KeyError:
        raise HTTPException(404, "No such model version") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _notify_audit(user, f"🧠 Model {version} promoted to champion: {body.reason}")
    entry.pop("model", None)
    return _finite({"ok": True, "champion": entry})


@router.post("/learning/rollback")
async def learning_rollback(body: PromoteBody,
                            user: DashboardUser = Depends(require_admin)) -> dict:
    reg = _registry()
    to = await asyncio.to_thread(reg.rollback, by=user.email, reason=body.reason)
    await _notify_audit(user, f"↩️ Model rollback to {to or 'none (rules only)'}: "
                              f"{body.reason}")
    return {"ok": True, "champion": to}


# ── Order lifecycle ──────────────────────────────────────────────────────────


@router.get("/control/lifecycle")
async def order_lifecycle() -> dict:
    from skopaq.execution.order_lifecycle import lifecycles

    journal = _journal()
    rows = await asyncio.to_thread(lambda: [lc.as_dict() for lc in lifecycles(journal.entries())])
    open_rows = [r for r in rows if not r["terminal"]]
    return {"orders": list(reversed(rows)), "open": len(open_rows),
            "anomalies": sum(len(r["anomalies"]) for r in rows)}


# ── Charges reconciliation ───────────────────────────────────────────────────


class ContractNoteBody(BaseModel):
    csv: str = Field(..., min_length=10, max_length=2_000_000)
    days: int = Field(7, ge=1, le=60)


@router.post("/finance/contract-note")
async def contract_note(body: ContractNoteBody,
                        user: DashboardUser = Depends(require_admin)) -> dict:
    from skopaq.finance.reconcile import (
        estimates_from_signals,
        parse_contract_note_csv,
        reconcile,
        save_report,
    )
    from skopaq.strategy.journal import SignalJournal

    try:
        notes = parse_contract_note_csv(body.csv)
    except ValueError as exc:
        raise HTTPException(422, f"Contract note not read: {exc}") from exc
    journal = SignalJournal.from_config(SkopaqConfig())
    today = datetime.now(_IST).date()
    days = sorted(today - timedelta(days=i) for i in range(body.days))
    signals = await asyncio.to_thread(journal.signals, days)
    report = reconcile(notes, estimates_from_signals(signals))
    path = await asyncio.to_thread(save_report, report, by=user.email)
    await _notify_audit(user, f"🧾 Contract note reconciled: {len(notes)} row(s), "
                              f"{len(report.discrepancies)} discrepancy(ies)")
    return _finite({"rows": len(notes), "saved": path.name, **report.as_dict()})


@router.get("/finance/reconciliation")
async def reconciliation_reports() -> dict:
    from skopaq.finance.reconcile import saved_reports

    return _finite({"reports": await asyncio.to_thread(saved_reports)})


# ── Drift and regime map ─────────────────────────────────────────────────────


@router.get("/learning/drift")
async def learning_drift(days: int = Query(5, ge=1, le=60)) -> dict:
    """Champion's training features vs the last ``days`` of live/paper signals (PSI)."""
    from skopaq.learning.monitor import drift_now

    return _finite(await asyncio.to_thread(drift_now, SkopaqConfig(), days))


@router.get("/learning/regimes")
async def learning_regimes() -> dict:
    from skopaq.learning.registry import model_dir
    from skopaq.strategy.selector import RegimeMapStore

    store = RegimeMapStore(model_dir(SkopaqConfig()))
    return _finite({"approved": store.approved(), "candidate": store.candidate()})


@router.post("/learning/regimes/approve")
async def learning_regimes_approve(body: PromoteBody,
                                   user: DashboardUser = Depends(require_admin)) -> dict:
    from skopaq.learning.registry import model_dir
    from skopaq.strategy.selector import RegimeMapStore

    store = RegimeMapStore(model_dir(SkopaqConfig()))
    try:
        entry = await asyncio.to_thread(store.approve, by=user.email, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _notify_audit(user, f"🗺️ Regime map {entry['version']} approved: {body.reason}")
    entry.pop("previous", None)
    return _finite({"ok": True, "approved": entry})


@router.post("/learning/regimes/rollback")
async def learning_regimes_rollback(body: PromoteBody,
                                    user: DashboardUser = Depends(require_admin)) -> dict:
    from skopaq.learning.registry import model_dir
    from skopaq.strategy.selector import RegimeMapStore

    store = RegimeMapStore(model_dir(SkopaqConfig()))
    previous = await asyncio.to_thread(store.rollback)
    await _notify_audit(user, f"↩️ Regime map rolled back: {body.reason}")
    return {"ok": True, "approved": (previous or {}).get("version")}
