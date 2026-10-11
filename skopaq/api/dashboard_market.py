"""Dashboard market extras: live price stream, NIFTY 50 heatmap, symbol search, price
alerts and the notification feed.

- ``GET    /api/dashboard/market/stream?symbols=``  Server-Sent Events: quotes every 2 s
  while NSE is open (15 s otherwise) for up to 50 symbols, plus any price alert those
  quotes fired. The browser reconnects after 10 minutes.
- ``GET    /api/dashboard/market/heatmap``          NIFTY 50 by sector with the day's change
- ``GET    /api/dashboard/market/search?q=``        NSE symbols (NIFTY 50, indices and the
  INDstocks instruments the server has loaded)
- ``GET    /api/dashboard/alerts``                  price alerts
- ``POST   /api/dashboard/alerts``                  add one (admin)
- ``DELETE /api/dashboard/alerts/{id}``             delete one (admin)
- ``POST   /api/dashboard/alerts/{id}/rearm``       re-arm a fired one (admin)
- ``GET    /api/dashboard/notifications``           the notification feed (fired alerts …)
- ``POST   /api/dashboard/notifications/read``      mark them read

Nothing here places an order.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from skopaq.api.dashboard_auth import DashboardUser, current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"],
                   dependencies=[Depends(current_user)])

_IST = timezone(timedelta(hours=5, minutes=30))
STREAM_OPEN_S = 2.0
STREAM_CLOSED_S = 15.0
STREAM_MAX_S = 600.0
MAX_STREAM_SYMBOLS = 50
_SYMBOL = re.compile(r"^\^?[A-Z0-9&\-.]{1,24}$")

# Display sectors for NIFTY 50 names the risk map files under OTHER (display only: the
# concentration check keeps its own map)
_DISPLAY_SECTOR = {
    "BEL": "CAPITAL_GOODS", "LT": "INFRASTRUCTURE", "TRENT": "RETAIL",
    "ETERNAL": "CONSUMER_TECH", "INDUSINDBK": "BANKING", "KOTAKBANK": "BANKING",
}


def _market_open(now: Optional[datetime] = None) -> bool:
    n = (now or datetime.now(_IST)).astimezone(_IST)
    minutes = n.hour * 60 + n.minute
    return n.weekday() < 5 and 9 * 60 + 15 <= minutes < 15 * 60 + 30


def _symbols(raw: str, limit: int) -> list[str]:
    out: list[str] = []
    for s in raw.split(","):
        s = s.strip().upper()
        if s and _SYMBOL.match(s) and s not in out:
            out.append(s)
    if not out:
        raise HTTPException(422, "Give at least one NSE symbol")
    return out[:limit]


def _store():
    from skopaq.alerts import AlertStore
    from skopaq.config import SkopaqConfig

    return AlertStore.from_config(SkopaqConfig())


def sector(symbol: str) -> str:
    from skopaq.risk.concentration import get_sector

    s = get_sector(symbol)
    return _DISPLAY_SECTOR.get(symbol, s) if s == "OTHER" else s


# ── Live prices ──────────────────────────────────────────────────────────────


async def _tick(symbols: list[str], store) -> dict[str, Any]:
    from skopaq.alerts import notify_fired
    from skopaq.broker import live_quotes

    quotes, errors = await live_quotes.get_quotes(symbols)
    fired = []
    try:
        if store is not None:
            fired = await asyncio.to_thread(store.check, quotes)
            if fired:
                asyncio.create_task(notify_fired(fired))
    except Exception:
        logger.warning("Alert check failed", exc_info=True)
    return {"quotes": quotes, "errors": errors, "source": live_quotes.source_label(quotes),
            "open": _market_open(), "at": datetime.now(timezone.utc).isoformat(),
            "alerts": [a.__dict__ for a in fired]}


@router.get("/market/stream")
async def market_stream(request: Request,
                        symbols: str = Query(..., max_length=1200)) -> StreamingResponse:
    wanted = _symbols(symbols, MAX_STREAM_SYMBOLS)
    try:
        store = _store()
    except Exception:
        store = None

    async def events():
        loop = asyncio.get_running_loop()
        end = loop.time() + STREAM_MAX_S
        yield "retry: 3000\n\n"
        while loop.time() < end:
            if await request.is_disconnected():
                break
            try:
                data = await _tick(wanted, store)
                yield f"data: {json.dumps(data, default=str)}\n\n"
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(STREAM_OPEN_S if _market_open() else STREAM_CLOSED_S)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/market/heatmap")
async def market_heatmap() -> dict:
    """NIFTY 50: every stock's day change and its sector, sectors averaged."""
    from skopaq.broker import live_quotes
    from skopaq.scanner.watchlist import NIFTY_50

    quotes, errors = await live_quotes.get_quotes(list(NIFTY_50))
    stocks = []
    for s in NIFTY_50:
        q = quotes.get(s) or {}
        stocks.append({"symbol": s, "sector": sector(s), "ltp": q.get("ltp"),
                       "change_pct": q.get("change_pct"), "change": q.get("change"),
                       "volume": q.get("volume"),
                       "turnover": (q.get("ltp") or 0) * (q.get("volume") or 0)})
    sectors: dict[str, list[float]] = {}
    for x in stocks:
        if isinstance(x["change_pct"], (int, float)):
            sectors.setdefault(x["sector"], []).append(x["change_pct"])
    by_sector = sorted(({"sector": k, "change_pct": sum(v) / len(v), "count": len(v)}
                        for k, v in sectors.items()), key=lambda r: -r["change_pct"])
    priced = [x for x in stocks if isinstance(x["change_pct"], (int, float))]
    return {"stocks": stocks, "sectors": by_sector,
            "advances": sum(1 for x in priced if x["change_pct"] > 0),
            "declines": sum(1 for x in priced if x["change_pct"] < 0),
            "errors": errors, "source": live_quotes.source_label(quotes)}


@router.get("/market/search")
async def market_search(q: str = Query("", max_length=30),
                        limit: int = Query(12, ge=1, le=30)) -> dict:
    from skopaq.broker import yahoo_quotes
    from skopaq.broker.scrip_resolver import known_symbols
    from skopaq.scanner.watchlist import NIFTY_50

    needle = re.sub(r"[^A-Z0-9&\-.^]", "", q.strip().upper())
    indices = [{"symbol": t, "name": n, "kind": "index"} for n, t in yahoo_quotes.INDICES.items()]
    nifty = set(NIFTY_50)
    pool = sorted(nifty | set(known_symbols("NSE")))
    if not needle:
        return {"results": indices + [{"symbol": s, "name": sector(s), "kind": "stock"}
                                      for s in sorted(nifty)[: max(0, limit - len(indices))]]}
    starts = [s for s in pool if s.startswith(needle)]
    inside = [s for s in pool if needle in s and not s.startswith(needle)]
    ranked = sorted(starts, key=lambda s: (s not in nifty, len(s), s)) + \
        sorted(inside, key=lambda s: (s not in nifty, len(s), s))
    out = [i for i in indices if needle in i["symbol"].upper() or needle in i["name"].upper()]
    out += [{"symbol": s, "name": sector(s) if s in nifty else "NSE", "kind": "stock"}
            for s in ranked]
    return {"results": out[:limit]}


# ── Alerts and notifications ────────────────────────────────────────────────


class AlertBody(BaseModel):
    symbol: str = Field(..., max_length=24)
    op: Literal["above", "below", "pct_up", "pct_down"]
    value: float
    note: str = Field("", max_length=200)


@router.get("/alerts")
async def list_alerts() -> dict:
    store = _store()
    alerts = await asyncio.to_thread(store.alerts)
    return {"alerts": [a.__dict__ | {"text": a.describe()} for a in alerts]}


@router.post("/alerts")
async def add_alert(body: AlertBody, user: DashboardUser = Depends(require_admin)) -> dict:
    store = _store()
    try:
        a = await asyncio.to_thread(store.add, body.symbol, body.op, body.value,
                                    note=body.note, by=user.email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "alert": a.__dict__ | {"text": a.describe()}}


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str, user: DashboardUser = Depends(require_admin)) -> dict:
    if not await asyncio.to_thread(_store().delete, alert_id):
        raise HTTPException(404, "No such alert")
    return {"ok": True}


@router.post("/alerts/{alert_id}/rearm")
async def rearm_alert(alert_id: str, user: DashboardUser = Depends(require_admin)) -> dict:
    a = await asyncio.to_thread(_store().rearm, alert_id)
    if a is None:
        raise HTTPException(404, "No such alert")
    return {"ok": True, "alert": a.__dict__ | {"text": a.describe()}}


@router.get("/notifications")
async def notifications(limit: int = Query(50, ge=1, le=100)) -> dict:
    feed = await asyncio.to_thread(_store().feed, limit)
    return {"items": [n.__dict__ for n in feed], "unread": sum(1 for n in feed if not n.read)}


class ReadBody(BaseModel):
    ids: Optional[list[str]] = Field(None, max_length=100)


@router.post("/notifications/read")
async def mark_read(body: ReadBody) -> dict:
    n = await asyncio.to_thread(_store().mark_read, body.ids)
    return {"ok": True, "marked": n}
