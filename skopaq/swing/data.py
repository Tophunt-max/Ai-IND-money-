"""Daily candles for the swing book.

* ``daily_candles(symbol, range_)``: live / recent bars (INDstocks with a token, else
  Yahoo; ``skopaq.broker.live_quotes``), today's forming bar dropped — what the live swing
  engine decides on.
* ``history(symbol, days)``: years of daily bars for training (the data vendors of
  ``tradingagents.dataflows``: INDstocks, then Yahoo).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from skopaq.market.candles import Candle

IST = timezone(timedelta(hours=5, minutes=30))


async def daily_candles(
    symbol: str, range_: str = "1y", *, now: Optional[datetime] = None
) -> list[Candle]:
    from skopaq.broker import live_quotes

    data = await live_quotes.get_history(symbol, range_)
    out = []
    for c in data.get("candles") or []:
        if c.get("c") is None:
            continue
        close = float(c["c"])
        out.append(
            Candle(
                datetime.fromtimestamp(int(c["t"]), IST),
                float(c.get("o") or close),
                float(c.get("h") or close),
                float(c.get("l") or close),
                close,
                int(c.get("v") or 0),
            )
        )
    now = now or datetime.now(IST)
    # A bar is closed once its day is over (the 15:30 close for today's)
    return [
        c
        for c in out
        if c.start.date() < now.date()
        or (c.start.date() == now.date() and now.hour * 60 + now.minute >= 15 * 60 + 30)
    ]


def _setup_vendors() -> None:
    from tradingagents.dataflows.config import set_config

    set_config(
        {
            "data_vendors": {
                "core_stock_apis": "indstocks,yfinance",
                "technical_indicators": "yfinance",
                "fundamental_data": "yfinance",
                "news_data": "yfinance",
            },
            "yfinance_symbol_suffix": ".NS",
        }
    )


def history(symbol: str, days: int = 1825) -> list[Candle]:
    """About ``days`` calendar days of daily candles, oldest first (blocking)."""
    import pandas as pd

    from tradingagents.dataflows.router import route_to_vendor

    _setup_vendors()
    now = datetime.now(timezone.utc)
    text = route_to_vendor(
        "get_stock_data",
        symbol,
        (now - timedelta(days=days)).strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
    )
    lines = [ln for ln in str(text).strip().split("\n") if not ln.startswith("#")]
    df = pd.read_csv(io.StringIO("\n".join(lines)))
    if "Close" not in df or len(df) < 30:
        raise ValueError(f"not enough daily history for {symbol} ({len(df)} bars)")
    out = []
    for r in df.itertuples(index=False):
        d = pd.Timestamp(getattr(r, "Date"))
        close = float(getattr(r, "Close"))
        if not close == close:  # NaN row
            continue
        start = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
        out.append(
            Candle(
                start,
                float(getattr(r, "Open")),
                float(getattr(r, "High")),
                float(getattr(r, "Low")),
                close,
                int(getattr(r, "Volume") or 0),
            )
        )
    return out
