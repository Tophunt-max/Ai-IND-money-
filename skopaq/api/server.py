"""FastAPI backend for SkopaqTrader.

Provides REST endpoints for the frontend dashboard and health checks.
Deployed on Railway via ``skopaq serve``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from skopaq import __version__
from skopaq.api.auth import cors_origins, docs_enabled, startup_problems
from skopaq.api.dashboard_auth import current_user
from skopaq.broker.token_manager import TokenManager
from skopaq.config import SkopaqConfig

logger = logging.getLogger(__name__)

# Settings saved from the dashboard (also when uvicorn imports this module directly)
from skopaq import env_overrides  # noqa: E402

env_overrides.apply()



def _validate_startup() -> None:
    """Fail closed: refuse to serve a live configuration without auth or with "*" CORS."""
    problems = startup_problems(SkopaqConfig())
    if problems:
        for problem in problems:
            logger.critical("API not started: %s", problem)
        raise RuntimeError("Unsafe live API configuration: " + "; ".join(problems))


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _validate_startup()
    yield


_docs = docs_enabled(SkopaqConfig())
app = FastAPI(
    title="SkopaqTrader API",
    version=__version__,
    lifespan=_lifespan,
    # Off in live mode unless SKOPAQ_API_DOCS_ENABLED=true: the schema maps every route
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)



# CORS — browser origins allowed to call the API (SKOPAQ_CORS_ORIGINS; default "*"
# keeps the Vercel frontend working). The API uses no cookies, so credentials are
# only allowed with an explicit origin list, never echoed to any site.
_origins = cors_origins(SkopaqConfig())
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Web dashboard (frontend/): Supabase login or SKOPAQ_API_TOKEN ─────────────
from skopaq.api.dashboard import router as dashboard_router  # noqa: E402

app.include_router(dashboard_router)

from skopaq.api.dashboard_tools import router as dashboard_tools_router  # noqa: E402

app.include_router(dashboard_tools_router)

from skopaq.api.dashboard_control import router as dashboard_control_router  # noqa: E402

app.include_router(dashboard_control_router)

from skopaq.api.dashboard_trading import router as dashboard_trading_router  # noqa: E402

app.include_router(dashboard_trading_router)


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@app.get("/health")
async def health(request: Request) -> dict:
    """Health check endpoint (used by Railway and the compose health check).

    Unauthenticated, so it carries no account data. The trading mode and broker-token
    state (which tell a stranger whether real money is behind this API) are only given
    to callers on this host (``scripts/macmini/verify.sh``); others get the status and
    version, and the dashboard reads the rest from ``/api/status`` after login.
    """
    body = {
        "status": "ok",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    host = request.client.host if request.client else ""
    if host in _LOOPBACK:
        config = SkopaqConfig()
        # notify=False: the compose health check polls this every 30 s; a probe sends no
        # Telegram.
        token = await asyncio.to_thread(TokenManager().get_health, notify=False)
        body.update(mode=config.trading_mode, token_valid=token.valid)
    return body


@app.get("/api/status", dependencies=[Depends(current_user)])
async def system_status() -> dict:
    """Detailed system status for the dashboard."""
    config = SkopaqConfig()
    token_health = TokenManager().get_health(notify=False)  # a status read sends no Telegram

    return {
        "version": __version__,
        "mode": config.trading_mode,
        "broker": {
            "name": "INDstocks",
            "base_url": config.indstocks_base_url,
            "token_valid": token_health.valid,
            "token_expires_at": token_health.expires_at.isoformat()
            if token_health.expires_at
            else None,
            "token_remaining": str(token_health.remaining) if token_health.remaining else None,
            "token_warning": token_health.warning or None,
        },
        "services": {
            "supabase": bool(config.supabase_url),
            "redis": bool(config.upstash_redis_url),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/portfolio", dependencies=[Depends(current_user)])
async def portfolio() -> dict:
    """Current portfolio snapshot (paper mode)."""
    from skopaq.broker.paper_engine import PaperEngine

    config = SkopaqConfig()
    paper = PaperEngine(initial_capital=config.initial_paper_capital)
    snapshot = paper.get_snapshot()

    return {
        "total_value": float(snapshot.total_value),
        "cash": float(snapshot.cash),
        "positions_value": float(snapshot.positions_value),
        "day_pnl": float(snapshot.day_pnl),
        "positions": [p.model_dump() for p in snapshot.positions],
        "open_orders": snapshot.open_orders,
        "mode": config.trading_mode,
        "timestamp": snapshot.timestamp.isoformat(),
    }
