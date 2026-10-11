"""Track record — does the system have an edge? (`skopaq report`)

Forward results only, from what Skopaq stores:

- **Signals** — every setup the engines (scalper, F&O, swing) decided on, from the signal
  journal: qualified or rejected (and the top reasons), executed, and the net outcome of
  those closed, per strategy.
- **Executed trades** — closed positions in the ``trades`` table, with the realized P&L
  and the signal's confidence (the model's probability) at entry.
- **Calibration** — whether higher confidence actually won more often.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Below this many closed signals, win rates are mostly noise.
MIN_SAMPLE = 30

_BUCKETS = ((0, 50), (50, 60), (60, 70), (70, 80), (80, 101))


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


@dataclass
class StrategyStats:
    decisions: int = 0
    qualified: int = 0
    executed: int = 0
    closed: int = 0
    wins: int = 0
    net: float = 0.0

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.closed if self.closed else None


@dataclass
class SignalStats:
    """What the engines decided (the signal journal), whether or not it was traded."""

    total: int = 0
    qualified: int = 0
    rejected: int = 0
    expired: int = 0
    executed: int = 0
    closed: int = 0
    wins: int = 0
    net: float = 0.0
    by_strategy: dict[str, StrategyStats] = field(default_factory=dict)
    top_rejections: list[tuple[str, int]] = field(default_factory=list)

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.closed if self.closed else None

    @property
    def enough_data(self) -> bool:
        return self.closed >= MIN_SAMPLE


def summarize_signals(rows: Iterable[dict]) -> SignalStats:
    """*rows*: signal journal rows (``SignalJournal.signals``)."""
    stats = SignalStats()
    reasons: dict[str, int] = {}
    for r in rows:
        stats.total += 1
        status = str(r.get("status") or "")
        sid = str(r.get("strategy_id") or "?")
        by = stats.by_strategy.setdefault(sid, StrategyStats())
        by.decisions += 1
        if status == "QUALIFIED":
            stats.qualified += 1
            by.qualified += 1
        elif status == "REJECTED":
            stats.rejected += 1
            for x in r.get("reasons") or []:
                # "net reward/risk 0.8 < 1.0" and "… 0.5 < 1.0" are one reason
                key = re.sub(r"\d+(\.\d+)?%?", "#", str(x).split("(")[0]).strip()[:48]
                reasons[key] = reasons.get(key, 0) + 1
        elif status == "EXPIRED":
            stats.expired += 1
        ex = r.get("execution") or {}
        if isinstance(ex.get("filled"), (int, float)) and ex["filled"] > 0:
            stats.executed += 1
            by.executed += 1
        out = r.get("outcome") or {}
        net = out.get("net_pnl")
        if isinstance(net, (int, float)):
            stats.closed += 1
            by.closed += 1
            stats.net += float(net)
            by.net += float(net)
            if net > 0:
                stats.wins += 1
                by.wins += 1
    stats.top_rejections = sorted(reasons.items(), key=lambda kv: -kv[1])[:8]
    return stats


@dataclass
class TradeStats:
    """Realized results of executed trades (closed positions)."""

    closed: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    profit_factor: Optional[float] = None  # gross profit / gross loss
    max_drawdown: float = 0.0  # worst peak-to-trough of cumulative P&L (INR)
    avg_return: Optional[float] = None  # per trade, on the capital it used
    return_std: Optional[float] = None

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.closed if self.closed else None


def _entry_value(trade) -> Optional[float]:
    price = trade.fill_price or trade.price
    if price is None or not trade.quantity:
        return None
    return float(price) * float(trade.quantity)


def summarize_trades(trades: Iterable[Any]) -> TradeStats:
    """*trades*: closed opening BUY rows (``TradeRepository.get_closed_since``)."""
    closed = sorted(
        (t for t in trades if t.pnl is not None and t.closed_at is not None),
        key=lambda t: t.closed_at,
    )
    stats = TradeStats(closed=len(closed))
    pnls = [float(t.pnl) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    stats.wins = len(wins)
    stats.total_pnl = sum(pnls)
    stats.avg_win = _mean(wins)
    stats.avg_loss = _mean(losses)
    if losses:
        stats.profit_factor = sum(wins) / -sum(losses)

    peak = equity = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        stats.max_drawdown = max(stats.max_drawdown, peak - equity)

    returns = [
        float(t.pnl) / value for t in closed
        if (value := _entry_value(t))
    ]
    stats.avg_return = _mean(returns)
    if len(returns) > 1:
        mean = stats.avg_return
        stats.return_std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    return stats


@dataclass
class CalibrationBucket:
    low: int
    high: int
    trades: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.trades if self.trades else None

    @property
    def label(self) -> str:
        return f"{self.low}–{min(self.high, 100)}"


def calibration(trades: Iterable[Any]) -> list[CalibrationBucket]:
    """Win rate of closed trades by the signal's confidence at entry."""
    buckets = [CalibrationBucket(low, high) for low, high in _BUCKETS]
    for trade in trades:
        if trade.pnl is None:
            continue
        confidence = (trade.agent_decision or {}).get("confidence")
        if confidence is None:
            continue
        for bucket in buckets:
            if bucket.low <= confidence < bucket.high:
                bucket.trades += 1
                bucket.wins += float(trade.pnl) > 0
                break
    return buckets


@dataclass
class Report:
    days: int
    mode: str
    signals: SignalStats
    trades: TradeStats
    calibration: list[CalibrationBucket]
    sources: list[str] = field(default_factory=list)


def _journal_rows(config, since: datetime, now: datetime) -> tuple[list[dict], list[str]]:
    try:
        from skopaq.strategy.journal import SignalJournal

        journal = SignalJournal.from_config(config)
        days = [(since + timedelta(days=i)).date() for i in range((now - since).days + 1)]
        return journal.signals(days), ["signal journal"]
    except Exception:
        logger.warning("Could not read the signal journal", exc_info=True)
        return [], []


def build_report(config, days: int = 90, now: Optional[datetime] = None) -> Report:
    """Collect the track record for the last *days* days in the current mode."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    is_paper = config.trading_mode != "live"

    client = None
    if config.supabase_url and config.supabase_service_key.get_secret_value():
        from supabase import create_client

        client = create_client(config.supabase_url, config.supabase_service_key.get_secret_value())

    rows, sources = _journal_rows(config, since, now)

    trades: list[Any] = []
    if client is not None:
        try:
            from skopaq.db.repositories import TradeRepository

            trades = TradeRepository(client).get_closed_since(since, is_paper=is_paper)
            sources.append("supabase:trades")
        except Exception:
            logger.warning("Could not read trades from Supabase", exc_info=True)

    return Report(
        days=days,
        mode="paper" if is_paper else "live",
        signals=summarize_signals(rows),
        trades=summarize_trades(trades),
        calibration=calibration(trades),
        sources=sources,
    )
