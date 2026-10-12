"""ATR (Average True Range) for position sizing, from INDstocks daily candles.

``fetch_atr`` reads the symbol's daily candles from the INDstocks API
(``skopaq.broker.live_quotes``) and computes Wilder's ATR locally
(``skopaq.market.indicators.atr``). Without a token, or when INDstocks has too
few candles, it returns None and the position sizer falls back to a percentage
estimate (2% of price).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from types import SimpleNamespace
from typing import Optional

logger = logging.getLogger(__name__)

# Default ATR as fraction of price when INDstocks data is unavailable
_FALLBACK_ATR_PCT = 0.02


def fetch_atr(
    symbol: str,
    trade_date: str,
    atr_period: int = 14,
    look_back_days: int = 30,
) -> Optional[float]:
    """Wilder ATR of *symbol*'s daily candles closed before *trade_date*, from INDstocks.

    Args:
        symbol: NSE symbol (e.g., "RELIANCE").
        trade_date: Current trading date (YYYY-MM-DD): its own (forming) bar is left out.
        atr_period: ATR lookback period (default 14).
        look_back_days: Unused (kept for the callers' signature); three months of daily
            candles are read, enough for any period up to about 60.

    Returns:
        Latest ATR value as a float, or None if unavailable.
    """
    try:
        from skopaq.broker import live_quotes
        from skopaq.market.indicators import atr

        data = live_quotes.run_sync(live_quotes.get_history(symbol, "3mo"), timeout=30)
        try:
            cutoff = date.fromisoformat(trade_date)
        except (TypeError, ValueError):
            cutoff = None
        candles = [
            SimpleNamespace(open=float(c["o"]), high=float(c["h"]), low=float(c["l"]),
                            close=float(c["c"]))
            for c in data["candles"]
            if c.get("c") and (cutoff is None or _ist_date(c["t"]) < cutoff)
        ]
        value = atr(candles, atr_period)
        if value is None or value <= 0:
            logger.warning("Not enough INDstocks daily candles for the ATR of %s", symbol)
            return None
        return float(value)
    except Exception:
        logger.warning(
            "ATR from INDstocks failed for %s — will use fallback estimate",
            symbol,
            exc_info=True,
        )
        return None


def _ist_date(ts: int) -> date:
    from datetime import datetime, timedelta, timezone

    return datetime.fromtimestamp(int(ts), timezone(timedelta(hours=5, minutes=30))).date()


def estimate_atr(price: float, pct: float = _FALLBACK_ATR_PCT) -> float:
    """Estimate ATR as a percentage of the current price.

    Used as a fallback when vendor data is unavailable.  The default 2%
    approximation is conservative for Indian large-caps (typical ATR is
    1.5–3% for NIFTY 50 stocks).

    Args:
        price: Current stock price.
        pct: Fraction of price to use (default 0.02 = 2%).

    Returns:
        Estimated ATR value.
    """
    return abs(price * pct)


def _parse_atr_value(raw: str) -> Optional[float]:
    """Extract the most recent ATR numeric value from vendor response.

    The upstream vendors return a text report with lines like:
        "2026-03-01: ATR = 45.23"
    or CSV-style:
        "date,ATR\\n2026-03-01,45.23\\n..."

    We scan for the last numeric value associated with "ATR" or "atr".
    """
    # Strategy 1: Look for "ATR" followed by a number
    # Matches patterns like "ATR = 45.23", "ATR: 45.23", "atr,45.23"
    matches = re.findall(r"(?:ATR|atr)\s*[=:,]\s*([\d.]+)", raw)
    if matches:
        try:
            return float(matches[-1])  # Most recent value (last in the list)
        except ValueError:
            pass

    # Strategy 2: Look for any decimal number in the last few lines
    # (vendor response may have description text followed by data)
    lines = raw.strip().splitlines()
    for line in reversed(lines[-10:]):
        nums = re.findall(r"\b(\d+\.?\d*)\b", line)
        if nums:
            try:
                val = float(nums[-1])
                if 0.1 < val < 10000:  # Reasonable ATR range
                    return val
            except ValueError:
                continue

    logger.warning("Could not parse ATR value from vendor response")
    return None
