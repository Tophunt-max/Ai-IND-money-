"""Quotes and price history from Yahoo Finance (yfinance), no broker token needed.

Used by the web dashboard (prices, charts, unrealized P&L) and as the paper engine's
fallback quote when INDstocks has no token. Yahoo's NSE prices can lag the exchange by
a few minutes: good for paper fills and display, never for live orders.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# NSE symbol (RELIANCE, M&M, BAJAJ-AUTO), optional .NS/.BO, or a Yahoo index (^NSEI).
SYMBOL_RE = re.compile(r"^(\^[A-Z0-9]{2,15}|[A-Z0-9&\-]{1,20}(\.(NS|BO))?)$")

INDICES = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "INDIA VIX": "^INDIAVIX"}

# range → (yfinance period, interval)
RANGES = {
    "1d": ("1d", "5m"),
    "5d": ("5d", "15m"),
    "1mo": ("1mo", "1d"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y": ("1y", "1d"),
    "5y": ("5y", "1wk"),
}

_QUOTE_TTL = 60.0
_HISTORY_TTL = 300.0
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def normalize(symbol: str) -> str:
    """User input → our symbol (upper case, no spaces). Raises ValueError if not valid."""
    s = (symbol or "").strip().upper().replace(" ", "")
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Not an NSE symbol: {symbol!r}")
    return s


def yahoo_ticker(symbol: str) -> str:
    """RELIANCE → RELIANCE.NS; ^NSEI and RELIANCE.BO stay as they are."""
    s = normalize(symbol)
    return s if s.startswith("^") or s.endswith((".NS", ".BO")) else f"{s}.NS"


def _cached(key: tuple, ttl: float, fn):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
        if len(_cache) > 500:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
    return value


def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _history(ticker: str, period: str, interval: str):
    import yfinance as yf

    return yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)


def get_quote(symbol: str) -> dict[str, Any]:
    """Latest daily bar: ltp, open/high/low, previous close, change. Raises on no data."""
    ticker = yahoo_ticker(symbol)

    def fetch():
        h = _history(ticker, "5d", "1d")
        h = h.dropna(subset=["Close"]) if len(h) else h
        if not len(h):
            raise LookupError(f"No price data for {symbol} on Yahoo Finance")
        last = h.iloc[-1]
        prev = _num(h.iloc[-2]["Close"]) if len(h) > 1 else None
        ltp = _num(last["Close"])
        change = (ltp - prev) if (ltp is not None and prev) else None
        return {
            "symbol": normalize(symbol),
            "ticker": ticker,
            "ltp": ltp,
            "open": _num(last["Open"]),
            "high": _num(last["High"]),
            "low": _num(last["Low"]),
            "prev_close": prev,
            "change": change,
            "change_pct": (change / prev) if (change is not None and prev) else None,
            "volume": int(_num(last.get("Volume")) or 0),
            "as_of": h.index[-1].isoformat(),
            "source": "yahoo",
        }

    return _cached(("q", ticker), _QUOTE_TTL, fetch)


def get_quotes(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """Quotes for several symbols: (quotes by symbol, errors by symbol). Never raises."""
    quotes: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for sym in symbols:
        try:
            q = get_quote(sym)
            quotes[q["symbol"]] = q
        except Exception as exc:
            errors[sym] = str(exc)
    return quotes, errors


def get_history(symbol: str, range_: str = "3mo") -> dict[str, Any]:
    """Candles for a chart: {symbol, range, interval, candles: [{t, o, h, l, c, v}]}."""
    if range_ not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    period, interval = RANGES[range_]
    ticker = yahoo_ticker(symbol)

    def fetch():
        h = _history(ticker, period, interval)
        candles = []
        for ts, row in h.iterrows():
            c = _num(row["Close"])
            if c is None:
                continue
            candles.append({
                "t": int(ts.timestamp()),
                "o": _num(row["Open"]), "h": _num(row["High"]), "l": _num(row["Low"]),
                "c": c, "v": int(_num(row.get("Volume")) or 0),
            })
        return {"symbol": normalize(symbol), "ticker": ticker, "range": range_,
                "interval": interval, "candles": candles}

    return _cached(("h", ticker, range_), _HISTORY_TTL, fetch)


def paper_quote(symbol: str):
    """A broker ``Quote`` from Yahoo for the paper engine (bid = ask = ltp)."""
    from skopaq.broker.models import Quote

    q = get_quote(symbol)
    ltp = q["ltp"] or 0.0
    return Quote(
        symbol=symbol, exchange="NSE", ltp=ltp, open=q["open"] or 0.0, high=q["high"] or 0.0,
        low=q["low"] or 0.0, close=q["prev_close"] or 0.0, volume=q["volume"],
        change=q["change"] or 0.0, change_pct=(q["change_pct"] or 0.0) * 100,
        bid=ltp, ask=ltp,
    )
