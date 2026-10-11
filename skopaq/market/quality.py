"""Market-data quality checks for a candle series before a decision is made on it.

``check_series`` reports invalid OHLC bars, duplicate or out-of-order timestamps, missing
intervals inside the session, an incomplete (still forming) last bar, and stale data (the
last closed bar is older than the feed should allow). A live entry must not be taken on a
series with ``blocking`` problems; ``skopaq.strategy.signal.qualify`` rejects it.

Data source labels (``DataSource``) say where prices came from, so the dashboard and the
signal log never present simulated or delayed data as live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Optional, Sequence

from skopaq.market.patterns import validate_ohlc

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class DataSource(str, Enum):
    LIVE = "live"              # broker WebSocket ticks
    DELAYED = "delayed"        # REST polling / a fallback vendor
    HISTORICAL = "historical"  # past candles (backtest, seeding)
    SIMULATED = "simulated"    # paper / synthetic


@dataclass
class QualityReport:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    last_bar_age_s: Optional[float] = None
    missing_bars: int = 0

    def add(self, text: str, *, block: bool) -> None:
        self.problems.append(text)
        if block:
            self.blocking.append(text)
            self.ok = False

    def as_dict(self) -> dict:
        return {"ok": self.ok, "problems": self.problems, "blocking": self.blocking,
                "last_bar_age_s": None if self.last_bar_age_s is None
                else round(self.last_bar_age_s, 1),
                "missing_bars": self.missing_bars}


def _in_session(ts: datetime) -> bool:
    t = ts.astimezone(IST).time()
    return SESSION_OPEN <= t < SESSION_CLOSE


def check_series(candles: Sequence[Any], interval_s: int, now: Optional[datetime] = None, *,
                 min_bars: int = 1, max_missing: int = 3, stale_after_s: Optional[float] = None,
                 check_last: int = 60) -> QualityReport:
    """Quality of the last ``check_last`` closed candles of one interval as of ``now``.

    Blocking: no / too few bars, an invalid bar, a duplicate or out-of-order start, a last
    bar that has not closed yet at ``now``, a last bar older than ``stale_after_s``
    (default: 2 intervals + 30 s), more than ``max_missing`` missing in-session intervals.
    """
    rep = QualityReport()
    now = now or datetime.now(IST)
    if len(candles) < max(1, min_bars):
        rep.add(f"only {len(candles)} bar(s), need {min_bars}", block=True)
        if not candles:
            return rep
    recent = list(candles)[-check_last:]
    for c in recent:
        problem = validate_ohlc(c)
        if problem:
            rep.add(f"invalid bar at {getattr(c, 'start', '?')}: {problem}", block=True)
            break
    step = timedelta(seconds=interval_s)
    for prev, cur in zip(recent, recent[1:]):
        if cur.start == prev.start:
            rep.add(f"duplicate bar at {cur.start}", block=True)
            break
        if cur.start < prev.start:
            rep.add(f"out-of-order bar at {cur.start}", block=True)
            break
        gap = cur.start - prev.start
        if gap > step and prev.start.astimezone(IST).date() == cur.start.astimezone(IST).date():
            missing = int(gap / step) - 1
            if missing > 0 and _in_session(prev.start):
                rep.missing_bars += missing
    if rep.missing_bars > max_missing:
        rep.add(f"{rep.missing_bars} missing bar(s) in the session", block=True)
    elif rep.missing_bars:
        rep.add(f"{rep.missing_bars} missing bar(s) in the session", block=False)
    last = recent[-1]
    closes_at = last.start + step
    if closes_at > now:
        rep.add(f"last bar {last.start} has not closed (closes {closes_at})", block=True)
    age = (now - closes_at).total_seconds()
    rep.last_bar_age_s = max(0.0, age)
    limit = stale_after_s if stale_after_s is not None else 2 * interval_s + 30
    if age > limit:
        rep.add(f"stale: last bar closed {age:.0f}s ago (limit {limit:.0f}s)", block=True)
    return rep


def quote_is_fresh(quote_ts: Optional[datetime], now: Optional[datetime] = None,
                   max_age_s: float = 5.0) -> bool:
    """A quote timestamp within ``max_age_s`` of ``now`` (no timestamp: not fresh)."""
    if quote_ts is None:
        return False
    now = now or datetime.now(timezone.utc)
    if quote_ts.tzinfo is None:
        quote_ts = quote_ts.replace(tzinfo=timezone.utc)
    return 0 <= (now - quote_ts).total_seconds() <= max_age_s
