"""Reinforcement learning — an offline research module, never wired to orders.

``TradingEnv`` is a gym-style environment over closed 1-minute candles: actions are
0 = flat, 1 = long a fixed notional; the reward is the step's net P&L *after* the
round-trip charges of every entry/exit (``skopaq.finance.charges``) and adverse slippage,
so an agent cannot learn to churn. Observations are a few point-in-time features
discretised into buckets.

``QAgent`` is a tabular Q-learning baseline (seeded, reproducible). ``research_run``
trains on the first days and evaluates greedily on later days it never saw, reporting
net P&L, trades and charges next to a buy-nothing and a buy-and-hold baseline.

This module deliberately has no path to the executor: ``RESEARCH_ONLY`` is checked by
the tests. The tradable agents (PPO / A2C / DQN) are ``rl_env`` / ``rl_agents`` /
``rl_live``: they replay the scalper's own exit rules and reach paper or live trading only
through the RL registry's gates and the scalper's signal and risk gates.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from skopaq.finance.charges import EQUITY_INTRADAY, leg_charges
from skopaq.market import indicators as ind

RESEARCH_ONLY = True


def _bucket(x: float, edges: Sequence[float]) -> int:
    if x != x:                                   # NaN
        return len(edges) + 1
    for i, e in enumerate(edges):
        if x < e:
            return i
    return len(edges)


class TradingEnv:
    def __init__(self, day: Sequence[Any], *, notional: float = 50_000.0,
                 slippage_pct: float = 0.0002, warmup: int = 30) -> None:
        self.day = list(day)
        self.notional, self.slip, self.warmup = notional, slippage_pct, warmup
        self.reset()

    def _obs(self) -> tuple:
        bars = self.day[:self.t + 1]
        closes = [b.close for b in bars]
        rsi = ind.rsi(closes, 14) or math.nan
        roc = ind.roc(closes, 10) or math.nan
        f9, s21 = ind.ema(closes, 9), ind.ema(closes, 21)
        gap = (f9 - s21) / closes[-1] if f9 is not None and s21 is not None else math.nan
        return (_bucket(rsi, (35, 45, 55, 65)), _bucket(roc, (-0.003, -0.001, 0.001, 0.003)),
                _bucket(gap, (-0.001, 0.0, 0.001)), int(self.qty > 0))

    def reset(self) -> tuple:
        self.t, self.qty = self.warmup, 0
        self.net, self.charges, self.trades = 0.0, 0.0, 0
        return self._obs()

    @property
    def done(self) -> bool:
        return self.t >= len(self.day) - 2

    def step(self, action: int) -> tuple[tuple, float, bool]:
        """Decide on bar ``t``'s close, act at bar ``t+1``'s open; the reward is the
        step's net P&L, so the rewards of a day add up to its net P&L exactly:

        * enter: (close[t+1] - buy fill) × qty - buy charges
        * hold:  (close[t+1] - close[t]) × qty
        * exit:  (sell fill - close[t]) × qty - sell charges
        * the session's last bar flattens what is still held at its close.
        """
        cur, nxt = self.day[self.t], self.day[self.t + 1]
        reward = 0.0
        if action == 1 and self.qty == 0:
            fill = nxt.open * (1 + self.slip)
            self.qty = max(1, int(self.notional // fill))
            c = leg_charges(EQUITY_INTRADAY, "BUY", fill * self.qty).total
            reward = (nxt.close - fill) * self.qty - c
            self.charges += c
            self.trades += 1
        elif action == 1 and self.qty > 0:
            reward = (nxt.close - cur.close) * self.qty
        elif action == 0 and self.qty > 0:
            fill = nxt.open * (1 - self.slip)
            c = leg_charges(EQUITY_INTRADAY, "SELL", fill * self.qty).total
            reward = (fill - cur.close) * self.qty - c
            self.charges += c
            self.qty = 0
        self.t += 1
        if self.done and self.qty > 0:
            last = self.day[self.t]
            fill = last.close * (1 - self.slip)
            c = leg_charges(EQUITY_INTRADAY, "SELL", fill * self.qty).total
            reward += (fill - last.close) * self.qty - c
            self.charges += c
            self.qty = 0
        self.net += reward
        return self._obs(), reward, self.done


@dataclass
class QAgent:
    alpha: float = 0.1
    gamma: float = 0.95
    epsilon: float = 0.1
    seed: int = 7
    q: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def act(self, obs: tuple, greedy: bool = False) -> int:
        if not greedy and self._rng.random() < self.epsilon:
            return self._rng.randint(0, 1)
        v = self.q.get(obs, [0.0, 0.0])
        return int(v[1] > v[0])

    def learn(self, obs: tuple, a: int, r: float, nxt: tuple, done: bool) -> None:
        v = self.q.setdefault(obs, [0.0, 0.0])
        target = r + (0.0 if done else self.gamma * max(self.q.get(nxt, [0.0, 0.0])))
        v[a] += self.alpha * (target - v[a])


def run_day(env: TradingEnv, agent: QAgent, *, learn: bool) -> dict:
    obs = env.reset()
    while not env.done:
        a = agent.act(obs, greedy=not learn)
        nxt, r, done = env.step(a)
        if learn:
            agent.learn(obs, a, r, nxt, done)
        obs = nxt
    return {"net": round(env.net, 2), "trades": env.trades, "charges": round(env.charges, 2)}


def research_run(days: Sequence[Sequence[Any]], *, train_frac: float = 0.7, epochs: int = 5,
                 seed: int = 7, notional: float = 50_000.0) -> dict:
    days = [d for d in days if len(d) > 60]
    n = max(1, int(len(days) * train_frac))
    train, test = days[:n], days[n:]
    agent = QAgent(seed=seed)
    for _ in range(epochs):
        for d in train:
            run_day(TradingEnv(d, notional=notional), agent, learn=True)
    results = [run_day(TradingEnv(d, notional=notional), agent, learn=False) for d in test]
    hold = []
    for d in test:
        qty = max(1, int(notional // d[30].open))
        gross = (d[-1].close - d[30].open) * qty
        hold.append(gross - leg_charges(EQUITY_INTRADAY, "BUY", d[30].open * qty).total
                    - leg_charges(EQUITY_INTRADAY, "SELL", d[-1].close * qty).total)
    return {"research_only": RESEARCH_ONLY, "train_days": len(train), "test_days": len(test),
            "states_learned": len(agent.q),
            "agent": {"net": round(sum(r["net"] for r in results), 2),
                      "trades": sum(r["trades"] for r in results),
                      "charges": round(sum(r["charges"] for r in results), 2)},
            "baseline_flat": 0.0, "baseline_buy_hold": round(sum(hold), 2)}
