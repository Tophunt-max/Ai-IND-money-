"""Daily candles for the swing book.

* ``daily_candles(symbol, range_)``: recent bars from INDstocks
  (``skopaq.broker.live_quotes``), today's forming bar dropped — what the live swing
  engine decides on.
* ``history(symbol, days)``: years of daily bars for training, from INDstocks too (one
  year per request, ``live_quotes.daily_history``).

All candles come from the INDstocks API; without a token both raise ``LookupError``.
"""

from __future__ import annotations

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


def _candle(row: dict) -> Candle:
    close = float(row["c"])
    return Candle(
        datetime.fromtimestamp(int(row["t"]), IST),
        float(row.get("o") or close),
        float(row.get("h") or close),
        float(row.get("l") or close),
        close,
        int(row.get("v") or 0),
    )


def history(symbol: str, days: int = 1825) -> list[Candle]:
    """About ``days`` calendar days of INDstocks daily candles, oldest first (blocking).
    Today's bar is left out until the 15:30 close, as in ``daily_candles``."""
    from skopaq.broker import live_quotes

    rows = live_quotes.run_sync(live_quotes.daily_history(symbol, days), timeout=120)
    now = datetime.now(IST)
    out = [
        _candle(r) for r in rows
        if r.get("c") is not None
    ]
    out = [
        c for c in out
        if c.start.date() < now.date()
        or (c.start.date() == now.date() and now.hour * 60 + now.minute >= 15 * 60 + 30)
    ]
    if len(out) < 30:
        raise ValueError(f"not enough daily history for {symbol} ({len(out)} bars)")
    return out
