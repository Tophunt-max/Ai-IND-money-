"""Real-time market data from INDstocks for the web dashboard, Yahoo Finance as fallback.

With a valid INDstocks token, everything on the dashboard's Market page comes from
INDstocks (live during market hours):

- **Stock quotes** and the open positions' marks: ``GET /market/quotes/full``.
- **Index quotes** (``^NSEI`` NIFTY 50, ``^NSEBANK``, ``^INDIAVIX``, ``^BSESN`` ...): the
  index's id from the index instruments file, priced as ``NIDX_<id>`` / ``BIDX_<id>``
  (``skopaq.broker.fno.index_scrip_code`` finds the code that answers).
- **Charts**: ``GET /market/historical/{interval}`` (5-minute candles for 1D, 15-minute
  for 5D, daily up to 1Y, weekly for 5Y), paged within the broker's window per call.

Without a token, for a symbol INDstocks cannot resolve, and when INDstocks fails, the data
comes from Yahoo Finance (``skopaq/broker/yahoo_quotes.py``, can lag several minutes). Each
quote and chart says its ``source``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from skopaq.broker import yahoo_quotes

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_TTL_S = 3.0          # the dashboard polls every 5 s while NSE is open; tabs share a call
_HISTORY_TTL_S = {"1d": 10.0, "5d": 30.0}
_HISTORY_TTL_DAILY_S = 300.0
_TIMEOUT_S = 10.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_history_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_last_error: dict[str, Any] = {"at": 0.0, "text": ""}

# Yahoo index ticker → (exchange, names in the INDstocks index instruments file, label)
INDEX_TICKERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "^NSEI": ("NSE", ("NIFTY 50", "NIFTY")),
    "^NSEBANK": ("NSE", ("NIFTY BANK", "BANKNIFTY")),
    "^INDIAVIX": ("NSE", ("INDIA VIX", "INDIAVIX", "NIFTY VIX")),
    "^CNXFIN": ("NSE", ("NIFTY FIN SERVICE", "NIFTY FINANCIAL SERVICES", "FINNIFTY")),
    "^NSEMDCP50": ("NSE", ("NIFTY MIDCAP 50",)),
    "^CNXIT": ("NSE", ("NIFTY IT",)),
    "^BSESN": ("BSE", ("SENSEX", "BSE SENSEX", "S&P BSE SENSEX")),
}

# Chart range → (INDstocks interval, days of history wanted, days one call may span)
RANGES: dict[str, tuple[str, int, int]] = {
    "1d": ("5minute", 7, 7),          # the last trading day within the week
    "5d": ("15minute", 7, 7),         # the last 5 trading days
    "1mo": ("1day", 31, 365),
    "3mo": ("1day", 92, 365),
    "6mo": ("1day", 183, 365),
    "1y": ("1day", 365, 365),
    "5y": ("1week", 5 * 365, 365),
}


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


def _client():
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager
    from skopaq.config import SkopaqConfig

    return INDstocksClient(SkopaqConfig(), TokenManager())


async def scrip_code(client, symbol: str) -> str:
    """The INDstocks market-data code of a dashboard symbol: an NSE/BSE stock
    (``RELIANCE``, ``RELIANCE.BO``) or a known index (``^NSEI``). Raises when unknown."""
    from skopaq.broker import fno
    from skopaq.broker.scrip_resolver import resolve_scrip_code

    if symbol.startswith("^"):
        if symbol not in INDEX_TICKERS:
            raise LookupError(f"{symbol}: no INDstocks code for this index")
        exchange, names = INDEX_TICKERS[symbol]
        sid = await fno.index_id(client, exchange, names)
        return await fno.index_scrip_code(client, exchange, sid)
    exchange = "BSE" if symbol.endswith(".BO") else "NSE"
    base = symbol.removesuffix(".BO").removesuffix(".NS")
    return await resolve_scrip_code(client, base, exchange)


_codes: dict[str, str] = {}      # symbol → scrip code that answered (process lifetime)


async def _indstocks(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """INDstocks quotes for *symbols* (stocks and known indices)."""
    quotes: dict[str, dict] = {}
    errors: dict[str, str] = {}
    async with _client() as client:
        codes: dict[str, str] = {}
        for sym in symbols:
            try:
                codes[sym] = _codes.get(sym) or await scrip_code(client, sym)
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
                    _codes[sym] = codes[sym]
                else:
                    errors[sym] = "INDstocks returned no price"
                    _codes.pop(sym, None)
    return quotes, errors


def _note_failure(text: str, now: float) -> None:
    if text != _last_error["text"] or now - _last_error["at"] > 300:
        logger.warning(text)
        _last_error.update(at=now, text=text)


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
    todo = [s for s in wanted if s not in quotes]
    live_errors: dict[str, str] = {}
    if todo and await asyncio.to_thread(_token_ok):
        try:
            got, live_errors = await asyncio.wait_for(_indstocks(todo), _TIMEOUT_S)
            for s, q in got.items():
                _cache[s] = (now, q)
            quotes.update(got)
        except Exception as exc:
            text = f"INDstocks quotes failed: {exc}"
            _note_failure(text, now)
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


# ── Charts ───────────────────────────────────────────────────────────────────


def _trim(candles: list[dict], range_: str) -> list[dict]:
    """1D: the last trading day; 5D: the last five trading days."""
    if range_ not in ("1d", "5d") or not candles:
        return candles
    days = sorted({datetime.fromtimestamp(c["t"], _IST).date() for c in candles})
    keep = set(days[-1:] if range_ == "1d" else days[-5:])
    return [c for c in candles if datetime.fromtimestamp(c["t"], _IST).date() in keep]


async def _indstocks_history(symbol: str, range_: str,
                             now: Optional[datetime] = None) -> dict[str, Any]:
    interval, days, window = RANGES[range_]
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows: dict[int, dict] = {}
    async with _client() as client:
        code = _codes.get(symbol) or await scrip_code(client, symbol)
        cursor = end
        while cursor > start:                    # the broker caps the window per call
            begin = max(start, cursor - timedelta(days=window))
            got = await client.get_historical(
                code, interval=interval, start_time=int(begin.timestamp() * 1000),
                end_time=int(cursor.timestamp() * 1000))
            for c in got:
                t = int(c.timestamp.timestamp())
                rows[t] = {"t": t, "o": c.open, "h": c.high, "l": c.low, "c": c.close,
                           "v": int(c.volume or 0)}
            cursor = begin
    candles = _trim([rows[t] for t in sorted(rows)], range_)
    if not candles:
        raise LookupError(f"INDstocks has no {interval} candles for {symbol}")
    _codes[symbol] = code
    return {"symbol": symbol, "ticker": code, "range": range_, "interval": interval,
            "candles": candles, "source": "indstocks"}


async def get_history(symbol: str, range_: str = "3mo") -> dict[str, Any]:
    """Chart candles ``{symbol, range, interval, candles: [{t, o, h, l, c, v}], source}``:
    INDstocks with a token, else (or when it fails) Yahoo Finance. Raises ValueError for a
    bad symbol or range, and LookupError when neither has candles."""
    symbol = yahoo_quotes.normalize(symbol)
    if range_ not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    now = time.monotonic()
    key = (symbol, range_)
    hit = _history_cache.get(key)
    ttl = _HISTORY_TTL_S.get(range_, _HISTORY_TTL_DAILY_S)
    if hit and now - hit[0] < ttl:
        return hit[1]
    live_error = ""
    if await asyncio.to_thread(_token_ok):
        try:
            data = await asyncio.wait_for(_indstocks_history(symbol, range_), 20.0)
            _history_cache[key] = (now, data)
            if len(_history_cache) > 300:
                _history_cache.clear()
            return data
        except Exception as exc:
            live_error = f"INDstocks history failed: {exc}"
            _note_failure(live_error, now)
    data = await asyncio.to_thread(yahoo_quotes.get_history, symbol, range_)
    return {**data, "source": "yahoo",
            **({"fallback_reason": live_error} if live_error else {})}


def source_label(quotes: dict[str, dict]) -> str:
    sources = {q.get("source") for q in quotes.values()}
    if sources == {"indstocks"}:
        return "INDstocks (live)"
    if "indstocks" in sources:
        return "INDstocks (live), Yahoo Finance for the rest"
    return "Yahoo Finance (may be delayed)"
