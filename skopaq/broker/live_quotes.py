"""Market data from the INDstocks API — the only source of NSE/BSE prices in SkopaqTrader.

Everything that needs a price, a candle or an index level comes through here (or straight
from ``INDstocksClient``): the dashboard, the swing engine and its training history, the
market regime (NIFTY 50, India VIX), ATR for position sizing, the executor's entry price,
backtests and the MCP tools.

- **Stock quotes** and the open positions' marks: ``GET /market/quotes/full``.
- **Index quotes** (``^NSEI`` NIFTY 50, ``^NSEBANK``, ``^INDIAVIX``, ``^BSESN`` ...): the
  index's id from the index instruments file, priced as ``NIDX_<id>`` / ``BIDX_<id>``
  (``skopaq.broker.fno.index_scrip_code`` finds the code that answers).
- **Candles**: ``GET /market/historical/{interval}``, paged by date range within the
  broker's maximum window per call (7 days for minute candles, 1 year for daily and weekly:
  https://api-docs.indstocks.com/historicalData/). ``daily_history`` walks back year by
  year for multi-year training data.

There is no other vendor: without a valid INDstocks token (``skopaq token set`` or
``skopaq token auto``) quotes come back as errors and history raises ``LookupError``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_IST = timezone(timedelta(hours=5, minutes=30))
_TTL_S = 3.0          # the dashboard polls every 5 s while NSE is open; tabs share a call
_HISTORY_TTL_S = {"1d": 10.0, "5d": 30.0}
_HISTORY_TTL_DAILY_S = 300.0
_TIMEOUT_S = 10.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_history_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_last_error: dict[str, Any] = {"at": 0.0, "text": ""}

NO_TOKEN = ("No valid INDstocks token: run `skopaq token set <token>` or `skopaq token "
            "auto` (all market data comes from the INDstocks API)")

# NSE symbol (RELIANCE, M&M, BAJAJ-AUTO), optional .NS/.BO, or an index ticker (^NSEI).
SYMBOL_RE = re.compile(r"^(\^[A-Z0-9]{2,15}|[A-Z0-9&\-]{1,20}(\.(NS|BO))?)$")

# Dashboard index tiles: label → index ticker
INDICES = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "INDIA VIX": "^INDIAVIX"}

# Index ticker → (exchange, names in the INDstocks index instruments file)
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
_DAILY_WINDOW_DAYS = 365              # 1day: one year per call


def normalize(symbol: str) -> str:
    """User input → our symbol (upper case, no spaces). Raises ValueError if not valid."""
    s = (symbol or "").strip().upper().replace(" ", "")
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Not an NSE symbol: {symbol!r}")
    return s


def run_sync(coro: Awaitable[T], timeout: Optional[float] = None) -> T:
    """Run *coro* to completion from synchronous code, also when called from inside a
    running event loop (it then runs on a short-lived thread with its own loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout(coro, timeout))
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = asyncio.run(_with_timeout(coro, timeout))
        except BaseException as exc:  # re-raised in the caller's thread
            box["error"] = exc

    t = threading.Thread(target=worker, name="indstocks-sync", daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


async def _with_timeout(coro: Awaitable[T], timeout: Optional[float]) -> T:
    return await (asyncio.wait_for(coro, timeout) if timeout else coro)


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
        # A fraction (0.0123 = 1.23 %); INDstocks sends percent
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
    """The INDstocks market-data code of a symbol: an NSE/BSE stock (``RELIANCE``,
    ``RELIANCE.BO``) or a known index (``^NSEI``). Raises when unknown."""
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
    """INDstocks quotes for *symbols* (NSE/BSE symbols or index tickers). Never raises:
    (quotes by symbol, errors by symbol)."""
    wanted = [normalize(s) for s in symbols]
    now = time.monotonic()
    quotes: dict[str, dict] = {}
    for s in wanted:
        hit = _cache.get(s)
        if hit and now - hit[0] < _TTL_S:
            quotes[s] = hit[1]
    todo = [s for s in wanted if s not in quotes]
    errors: dict[str, str] = {}
    if not todo:
        return quotes, errors
    if not await asyncio.to_thread(_token_ok):
        _note_failure(NO_TOKEN, now)
        return quotes, {s: NO_TOKEN for s in todo}
    try:
        got, errors = await asyncio.wait_for(_indstocks(todo), _TIMEOUT_S)
        for s, q in got.items():
            _cache[s] = (now, q)
        quotes.update(got)
    except Exception as exc:
        text = f"INDstocks quotes failed: {exc}"
        _note_failure(text, now)
        errors = {s: text for s in todo}
    if len(_cache) > 500:
        _cache.clear()
    return quotes, errors


async def get_ltp(symbol: str) -> Optional[float]:
    """The INDstocks last price of *symbol*, or ``None`` (no token, unknown, no price)."""
    quotes, _ = await get_quotes([symbol])
    q = quotes.get(normalize(symbol))
    return float(q["ltp"]) if q and q.get("ltp") else None


# ── Candles ──────────────────────────────────────────────────────────────────


def _trim(candles: list[dict], range_: str) -> list[dict]:
    """1D: the last trading day; 5D: the last five trading days."""
    if range_ not in ("1d", "5d") or not candles:
        return candles
    days = sorted({datetime.fromtimestamp(c["t"], _IST).date() for c in candles})
    keep = set(days[-1:] if range_ == "1d" else days[-5:])
    return [c for c in candles if datetime.fromtimestamp(c["t"], _IST).date() in keep]


async def _paged(client, code: str, interval: str, start: datetime, end: datetime,
                 window_days: int, *, stop_on_empty: bool = False) -> list[dict]:
    """Candles of *code* from *start* to *end*, one request per broker window, walking
    back from *end*. ``stop_on_empty``: stop at the first window with no candles (the
    start of the instrument's history), for open-ended multi-year requests."""
    rows: dict[int, dict] = {}
    cursor = end
    while cursor > start:                    # the broker caps the window per call
        begin = max(start, cursor - timedelta(days=window_days))
        got = await client.get_historical(
            code, interval=interval, start_time=int(begin.timestamp() * 1000),
            end_time=int(cursor.timestamp() * 1000))
        for c in got:
            ts = c.timestamp
            if ts.tzinfo is None:            # the client parses ts as a naive local time
                ts = ts.astimezone()
            t = int(ts.timestamp())
            rows[t] = {"t": t, "o": c.open, "h": c.high, "l": c.low, "c": c.close,
                       "v": int(c.volume or 0)}
        if stop_on_empty and not got:
            break
        cursor = begin
    return [rows[t] for t in sorted(rows)]


async def _indstocks_history(symbol: str, range_: str,
                             now: Optional[datetime] = None) -> dict[str, Any]:
    interval, days, window = RANGES[range_]
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    async with _client() as client:
        code = _codes.get(symbol) or await scrip_code(client, symbol)
        rows = await _paged(client, code, interval, start, end, window)
    candles = _trim(rows, range_)
    if not candles:
        raise LookupError(f"INDstocks has no {interval} candles for {symbol}")
    _codes[symbol] = code
    return {"symbol": symbol, "ticker": code, "range": range_, "interval": interval,
            "candles": candles, "source": "indstocks"}


async def get_history(symbol: str, range_: str = "3mo") -> dict[str, Any]:
    """Chart candles ``{symbol, range, interval, candles: [{t, o, h, l, c, v}], source}``
    from INDstocks. Raises ValueError for a bad symbol or range, and LookupError without a
    token or when INDstocks has no candles."""
    symbol = normalize(symbol)
    if range_ not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    now = time.monotonic()
    key = (symbol, range_)
    hit = _history_cache.get(key)
    ttl = _HISTORY_TTL_S.get(range_, _HISTORY_TTL_DAILY_S)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if not await asyncio.to_thread(_token_ok):
        raise LookupError(NO_TOKEN)
    try:
        data = await asyncio.wait_for(_indstocks_history(symbol, range_), 20.0)
    except Exception as exc:
        _note_failure(f"INDstocks history failed for {symbol}: {exc}", now)
        raise LookupError(f"INDstocks history failed for {symbol}: {exc}") from exc
    _history_cache[key] = (now, data)
    if len(_history_cache) > 300:
        _history_cache.clear()
    return data


async def daily_history(symbol: str, days: int = 1825, *,
                        now: Optional[datetime] = None) -> list[dict]:
    """About *days* calendar days of daily candles ``[{t, o, h, l, c, v}]`` (oldest
    first) from INDstocks, one year per request, back to the start of the history.
    Raises LookupError without a token or when there are no candles."""
    symbol = normalize(symbol)
    if days <= 0:
        raise ValueError("days must be positive")
    if not await asyncio.to_thread(_token_ok):
        raise LookupError(NO_TOKEN)
    end = now or datetime.now(timezone.utc)
    async with _client() as client:
        code = _codes.get(symbol) or await scrip_code(client, symbol)
        rows = await _paged(client, code, "1day", end - timedelta(days=days), end,
                            _DAILY_WINDOW_DAYS, stop_on_empty=True)
    if not rows:
        raise LookupError(f"INDstocks has no daily candles for {symbol}")
    _codes[symbol] = code
    return rows


def source_label(quotes: dict[str, dict]) -> str:
    return "INDstocks (live)" if quotes else "INDstocks unavailable"
