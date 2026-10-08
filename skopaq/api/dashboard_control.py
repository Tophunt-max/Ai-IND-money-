"""Dashboard control: run the auto-trading engine from the web UI.

Reads (any signed-in user):

- ``GET  /api/dashboard/control``          engine status: mode, scheduler, kill switch,
                                           the running session and its monitored positions
- ``GET  /api/dashboard/control/stream``   the same as Server-Sent Events, every 2 s
- ``GET  /api/dashboard/control/orders``   today's broker order book (live), Skopaq's own
                                           orders marked

Actions (admins; each is logged and sent to Telegram):

- ``POST /control/pause`` / ``/control/resume``  kill switch: no new BUYs / BUYs again
- ``POST /control/auto``                         daily auto sessions on or off
- ``POST /control/start``                        run today's session (or, live, a monitor) now
- ``POST /control/stop``                         stop the running session: it sells what it
                                                 holds (CLOSING), as on a SIGTERM
- ``POST /control/close``                        close one position or all of them
- ``POST /control/plan``                         change a position's stop-loss or target
- ``POST /control/orders``                       a manual BUY / SELL (live needs ``confirm_live``)
- ``POST /control/orders/{id}/cancel``           cancel an open order (live)

With a session running, closes, plan changes and orders go to its monitor through the
control channel (``skopaq/execution/control.py``), so they are booked with the session's
exit plans. Without one, live closes and orders are placed here, through the same
Executor → SafetyChecker → live order worker (SELL locks and the order journal are shared
with every process on the host); paper positions exist only inside a session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from skopaq.api.dashboard_auth import DashboardUser, current_user, require_admin
from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard-control"],
                   dependencies=[Depends(current_user)])

_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,30}$")
_ACTIVE_S = 30.0          # a status older than this means nothing is running
_STREAM_EVERY_S = 2.0
_STREAM_MAX_S = 600.0     # the browser reconnects after this
_COMMAND_WAIT_S = 15.0
_ORDER_WAIT_S = 75.0


def _channel():
    from skopaq.execution.control import ControlChannel

    channel = ControlChannel.from_config(SkopaqConfig())
    if channel is None:
        raise HTTPException(503, "SKOPAQ_CONTROL_DIR is not set on the server")
    return channel


def _symbol(value: str) -> str:
    symbol = (value or "").strip().upper()
    if not _SYMBOL.match(symbol):
        raise HTTPException(422, f"Not a symbol: {value!r}")
    return symbol


async def _notify(text: str) -> None:
    try:
        from skopaq.notifications import notify

        await asyncio.wait_for(notify(text), 15)
    except Exception:
        logger.warning("Control notification not sent", exc_info=True)


def _audit(user: DashboardUser, action: str) -> str:
    logger.warning("DASHBOARD CONTROL by %s: %s", user.email, action)
    return f"🎛️ {action} — from the dashboard by {user.email}"


# ── Status ───────────────────────────────────────────────────────────────────


def _status() -> dict[str, Any]:
    from skopaq.execution import kill_switch
    from skopaq.execution.scheduler import ScheduleSettings

    config = SkopaqConfig()
    out: dict[str, Any] = {"mode": config.trading_mode}
    try:
        settings = ScheduleSettings.from_config(config)
        out["scheduler"] = {"ok": True, "enabled": settings.enabled, "mode": settings.mode,
                            "confirm_live": settings.confirm_live,
                            "start": settings.start.strftime("%H:%M"),
                            "deadline": settings.deadline.strftime("%H:%M")}
    except ValueError as exc:
        out["scheduler"] = {"ok": False, "error": str(exc)}
    halt = kill_switch.status(use_cache=False)
    out["halt"] = {"halted": halt.halted, "reason": halt.reason, "since": halt.since,
                   "source": halt.source, "text": halt.describe()}
    channel = None
    try:
        channel = _channel()
    except HTTPException:
        pass
    session = channel.fresh_status("session", _ACTIVE_S) if channel else None
    monitor = channel.fresh_status("monitor", _ACTIVE_S) if channel else None
    scalper = channel.fresh_status("scalper", _ACTIVE_S) if channel else None
    fno = channel.fresh_status("fno", _ACTIVE_S) if channel else None
    out["session"] = session
    out["monitor"] = monitor
    out["scalper"] = scalper
    out["scalp_enabled"] = config.scalp_enabled
    out["fno"] = fno
    out["fno_enabled"] = config.fno_enabled
    out["fno_instrument"] = config.fno_instrument
    out["active"] = bool(session or monitor or scalper or fno)
    out["pending_commands"] = channel.pending() if channel else 0
    last = channel.read_status("session") if channel else None
    out["last_session"] = last if last and not session else None
    return out


@router.get("/control")
async def control_status() -> dict:
    return await asyncio.to_thread(_status)


@router.get("/control/stream")
async def control_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events: the control status every 2 s, for up to 10 minutes."""

    async def events():
        loop = asyncio.get_running_loop()
        end = loop.time() + _STREAM_MAX_S
        yield "retry: 3000\n\n"
        while loop.time() < end:
            if await request.is_disconnected():
                break
            try:
                data = await asyncio.to_thread(_status)
                yield f"data: {json.dumps(data, default=str)}\n\n"
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(_STREAM_EVERY_S)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Engine ───────────────────────────────────────────────────────────────────


class ReasonBody(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/control/pause")
async def pause(body: ReasonBody, user: DashboardUser = Depends(require_admin)) -> dict:
    """Kill switch on: no new BUYs anywhere; exits keep running."""
    from skopaq.execution import kill_switch

    reason = body.reason.strip() or "paused from the dashboard"
    try:
        await asyncio.to_thread(kill_switch.halt, reason, by=f"dashboard:{user.email}")
    except RuntimeError as exc:
        raise HTTPException(500, f"Not paused: {exc}") from exc
    await _notify(_audit(user, f"⏸ Trading PAUSED (no new BUYs): {reason}"))
    return await control_status()


@router.post("/control/resume")
async def resume(user: DashboardUser = Depends(require_admin)) -> dict:
    from skopaq.execution import kill_switch

    await asyncio.to_thread(kill_switch.resume, by=f"dashboard:{user.email}")
    await _notify(_audit(user, "▶️ Trading RESUMED"))
    return await control_status()


class AutoBody(BaseModel):
    enabled: bool


@router.post("/control/auto")
async def auto_sessions(body: AutoBody, user: DashboardUser = Depends(require_admin)) -> dict:
    """Daily auto sessions on or off (``SKOPAQ_SCHEDULER_ENABLED``). A running session
    is not stopped by turning them off: use Stop."""
    from skopaq import env_overrides

    value = "true" if body.enabled else "false"
    try:
        await asyncio.to_thread(env_overrides.change, {"SKOPAQ_SCHEDULER_ENABLED": value}, [],
                                by=f"dashboard:{user.email}")
    except (PermissionError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    await _notify(_audit(user, f"Auto sessions turned {'ON' if body.enabled else 'OFF'}"))
    return await control_status()


class StartBody(BaseModel):
    job: Literal["daemon", "monitor"] = "daemon"


@router.post("/control/start")
async def start(body: StartBody, user: DashboardUser = Depends(require_admin)) -> dict:
    """Ask the scheduler to run today's session (or, live, ``skopaq monitor``) now."""
    status = await asyncio.to_thread(_status)
    if status["active"]:
        raise HTTPException(409, "A session or monitor is already running")
    sched = status["scheduler"]
    if not sched.get("ok"):
        raise HTTPException(409, f"The scheduler configuration is invalid: {sched.get('error')}")
    if not sched.get("enabled"):
        raise HTTPException(409, "Auto sessions are off: turn them on first (the scheduler "
                                 "service runs the session)")
    channel = _channel()
    await asyncio.to_thread(channel.request_start, f"dashboard:{user.email}", job=body.job)
    label = "session" if body.job == "daemon" else "monitor"
    await _notify(_audit(user, f"🚀 Start {label} requested"))
    return {"requested": body.job,
            "note": "The scheduler starts it within its poll interval (about 30 s), between "
                    "09:00 and 15:00 IST on a trading day; Telegram says if it refuses.",
            **status}


@router.post("/control/stop")
async def stop(body: ReasonBody, user: DashboardUser = Depends(require_admin)) -> dict:
    """Stop the running session or monitor. The daemon then sells every position it
    holds (CLOSING); a standalone monitor sells only from the EOD exit on."""
    status = await asyncio.to_thread(_status)
    if not status["active"]:
        raise HTTPException(409, "No session or monitor is running")
    channel = _channel()
    reason = body.reason.strip() or "stopped from the dashboard"
    await asyncio.to_thread(channel.request_stop, f"dashboard:{user.email}", reason)
    await _notify(_audit(user, f"⏹ Session STOP requested: {reason}"))
    return {"requested": True, **status}


# ── Positions ────────────────────────────────────────────────────────────────


async def _await_result(channel, cmd_id: str, wait_s: float) -> dict:
    loop = asyncio.get_running_loop()
    end = loop.time() + wait_s
    while loop.time() < end:
        res = await asyncio.to_thread(channel.result, cmd_id)
        if res is not None:
            return res
        await asyncio.sleep(0.5)
    return {"id": cmd_id, "ok": None,
            "message": "sent to the session; no answer yet — watch the positions"}


async def _via_session(kind: str, payload: dict, user: DashboardUser,
                       wait_s: float = _COMMAND_WAIT_S, target: str = "monitor"
                       ) -> Optional[dict]:
    """Send a command to the running monitor (or scalper); None when none runs."""
    status = await asyncio.to_thread(_status)
    if not status[target]:
        return None
    channel = _channel()
    cmd_id = await asyncio.to_thread(channel.submit, kind, payload, f"dashboard:{user.email}",
                                     target=target)
    return {"via": "session", **(await _await_result(channel, cmd_id, wait_s))}


class CloseBody(BaseModel):
    symbol: Optional[str] = None   # None: every position
    # CNC positions, the scalper's INTRADAY equity, or the F&O engine's contracts
    scope: Literal["swing", "scalp", "fno"] = "swing"


@router.post("/control/close")
async def close(body: CloseBody, user: DashboardUser = Depends(require_admin)) -> dict:
    symbol = _symbol(body.symbol) if body.symbol else None
    scope = body.scope
    label = {"scalp": " (scalps)", "fno": " (F&O)"}.get(scope, "")
    what = (f"Close {symbol}" if symbol else "CLOSE ALL positions") + label
    await _notify(_audit(user, f"🔻 {what}"))
    kind, payload = ("close", {"symbol": symbol}) if symbol else ("close_all", {})
    target = {"scalp": "scalper", "fno": "fno"}.get(scope, "monitor")
    res = await _via_session(kind, payload, user, target=target)
    if res is not None:
        return res
    config = SkopaqConfig()
    if config.trading_mode != "live":
        raise HTTPException(409, "No session is running: paper positions exist only inside a "
                                 "session")
    product = {"scalp": "INTRADAY", "fno": "FNO"}.get(scope, "CNC")
    return {"via": "api", **(await _live_close(config, symbol, user, product=product))}


class PlanBody(BaseModel):
    symbol: str
    stop_loss: Optional[float] = Field(None, gt=0)
    target: Optional[float] = Field(None, ge=0)   # 0: no target


@router.post("/control/plan")
async def set_plan(body: PlanBody, user: DashboardUser = Depends(require_admin)) -> dict:
    symbol = _symbol(body.symbol)
    if body.stop_loss is None and body.target is None:
        raise HTTPException(422, "Give a stop_loss or a target")
    payload = {"symbol": symbol, "stop_loss": body.stop_loss,
               "target": "off" if body.target == 0 else body.target}
    await _notify(_audit(user, f"✏️ {symbol} exit plan: stop {body.stop_loss}, "
                               f"target {body.target}"))
    res = await _via_session("set_plan", payload, user)
    if res is not None:
        return res
    # No monitor: change today's saved plan, which the next monitor follows
    from skopaq.execution.exit_plan import planner_from_config

    config = SkopaqConfig()
    planner = planner_from_config(config)
    mode = "live" if config.trading_mode == "live" else "paper"
    plan = await asyncio.to_thread(planner.get, mode, symbol)
    if plan is None:
        raise HTTPException(404, f"No exit plan for {symbol} today")
    if body.stop_loss is not None:
        plan.stop_loss = round(body.stop_loss, 2)
    if body.target is not None:
        plan.target = None if body.target == 0 else round(body.target, 2)
    await asyncio.to_thread(planner.save, plan)
    return {"via": "plan", "ok": True,
            "message": f"{symbol}: saved; the next monitor follows it"}


# ── Orders ───────────────────────────────────────────────────────────────────


class OrderBody(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int = Field(..., gt=0, le=100_000)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: Optional[float] = Field(None, gt=0)
    stop_loss: Optional[float] = Field(None, gt=0)
    confirm_live: bool = False


@router.post("/control/orders")
async def place_order(body: OrderBody, user: DashboardUser = Depends(require_admin)) -> dict:
    """A manual order through the safety checks (live: real money, ``confirm_live``)."""
    symbol = _symbol(body.symbol)
    if body.order_type == "LIMIT" and body.price is None:
        raise HTTPException(422, "A LIMIT order needs a price")
    config = SkopaqConfig()
    live = config.trading_mode == "live"
    if live and not body.confirm_live:
        raise HTTPException(409, "Live mode: this is a real-money order — confirm it "
                                 "(type LIVE)")
    desc = (f"{body.side} {body.quantity} {symbol} {body.order_type}"
            + (f" @ {body.price}" if body.price else ""))
    await _notify(_audit(user, f"{'🔴 LIVE ' if live else ''}Manual order: {desc}"))
    payload = body.model_dump(exclude={"confirm_live"})
    payload["symbol"] = symbol
    res = await _via_session("order", payload, user, wait_s=_ORDER_WAIT_S)
    if res is not None:
        return res
    if not live:
        raise HTTPException(409, "No session is running: paper orders are placed inside a "
                                 "session (start one first)")
    return {"via": "api", **(await _live_order(config, payload, user))}


@router.get("/control/orders")
async def orders() -> dict:
    config = SkopaqConfig()
    if config.trading_mode != "live":
        return {"mode": config.trading_mode, "orders": [],
                "note": "Paper orders are filled at once inside the session"}
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.order_status import TERMINAL_STATES, parse_order_book
    from skopaq.broker.token_manager import TokenManager
    from skopaq.execution.order_journal import OrderJournal

    async with INDstocksClient(config, TokenManager()) as client:
        rows = await client.get_order_book()
    own = await asyncio.to_thread(OrderJournal.from_config(config).own_ids_today)
    out = []
    for snap in parse_order_book(rows):
        out.append({
            "order_id": snap.order_id, "side": snap.side, "status": snap.status_raw,
            "state": snap.state.value, "name": snap.name or snap.symbol,
            "product": snap.product, "order_type": snap.order_type,
            "requested": _num(snap.requested_qty), "filled": _num(snap.filled_qty),
            "price": _num(snap.traded_price), "message": snap.message,
            "created_at": snap.created_at.isoformat() if snap.created_at else None,
            "own": snap.order_id in own, "open": snap.state not in TERMINAL_STATES,
        })
    return {"mode": "live", "orders": out}


@router.post("/control/orders/{order_id}/cancel")
async def cancel_order(order_id: str, user: DashboardUser = Depends(require_admin)) -> dict:
    """Cancel an open live order. Skopaq's own orders only while no session runs (a
    running session works them itself: stop it, or close the position)."""
    if not re.match(r"^[A-Za-z0-9\-]{1,40}$", order_id):
        raise HTTPException(422, "Not an order id")
    config = SkopaqConfig()
    if config.trading_mode != "live":
        raise HTTPException(409, "Only live orders can be cancelled")
    from skopaq.broker.client import BrokerError, INDstocksClient
    from skopaq.broker.models import CancelOrderRequest, Segment
    from skopaq.broker.token_manager import TokenManager
    from skopaq.execution.order_journal import OrderJournal

    own = await asyncio.to_thread(OrderJournal.from_config(config).own_ids_today)
    status = await asyncio.to_thread(_status)
    if order_id in own and status["active"]:
        raise HTTPException(409, "This is the running session's own order: it works it "
                                 "itself — stop the session or close the position instead")
    segment = Segment.DERIVATIVE if order_id.upper().startswith("DRV-") else Segment.EQUITY
    await _notify(_audit(user, f"✖️ Cancel order {order_id}"))
    try:
        async with INDstocksClient(config, TokenManager()) as client:
            res = await client.cancel_order(CancelOrderRequest(order_id=order_id,
                                                               segment=segment))
            row = await client.get_order(order_id, segment.value)
    except BrokerError as exc:
        raise HTTPException(502, f"Cancel refused: {exc}") from exc
    return {"ok": True, "order_id": order_id, "status": res.status,
            "now": row.get("status") if isinstance(row, dict) else None}


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Live execution here (no session running) ─────────────────────────────────


class _LiveDesk:
    """Executor → SafetyChecker → live order worker in the API process."""

    def __init__(self, config) -> None:
        from skopaq.broker.client import INDstocksClient
        from skopaq.broker.paper_engine import PaperEngine
        from skopaq.broker.token_manager import TokenManager
        from skopaq.constants import SAFETY_RULES
        from skopaq.execution.executor import Executor
        from skopaq.execution.exit_plan import planner_from_config
        from skopaq.execution.order_router import OrderRouter
        from skopaq.execution.pnl_history import seed_safety_checker
        from skopaq.execution.safety_checker import SafetyChecker

        self.config = config
        self.client = INDstocksClient(config, TokenManager())
        self.router = OrderRouter(config, PaperEngine(initial_capital=1),
                                  live_client=self.client)
        safety = SafetyChecker(rules=SAFETY_RULES,
                               max_sector_concentration_pct=config.max_sector_concentration_pct)
        seed_safety_checker(safety, config)
        self.executor = Executor(self.router, safety, exit_planner=planner_from_config(config))

    async def __aenter__(self) -> "_LiveDesk":
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        from skopaq.execution.order_alerts import get_alerter

        try:
            await self.router.registry.drain_recordings()
            await get_alerter().drain()
        finally:
            await self.client.__aexit__(*exc)

    async def run(self, signal) -> Any:
        from skopaq.cli import main as cli

        result = await self.executor.execute_signal(signal)
        if result.success:
            try:
                await cli._record_exit(self.config, None, None, signal, result)
            except Exception:
                logger.warning("Trade row of %s not written", signal.symbol, exc_info=True)
        return result


def _result_text(result, signal) -> str:
    from skopaq.broker.models import filled_quantity_of

    if not result.success:
        return f"{signal.action} {signal.symbol} not done: {result.rejection_reason}"
    filled = filled_quantity_of(result, signal.quantity or 0)
    return (f"{signal.action} {filled} {signal.symbol} filled at "
            f"{(result.fill_price or signal.entry_price or 0):.2f}")


async def _live_close(config, symbol: Optional[str], user: DashboardUser, *,
                      product: str = "CNC") -> dict:
    """Sell the day's ``product`` positions (CNC: the swing book; INTRADAY: scalps;
    FNO: the F&O positions, INTRADAY or MARGIN — long rows only: a SELL never opens a
    short)."""
    from skopaq.broker.models import OrderType, Product, TradingSignal
    from skopaq.broker.order_status import is_non_cnc_product

    if product == "FNO":
        return await _live_close_fno(config, symbol, user)

    def ours(p) -> bool:
        if product == "CNC":
            return not is_non_cnc_product(p.product)
        return (p.product or "").upper() == product

    done = []
    async with _LiveDesk(config) as desk:
        rows = [p for p in await desk.router.get_positions()
                if p.quantity > 0 and ours(p)
                and (symbol is None or p.symbol.upper() == symbol)]
        if not rows:
            return {"ok": False, "message": f"No open {symbol or 'position'} at the broker"}
        for row in rows:
            ltp = row.last_price or row.average_price
            signal = TradingSignal(
                symbol=row.symbol, action="SELL", confidence=100, entry_price=ltp,
                order_type=OrderType.MARKET, quantity=Decimal(row.quantity),
                reasoning=f"MANUAL CLOSE from the dashboard ({user.email})",
                position_only=product == "CNC",
                product=Product.INTRADAY if product == "INTRADAY" else None)
            result = await desk.run(signal)
            done.append({"symbol": row.symbol, "ok": result.success,
                         "message": _result_text(result, signal)})
    return {"ok": all(d["ok"] for d in done), "message": "; ".join(d["message"] for d in done),
            "results": done, "warning": "No monitor is running: start one to protect what "
                                        "is left" if not all(d["ok"] for d in done) else None}


async def _live_close_fno(config, symbol: Optional[str], user: DashboardUser) -> dict:
    """Sell the F&O positions held long (the whole net quantity, a whole number of lots),
    each with its contract's security id."""
    from skopaq.broker.models import Exchange, OrderType, Product, Segment, TradingSignal

    done = []
    async with _LiveDesk(config) as desk:
        rows = [p for p in await desk.router.get_derivative_positions()
                if p.quantity > 0 and p.security_id
                and (symbol is None or p.symbol.upper() == symbol)]
        if not rows:
            return {"ok": False, "message": f"No open F&O {symbol or 'position'} at the broker"}
        for row in rows:
            ltp = row.last_price or row.average_price
            product = (row.product or "").upper()
            signal = TradingSignal(
                symbol=row.symbol, action="SELL", confidence=100, entry_price=ltp,
                exchange=Exchange.BSE if (row.exchange or "").upper() in ("BSE", "BFO")
                else Exchange.NSE,
                order_type=OrderType.MARKET, quantity=Decimal(row.quantity),
                reasoning=f"MANUAL F&O CLOSE from the dashboard ({user.email})",
                position_only=True, segment=Segment.DERIVATIVE, security_id=row.security_id,
                lot_size=int(getattr(row, "lot_size", 0) or 1),
                product=Product.MARGIN if product == "MARGIN" else Product.INTRADAY)
            result = await desk.run(signal)
            done.append({"symbol": row.symbol, "ok": result.success,
                         "message": _result_text(result, signal)})
    return {"ok": all(d["ok"] for d in done), "message": "; ".join(d["message"] for d in done),
            "results": done}


async def _live_order(config, p: dict, user: DashboardUser) -> dict:
    from skopaq.broker.models import OrderType, TradingSignal
    from skopaq.broker.scrip_resolver import resolve_scrip_code

    async with _LiveDesk(config) as desk:
        scrip = await resolve_scrip_code(desk.client, p["symbol"])
        ltp = await desk.client.get_ltp(scrip)
        limit = p.get("order_type") == "LIMIT"
        ref = float(p["price"]) if limit else float(ltp or 0)
        if ref <= 0:
            return {"ok": False, "message": f"No price for {p['symbol']}"}
        signal = TradingSignal(
            symbol=p["symbol"], action=p["side"], confidence=100, entry_price=ref,
            order_type=OrderType.LIMIT if limit else OrderType.MARKET,
            quantity=Decimal(int(p["quantity"])), stop_loss=p.get("stop_loss"),
            reasoning=f"Manual {p['side']} from the dashboard ({user.email})")
        result = await desk.run(signal)
    out = {"ok": result.success, "message": _result_text(result, signal)}
    if result.success and p["side"] == "BUY":
        out["warning"] = ("No session is running: this position has no automatic stop-loss "
                          "or target until a monitor runs (Start monitor)")
    return out
