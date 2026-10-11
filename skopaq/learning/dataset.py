"""Point-in-time learning dataset: features as the decision saw them, labels from the
confirmed net outcome.

Two sources build the same ``Example`` rows:

* ``from_journal`` — the live/paper signal journal (``skopaq.strategy.journal``): a
  decision line's evidence is the feature source; the label comes from its ``outcome``
  line (net P&L after charges). A signal with no execution or no outcome is
  *unlabelled* (counted, never silently a win or a loss).
* ``from_backtest`` — ``skopaq.scalping.backtest.simulate`` over historical candles with
  ``on_decision`` building the evidence from the candles known at that close only.

``check_point_in_time`` enforces the no-leakage contract: no outcome-named feature, and
the evidence's last bar never after the decision time. ``chronological_split`` and
``walk_forward_folds`` never shuffle, and leave an embargo gap between sets so a trade
open at a split boundary cannot leak its outcome across it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

FEATURE_NAMES: tuple[str, ...] = (
    "rsi", "atr_pct", "adx", "plus_minus_di", "macd_hist_pct", "bb_width_pct",
    "bb_percent_b", "roc", "rel_volume", "ema_gap_pct", "dist_support_pct",
    "dist_resistance_pct", "body_pct", "upper_wick_pct", "lower_wick_pct",
    "pattern_bias", "regime_trend_up", "regime_trend_down", "regime_ranging",
    "vol_ratio", "htf_up", "minute_of_session", "stop_pct", "reward_risk",
)
# Names that would carry the outcome into the features (leakage)
_FORBIDDEN = {"net_pnl", "gross_pnl", "exit_price", "won", "label", "pnl", "exit_reason",
              "mae", "mfe", "closed_at"}


def _f(value: Any) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return math.nan
    return x if math.isfinite(x) else math.nan


def features_from_evidence(evidence: dict, *, entry: float, stop: float, target: float,
                           decided_at: datetime) -> dict[str, float]:
    """The model's feature vector of one decision (NaN where evidence is missing)."""
    ind = evidence.get("indicators") or {}
    pats = evidence.get("patterns") or {}
    reg = evidence.get("regime") or {}
    htf = evidence.get("htf") or {}
    close = _f(ind.get("close")) or entry
    ema_f, ema_s = _f(ind.get("ema_fast")), _f(ind.get("ema_slow"))
    sup, res = _f(ind.get("support")), _f(ind.get("resistance"))
    bias = pats.get("bias")
    ist = decided_at.astimezone(decided_at.tzinfo)
    minutes = (ist.hour * 60 + ist.minute) - (9 * 60 + 15)
    risk = abs(entry - stop)
    return {
        "rsi": _f(ind.get("rsi")),
        "atr_pct": _f(ind.get("atr_pct")),
        "adx": _f(ind.get("adx")),
        "plus_minus_di": _f(ind.get("plus_di")) - _f(ind.get("minus_di")),
        "macd_hist_pct": _f(ind.get("macd_hist")) / close if close else math.nan,
        "bb_width_pct": _f(ind.get("bb_width_pct")),
        "bb_percent_b": _f(ind.get("bb_percent_b")),
        "roc": _f(ind.get("roc")),
        "rel_volume": _f(ind.get("rel_volume")),
        "ema_gap_pct": (ema_f - ema_s) / close if close else math.nan,
        "dist_support_pct": (close - sup) / close if close else math.nan,
        "dist_resistance_pct": (res - close) / close if close else math.nan,
        "body_pct": _f(pats.get("body_pct")),
        "upper_wick_pct": _f(pats.get("upper_wick_pct")),
        "lower_wick_pct": _f(pats.get("lower_wick_pct")),
        "pattern_bias": {"bullish": 1.0, "bearish": -1.0}.get(bias, 0.0),
        "regime_trend_up": 1.0 if reg.get("trend") == "trending_up" else 0.0,
        "regime_trend_down": 1.0 if reg.get("trend") == "trending_down" else 0.0,
        "regime_ranging": 1.0 if reg.get("trend") == "ranging" else 0.0,
        "vol_ratio": _f(reg.get("vol_ratio")),
        "htf_up": {"up": 1.0, "down": -1.0}.get(htf.get("direction"), 0.0),
        "minute_of_session": float(minutes),
        "stop_pct": risk / entry if entry else math.nan,
        "reward_risk": abs(target - entry) / risk if risk else math.nan,
    }


@dataclass
class Example:
    decided_at: datetime
    features: dict[str, float]
    label: Optional[int]               # 1 net win, 0 net loss/flat, None unlabelled
    net_pnl: Optional[float]
    strategy_id: str = ""
    symbol: str = ""
    signal_id: str = ""
    closed_at: Optional[datetime] = None
    evidence_last_bar: Optional[datetime] = None
    meta: dict = field(default_factory=dict)


@dataclass
class DatasetReport:
    examples: list[Example]
    unlabelled: int = 0
    rejected_signals: int = 0
    leakage: list[str] = field(default_factory=list)

    @property
    def labelled(self) -> list[Example]:
        return [e for e in self.examples if e.label is not None]


def _dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def from_journal(signals: Iterable[dict]) -> DatasetReport:
    """Examples from merged signal-journal rows (``SignalJournal.signals``)."""
    rep = DatasetReport(examples=[])
    for row in signals:
        if row.get("status") != "QUALIFIED":
            rep.rejected_signals += 1
            continue
        decided = _dt(row.get("decided_at"))
        if decided is None:
            continue
        ev = row.get("evidence") or {}
        feats = features_from_evidence(ev, entry=float(row.get("entry") or 0),
                                       stop=float(row.get("stop") or 0),
                                       target=float(row.get("target") or 0),
                                       decided_at=decided)
        execution, outcome = row.get("execution") or {}, row.get("outcome") or {}
        label = net = None
        closed = None
        if execution.get("status") == "FILLED" and outcome.get("net_pnl") is not None:
            net = float(outcome["net_pnl"])
            label = 1 if net > 0 else 0
            closed = _dt(outcome.get("closed_at"))
        else:
            rep.unlabelled += 1
        rep.examples.append(Example(
            decided_at=decided, features=feats, label=label, net_pnl=net,
            strategy_id=str(row.get("strategy_id") or ""), symbol=str(row.get("symbol") or ""),
            signal_id=str(row.get("signal_id") or ""), closed_at=closed,
            evidence_last_bar=_dt(ev.get("last_bar")),
            meta={"regime": (ev.get("regime") or {}).get("trend") or "unknown"}))
    rep.leakage = check_point_in_time(rep.examples)
    return rep


def from_backtest(symbol: str, days: Sequence[Sequence[Any]], settings: Any, *,
                  slippage_pct: float = 0.0002, equity: float = 1_000_000.0) -> DatasetReport:
    """Examples from a backtest: each taken setup's evidence at its decision close, its
    label the trade's net P&L (charges and slippage included)."""
    from skopaq.scalping.backtest import simulate
    from skopaq.strategy.evidence import build_evidence

    def on_decision(candles, setup, decided_at):
        ev = build_evidence(candles, settings.candle_seconds, decided_at,
                            htf_s=getattr(settings, "htf_seconds", 300), source="historical",
                            stale_after_s=10 ** 9)
        return {"evidence": ev, "setup": setup}

    result = simulate(symbol, days, settings, equity=equity, slippage_pct=slippage_pct,
                      on_decision=on_decision)
    rep = DatasetReport(examples=[])
    for t in result.trades:
        tag = t.tag or {}
        setup, ev = tag.get("setup"), tag.get("evidence") or {}
        decided = _dt(t.decided_at)
        if setup is None or decided is None:
            continue
        rep.examples.append(Example(
            decided_at=decided,
            features=features_from_evidence(ev, entry=setup.entry, stop=setup.stop,
                                            target=setup.target, decided_at=decided),
            label=1 if t.pnl > 0 else 0, net_pnl=t.pnl, strategy_id=f"scalp.{t.strategy}",
            symbol=symbol, evidence_last_bar=_dt(ev.get("last_bar")),
            meta={"regime": (ev.get("regime") or {}).get("trend") or "unknown"},
            closed_at=decided.replace(hour=int(t.closed[:2]), minute=int(t.closed[3:5]))))
    rep.leakage = check_point_in_time(rep.examples)
    return rep


def check_point_in_time(examples: Iterable[Example]) -> list[str]:
    """Leakage problems (empty: none): outcome-named features, evidence after the
    decision, an outcome closed before its decision."""
    problems = []
    for e in examples:
        bad = _FORBIDDEN & set(e.features)
        if bad:
            problems.append(f"{e.signal_id or e.decided_at}: outcome features {sorted(bad)}")
        if e.evidence_last_bar is not None and e.evidence_last_bar > e.decided_at:
            problems.append(f"{e.signal_id or e.decided_at}: evidence bar "
                            f"{e.evidence_last_bar} after the decision")
        if e.closed_at is not None and e.closed_at < e.decided_at:
            problems.append(f"{e.signal_id or e.decided_at}: closed before it was decided")
    return problems


def chronological_split(examples: Sequence[Example], train: float = 0.6, val: float = 0.2,
                        embargo: timedelta = timedelta(hours=1)
                        ) -> tuple[list[Example], list[Example], list[Example]]:
    """Train / validation / test in time order, never shuffled. Examples decided within
    ``embargo`` after a set's last decision are dropped, so holding windows cannot overlap
    across the boundary."""
    rows = sorted((e for e in examples if e.label is not None), key=lambda e: e.decided_at)
    n = len(rows)
    a, b = int(n * train), int(n * (train + val))
    tr, va, te = rows[:a], rows[a:b], rows[b:]

    def after(prev: list[Example], nxt: list[Example]) -> list[Example]:
        if not prev:
            return nxt
        edge = max(e.closed_at or e.decided_at for e in prev) + embargo
        return [e for e in nxt if e.decided_at > edge]

    va = after(tr, va)
    te = after(va or tr, te)
    return tr, va, te


def walk_forward_folds(examples: Sequence[Example], folds: int = 4, min_train: int = 50,
                       embargo: timedelta = timedelta(hours=1)
                       ) -> list[tuple[list[Example], list[Example]]]:
    """Expanding-window folds: train on everything before a block, test on the block."""
    rows = sorted((e for e in examples if e.label is not None), key=lambda e: e.decided_at)
    n = len(rows)
    if folds < 1 or n < min_train + folds:
        return []
    block = (n - min_train) // folds
    out = []
    for k in range(folds):
        start = min_train + k * block
        end = n if k == folds - 1 else start + block
        train = rows[:start]
        edge = max(e.closed_at or e.decided_at for e in train) + embargo
        test = [e for e in rows[start:end] if e.decided_at > edge]
        if test:
            out.append((train, test))
    return out
