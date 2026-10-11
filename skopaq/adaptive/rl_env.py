"""The trading environment the RL agents (``rl_agents``) learn in and are judged on.

It replays one session of closed candles exactly the way the live scalper would trade it,
so a policy learns the rules it will later live under:

* **Actions** (FreqAI's 3-action layout, long only): ``0`` hold, ``1`` enter long,
  ``2`` exit. An action that does not apply (enter while long, exit while flat) is a
  hold — ``action_mask`` tells the agent which ones apply.
* **Timing:** the agent decides on bar ``t``'s close; an entry or exit fills at bar
  ``t+1``'s open with adverse slippage. Entries only inside the scalper's entry window.
* **Exits the agent cannot override:** every entry gets the live geometry
  (``entry_geometry``: an ATR stop bounded 0.4–2.5 ATR and a ``rr`` target, shifted by
  the fill like the engine does). An entry the live scalper would refuse on cost — its
  ``min_reward_to_cost`` check or qualification's net reward/risk after charges and
  slippage (``strategy.signal.economics``) — is not taken here either (``refused``).
  It is then managed by the *same* code the engine
  uses — ``scalping.rules.update_trail`` / ``exit_reason`` (stop, target, breakeven and
  trail, time stop) — and flattened at ``flatten_at``. The agent can only exit earlier.
* **Reward:** the change of the account's mark-to-market equity after every charge
  (``finance.charges.leg_charges``) and slippage, in percent of the notional. The rewards
  of a session add up to its net P&L exactly, so churning is never rewarded.

``rl_features`` is shared with the live policy (``rl_live``): every value at bar ``t``
uses bars ``≤ t`` of the same session only (tested against appended future bars).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time, timedelta
from typing import Any, Optional, Sequence

import numpy as np

from skopaq.finance.charges import EQUITY_INTRADAY, leg_charges
from skopaq.scalping.costs import round_trip_cost
from skopaq.scalping.rules import ScalpPosition, exit_reason, update_trail
from skopaq.scalping.strategies import StrategyParams, _bounded_stop, _setup, atr_last

HOLD, ENTER, EXIT = 0, 1, 2
ACTIONS = ("hold", "enter", "exit")
FEATURES = ("ret_1", "ret_5", "ret_15", "ema_gap", "rsi", "atr_pct", "volume_ratio",
            "range_pos", "vwap_gap", "time_of_day")
STATE = ("in_position", "unrealized_pct", "bars_held")
OBS_SIZE = len(FEATURES) + len(STATE)


def _ema(x: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(x)
    k = 2.0 / (period + 1)
    for i, v in enumerate(x):
        out[i] = v if i == 0 else out[i - 1] + k * (v - out[i - 1])
    return out


def _wilder(x: np.ndarray, period: int) -> np.ndarray:
    """Wilder smoothing; NaN until ``period`` values are known."""
    out = np.full_like(x, np.nan)
    if len(x) < period:
        return out
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = (out[i - 1] * (period - 1) + x[i]) / period
    return out


def rl_features(candles: Sequence[Any]) -> np.ndarray:
    """``(len(candles), len(FEATURES))`` point-in-time features of one session."""
    n = len(candles)
    if n == 0:
        return np.zeros((0, len(FEATURES)))
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    lo = np.array([c.low for c in candles], float)
    c = np.array([c.close for c in candles], float)
    v = np.array([float(x.volume or 0) for x in candles], float)
    del o
    logc = np.log(np.maximum(c, 1e-9))

    def ret(k: int) -> np.ndarray:
        r = np.full(n, np.nan)
        r[k:] = (logc[k:] - logc[:-k]) * 100
        return r

    gap = (_ema(c, 9) - _ema(c, 21)) / c * 100
    d = np.diff(c, prepend=c[0])
    up, dn = _wilder(np.maximum(d[1:], 0), 14), _wilder(np.maximum(-d[1:], 0), 14)
    rsi = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(dn > 0, 100 - 100 / (1 + up / dn), np.where(up > 0, 100.0, 50.0))
    rsi[1:] = np.where(np.isnan(up), np.nan, rs)
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    atr = np.full(n, np.nan)
    atr[1:] = _wilder(tr[1:], 14)
    vol_ratio = np.full(n, np.nan)
    hi20, lo20 = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        a = max(0, i - 19)
        if i >= 1:
            vol_ratio[i] = math.log((v[i] + 1) / (v[a:i].mean() + 1))
        hi20[i], lo20[i] = h[a:i + 1].max(), lo[a:i + 1].min()
    span = hi20 - lo20
    range_pos = np.where(span > 0, (c - lo20) / np.where(span > 0, span, 1) - 0.5, 0.0)
    typical = (h + lo + c) / 3
    cum_v = np.cumsum(v)
    vwap = np.where(cum_v > 0, np.cumsum(typical * v) / np.where(cum_v > 0, cum_v, 1), c)
    t0 = candles[0].start
    tod = np.array([(x.start - t0).total_seconds() / 60 / 375 for x in candles])
    out = np.column_stack([ret(1), ret(5), ret(15), gap, rsi / 100 - 0.5, atr / c * 100,
                           vol_ratio, range_pos, (c - vwap) / c * 100, tod])
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def state_vector(in_position: bool, unrealized_pct: float, bars_held: int) -> np.ndarray:
    return np.array([float(in_position), float(np.clip(unrealized_pct, -5, 5)),
                     min(bars_held, 120) / 60.0])


def action_mask(in_position: bool) -> np.ndarray:
    return np.array([True, not in_position, in_position])


def entry_geometry(candles: Sequence[Any], params: StrategyParams, stop_atr: float):
    """The live entry's ``Setup`` at the last close (None without an ATR yet)."""
    atr = atr_last(candles)
    if not candles or atr is None or atr <= 0:
        return None
    entry = candles[-1].close
    stop = _bounded_stop(entry, entry - stop_atr * atr, atr)
    return _setup("rl_policy", entry, stop, None, params, "RL policy entry")


@dataclass(frozen=True)
class EnvConfig:
    notional: float = 50_000.0
    slippage_pct: float = 0.0002
    warmup: int = 30
    rr: float = 1.5
    stop_atr: float = 1.5
    max_hold_minutes: float = 30.0
    entry_start: time = time(9, 30)
    entry_end: time = time(14, 45)
    flatten_at: time = time(15, 10)
    min_reward_to_cost: float = 2.0             # the scalper's cost check
    min_net_rr: float = 1.0                     # qualification's net reward/risk

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("entry_start", "entry_end", "flatten_at"):
            d[k] = d[k].strftime("%H:%M")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EnvConfig":
        d = dict(d)
        for k in ("entry_start", "entry_end", "flatten_at"):
            if isinstance(d.get(k), str):
                hh, mm = d[k].split(":")
                d[k] = time(int(hh), int(mm))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class RLTradingEnv:
    """One session; ``reset() -> obs``, ``step(a) -> (obs, reward, done)``."""

    def __init__(self, day: Sequence[Any], cfg: EnvConfig = EnvConfig(),
                 features: Optional[np.ndarray] = None) -> None:
        self.day, self.cfg = list(day), cfg
        self.feats = rl_features(self.day) if features is None else features
        self.params = StrategyParams(rr=cfg.rr)
        self.max_hold = timedelta(minutes=cfg.max_hold_minutes)
        self.reset()

    # ── state ───────────────────────────────────────────────────────────
    def reset(self) -> np.ndarray:
        self.t = min(self.cfg.warmup, max(len(self.day) - 2, 0))
        self.pos: Optional[ScalpPosition] = None
        self.held = 0
        self.realized = self.equity = 0.0
        self.charges = 0.0
        self.trades = 0
        self.trade_pnls: list[float] = []
        self._trade = 0.0
        self.exits: dict[str, int] = {}
        self.refused = 0
        return self.obs()

    @property
    def in_position(self) -> bool:
        return self.pos is not None

    @property
    def done(self) -> bool:
        return self.t >= len(self.day) - 1 or (
            self.pos is None and self.day[self.t].start.time() >= self.cfg.flatten_at)

    def obs(self) -> np.ndarray:
        unreal = 0.0
        if self.pos is not None:
            unreal = (self.day[self.t].close / self.pos.entry - 1) * 100
        return np.concatenate([self.feats[self.t], state_vector(self.in_position, unreal,
                                                                self.held)])

    def mask(self) -> np.ndarray:
        return action_mask(self.in_position)

    def can_enter(self) -> bool:
        tt = self.day[self.t].start.time()
        return self.pos is None and self.cfg.entry_start <= tt <= self.cfg.entry_end

    def _economic(self, s, qty: int) -> bool:
        """Would the live scalper take this setup on cost (before the market checks)?"""
        from skopaq.strategy.signal import QualifyRules, SignalCandidate, economics

        cfg = self.cfg
        if s.reward * qty < cfg.min_reward_to_cost * round_trip_cost(s.entry, s.target, qty):
            return False
        c = SignalCandidate(strategy_id="scalp.rl_policy", strategy_version="", symbol="",
                            product=EQUITY_INTRADAY, direction=1,
                            decided_at=self.day[self.t].start, timeframe_s=60,
                            entry=s.entry, stop=s.stop, target=s.target, qty=qty)
        reward, risk, _, _ = economics(c, qty, QualifyRules(slippage_pct=cfg.slippage_pct))
        return risk > 0 and reward / risk >= cfg.min_net_rr

    # ── legs ────────────────────────────────────────────────────────────
    def _buy(self, fill: float, qty: int, stop: float, target: float, atr: float) -> None:
        c = leg_charges(EQUITY_INTRADAY, "BUY", fill * qty).total
        self.realized -= c
        self.charges += c
        self._trade = -c
        self.trades += 1
        self.held = 0
        self.pos = ScalpPosition(symbol="", strategy="rl_policy", qty=qty, entry=fill,
                                 stop=round(stop, 2), target=round(target, 2),
                                 opened_at=self.day[self.t + 1].start, atr=atr,
                                 cost_per_share=round(round_trip_cost(fill, target, qty)
                                                      / qty, 2))

    def _sell(self, fill: float, why: str) -> None:
        p = self.pos
        assert p is not None
        c = leg_charges(EQUITY_INTRADAY, "SELL", fill * p.qty).total
        gross = (fill - p.entry) * p.qty
        self.realized += gross - c
        self.charges += c
        self._trade += gross - c
        self.trade_pnls.append(self._trade)
        self.exits[why] = self.exits.get(why, 0) + 1
        self.pos = None
        self.held = 0

    # ── step ────────────────────────────────────────────────────────────
    def step(self, action: int) -> tuple[np.ndarray, float, bool]:
        cfg, slip = self.cfg, self.cfg.slippage_pct
        nxt = self.day[self.t + 1]
        if action == ENTER and self.can_enter():
            s = entry_geometry(self.day[:self.t + 1], self.params, cfg.stop_atr)
            if s is not None and not self._economic(s, max(1, int(cfg.notional // s.entry))):
                self.refused += 1
            elif s is not None:
                fill = nxt.open * (1 + slip)
                shift = fill - s.entry
                self._buy(fill, max(1, int(cfg.notional // fill)), s.stop + shift,
                          s.target + shift, atr_last(self.day[:self.t + 1]) or s.risk)
        elif action == EXIT and self.pos is not None:
            self._sell(nxt.open * (1 - slip), "agent")
        p = self.pos
        if p is not None:                        # the bar after the decision, as live
            self.held += 1
            end = nxt.start + timedelta(minutes=1)
            if nxt.low <= p.stop:
                self._sell(min(nxt.open, p.stop) * (1 - slip),
                           "trail" if p.breakeven else "stop")
            elif nxt.high >= p.target:
                self._sell(p.target * (1 - slip), "target")
            else:
                update_trail(p, nxt.high)
                why = exit_reason(p, nxt.close, end, self.max_hold)
                if why and "TIME STOP" in why:
                    self._sell(nxt.close * (1 - slip), "time")
                elif nxt.start.time() >= cfg.flatten_at or self.t + 1 >= len(self.day) - 1:
                    self._sell(nxt.close * (1 - slip), "flatten")
        self.t += 1
        mark = self.realized
        if self.pos is not None:
            mark += (self.day[self.t].close - self.pos.entry) * self.pos.qty
        reward_inr = mark - self.equity
        self.equity = mark
        return self.obs(), reward_inr / cfg.notional * 100, self.done

    @property
    def net(self) -> float:
        return self.realized


def precompute(days: Sequence[Sequence[Any]], min_bars: int = 60) -> list[tuple[list, np.ndarray]]:
    return [(list(d), rl_features(d)) for d in days if len(d) >= min_bars]
