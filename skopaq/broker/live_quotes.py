"""Real-time NSE quotes from INDstocks for the web dashboard, Yahoo Finance as fallback.

With a valid INDstocks token the dashboard's stock quotes and the open positions' marks come
from INDstocks (``/market/quotes/full``, live during market hours). Without a token, for
symbols INDstocks cannot resolve, for indices (``^NSEI``: their INDstocks codes are not
documented) and when INDstocks fails, the quote comes from Yahoo Finance
(``skopaq/broker/yahoo_quotes.py``, can lag a few minutes). Each quote says its ``source``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from skopaq.broker import yahoo_quotes

logger = logging.getLogger(__name__)

_TTL_S = 5.0  # the dashboard polls every 15–60 s; several tabs share one call
_TIMEOUT_S = 10.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_last_error: dict[str, Any] = {"at": 0.0, "text": ""}


def _token_ok() -> bool:
    from skopaq.broker.token_manager import TokenManager

    try:
        return TokenManager().get_health(notify=False).valid
    except Exception:
        return False


def _shape(symbol: str, scrip: str, q) -> dict[str, Any] | None:
    ltp = float(q.ltp or 0)
    if ltp <= 0:
        return None
    prev = float(q.close or 0) or None
    change = float(q.change) if q.change else ((ltp - prev) if prev else None)
    pct = q.change_pct
    return {
        "symbol": symbol,
        "ticker": scrip,
        "ltp": ltp,
        "open": float(q.open) or None,
        "high": float(q.high) or None,
        "low": float(q.low) or None,
        "prev_close": prev,
        "change": change,
        # yahoo_quotes uses fractions (0.0123 = 1.23 %); INDstocks sends percent
        "change_pct": (float(pct) / 100) if pct else ((change / prev) if change and prev else None),
        "volume": int(q.volume or 0),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "indstocks",
    }


async def _indstocks(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """INDstocks quotes for NSE equity *symbols* (no indices)."""
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.scrip_resolver import resolve_scrip_code
    from skopaq.broker.token_manager import TokenManager
    from skopaq.config import SkopaqConfig

    quotes: dict[str, dict] = {}
    errors: dict[str, str] = {}
    async with INDstocksClient(SkopaqConfig(), TokenManager()) as client:
        codes: dict[str, str] = {}
        for sym in symbols:
            exchange = "BSE" if sym.endswith(".BO") else "NSE"
            base = sym.removesuffix(".BO").removesuffix(".NS")
            try:
                codes[sym] = await resolve_scrip_code(client, base, exchange)
            except Exception as exc:
                errors[sym] = f"INDstocks: {exc}"
        if codes:
            order = list(codes)
            result = await client.get_quotes([codes[s] for s in order], order)
            by_symbol = {q.symbol: q for q in result}
            for sym in order:
                shaped = _shape(sym, codes[sym], by_symbol[sym]) if sym in by_symbol else None
                if shaped:
                    quotes[sym] = shaped
                else:
                    errors[sym] = "INDstocks returned no price"
    return quotes, errors


async def get_quotes(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """Quotes for *symbols* (normalised NSE symbols or Yahoo indices). Never raises:
    (quotes by symbol, errors by symbol); a symbol INDstocks could not price is tried on
    Yahoo, and only listed in errors if both fail."""
    wanted = [yahoo_quotes.normalize(s) for s in symbols]
    now = time.monotonic()
    quotes: dict[str, dict] = {}
    for s in wanted:
        hit = _cache.get(s)
        if hit and now - hit[0] < _TTL_S:
            quotes[s] = hit[1]
    todo = [s for s in wanted if s not in quotes and not s.startswith("^")]
    live_errors: dict[str, str] = {}
    if todo and await asyncio.to_thread(_token_ok):
        try:
            got, live_errors = await asyncio.wait_for(_indstocks(todo), _TIMEOUT_S)
            for s, q in got.items():
                _cache[s] = (now, q)
            quotes.update(got)
        except Exception as exc:
            text = f"INDstocks quotes failed: {exc}"
            if text != _last_error["text"] or now - _last_error["at"] > 300:
                logger.warning(text)
                _last_error.update(at=now, text=text)
            live_errors = {s: text for s in todo}
    rest = [s for s in wanted if s not in quotes]
    errors: dict[str, str] = {}
    if rest:
        yq, ye = await asyncio.to_thread(yahoo_quotes.get_quotes, rest)
        quotes.update(yq)
        for s in rest:
            if s not in yq:
                errors[s] = "; ".join(e for e in (live_errors.get(s), ye.get(s)) if e)
    if len(_cache) > 500:
        _cache.clear()
    return quotes, errors


def source_label(quotes: dict[str, dict]) -> str:
    sources = {q.get("source") for q in quotes.values()}
    if sources == {"indstocks"}:
        return "INDstocks (live)"
    if "indstocks" in sources:
        return "INDstocks (live), Yahoo Finance for the rest"
    return "Yahoo Finance (may be delayed)"
