"""Scalper settings from ``SkopaqConfig`` (``scalp_*``), sanitised: a bad value is its
default, so a typo never stops the session."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time, timedelta
from typing import Any

from skopaq.scalping.strategies import STRATEGY_NAMES, StrategyParams

DEFAULT_SYMBOLS = ("RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN,AXISBANK,KOTAKBANK,LT,"
                   "BHARTIARTL")


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return default if not math.isfinite(value) else min(hi, max(lo, value))


def _hhmm(value: Any, default: time) -> time:
    try:
        h, m = str(value).strip().split(":")
        return time(int(h), int(m))
    except (TypeError, ValueError):
        return default


def _list(value: Any, default: str) -> tuple[str, ...]:
    text = value if isinstance(value, str) and value.strip() else default
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


@dataclass(frozen=True)
class ScalpSettings:
    symbols: tuple[str, ...]
    strategies: tuple[str, ...]
    candle_seconds: int
    risk_per_trade_pct: float
    max_position_value: float
    max_trades_per_day: int
    max_open: int
    max_daily_loss: float
    cooldown: timedelta
    entry_start: time
    entry_end: time
    flatten_at: time
    max_hold: timedelta
    min_reward_to_cost: float
    tick_max_age_s: float
    rest_poll_s: float
    params: StrategyParams

    @classmethod
    def from_config(cls, config: Any) -> "ScalpSettings":
        g = lambda name, default=None: getattr(config, name, default)  # noqa: E731
        strategies = tuple(s.lower() for s in _list(g("scalp_strategies"),
                                                     ",".join(STRATEGY_NAMES))
                           if s.lower() in STRATEGY_NAMES) or STRATEGY_NAMES
        entry_start = _hhmm(g("scalp_entry_start"), time(9, 30))
        entry_end = _hhmm(g("scalp_entry_end"), time(14, 45))
        flatten_at = _hhmm(g("scalp_flatten_at"), time(15, 10))
        if not (entry_start < entry_end <= flatten_at <= time(15, 15)):
            entry_start, entry_end, flatten_at = time(9, 30), time(14, 45), time(15, 10)
        return cls(
            symbols=_list(g("scalp_symbols"), DEFAULT_SYMBOLS)[:50],
            strategies=strategies,
            candle_seconds=int(_num(g("scalp_candle_seconds"), 60, 15, 900)),
            risk_per_trade_pct=_num(g("scalp_risk_per_trade_pct"), 0.0025, 0.0001, 0.02),
            max_position_value=_num(g("scalp_max_position_value_inr"), 50_000, 1_000, 1e8),
            max_trades_per_day=int(_num(g("scalp_max_trades_per_day"), 10, 1, 200)),
            max_open=int(_num(g("scalp_max_open"), 2, 1, 20)),
            max_daily_loss=_num(g("scalp_max_daily_loss_inr"), 2_000, 100, 1e8),
            cooldown=timedelta(minutes=_num(g("scalp_cooldown_minutes"), 5, 0, 240)),
            entry_start=entry_start, entry_end=entry_end, flatten_at=flatten_at,
            max_hold=timedelta(minutes=_num(g("scalp_max_hold_minutes"), 30, 1, 375)),
            min_reward_to_cost=_num(g("scalp_min_reward_to_cost"), 2.0, 0.5, 50),
            tick_max_age_s=_num(g("ws_tick_max_age_seconds"), 5.0, 1, 60),
            rest_poll_s=_num(g("scalp_rest_poll_seconds"), 3.0, 1, 60),
            params=StrategyParams(
                rr=_num(g("scalp_rr"), 1.5, 0.5, 10),
                orb_minutes=int(_num(g("scalp_orb_minutes"), 15, 5, 120)),
            ),
        )
