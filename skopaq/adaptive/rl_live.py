"""Live (paper by default) trading with an RL policy — inside every existing gate.

The policy never places an order. In the scalper (``scalping.engine``) it is one more
entry strategy, ``rl_policy`` (``SKOPAQ_SCALP_STRATEGIES``), and an extra exit signal:

* **entry:** on a closed candle, if the policy's action is *enter*, it proposes a
  ``Setup`` with the live geometry it was trained with (``rl_env.entry_geometry``). That
  setup then goes through exactly what every scalp goes through: the regime map, the cost
  check, ``SignalGate.decide`` / ``qualify`` (net R:R after charges, spread, liquidity,
  data quality, the model gate), position sizing, ``Executor`` → ``EntryGate`` →
  ``SafetyChecker`` (kill switch, limits, funds) → the paper or live router.
* **exit:** for a position it opened, an *exit* action sells it through the engine's
  normal ``_exit`` (a SELL can only reduce risk). It can never move a stop or target:
  stop, trail, target, time stop and the square-off keep running as for any scalp.

Which policy runs (``RLRuntime.from_config``):

* ``SKOPAQ_RL_ENABLED`` must be true;
* **live** account: only the RL registry's ``champion`` — a policy that passed the gates,
  spent the shadow days and was promoted by a named admin — and only with
  ``SKOPAQ_RL_ALLOW_LIVE=true`` (``.env`` only: locked against runtime overrides);
* **paper** account: the champion, else the ``shadow`` policy
  (``SKOPAQ_RL_PAPER_USE_SHADOW``), so a policy collects its paper evidence before anyone
  is asked to approve it;
* a bundle that fails its SHA-256 check, a missing PyTorch or a timeframe mismatch:
  no policy, the scalper runs its rule strategies only.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from skopaq.adaptive import artifacts
from skopaq.adaptive.rl_env import (
    ACTIONS,
    ENTER,
    EXIT,
    FEATURES,
    EnvConfig,
    action_mask,
    entry_geometry,
    rl_features,
    state_vector,
)
from skopaq.scalping.strategies import Setup, StrategyParams

logger = logging.getLogger(__name__)

STRATEGY = "rl_policy"


class RLPolicy:
    """A saved policy (``rl_agents.save_policy``), verified before it is loaded."""

    def __init__(self, path: Path | str, version: str = "") -> None:
        from safetensors.torch import load_file

        from skopaq.adaptive.rl_agents import Normalizer, build_net

        self.path = Path(path)
        artifacts.verify(self.path)                  # IntegrityError: not loaded
        self.meta = json.loads((self.path / "policy.json").read_text())
        if tuple(self.meta.get("features", ())) != FEATURES:
            raise ValueError(f"{self.path.name}: trained on other features")
        self.algo = self.meta["algo"]
        self.version = version or self.path.name
        self.net = build_net(self.algo, int(self.meta["hidden"]))
        self.net.load_state_dict(load_file(str(self.path / "policy.safetensors")))
        self.net.eval()
        n = self.meta["normalizer"]
        self.norm = Normalizer(np.array(n["mean"]), np.array(n["std"]))
        self.env = EnvConfig.from_dict((self.meta.get("config") or {}).get("env", {}))
        self.timeframe_s = int(self.meta.get("timeframe_s", 60))

    def decide(self, candles: Sequence[Any], *, entry: Optional[float] = None,
               bars_held: int = 0) -> dict:
        """The policy's action on the last closed candle (``entry``: a held position)."""
        from skopaq.adaptive.rl_agents import greedy_action

        out: dict[str, Any] = {"version": self.version, "algo": self.algo}
        if len(candles) < self.env.warmup:
            return {**out, "action": "hold", "do_predict": -1,
                    "why": f"warm-up: {len(candles)}/{self.env.warmup} bars"}
        if len(candles) >= 2:
            step = (candles[-1].start - candles[-2].start).total_seconds()
            if step != self.timeframe_s:
                return {**out, "action": "hold", "do_predict": -1,
                        "why": f"candles are {step:.0f}s, the policy was trained on "
                               f"{self.timeframe_s}s"}
        held = entry is not None
        unreal = (candles[-1].close / entry - 1) * 100 if held else 0.0
        obs = np.concatenate([rl_features(candles)[-1], state_vector(held, unreal, bars_held)])
        a, scores = greedy_action(self.net, self.norm(obs), action_mask(held))
        clean = [None if (isinstance(s, float) and math.isnan(s)) else round(float(s), 4)
                 for s in scores]
        return {**out, "action": ACTIONS[a], "do_predict": 1,
                "scores": dict(zip(ACTIONS, clean)),
                "score_kind": "q_value" if self.algo == "dqn" else "probability"}


class RLRuntime:
    """The policy the scalper consults, and its last decision per symbol."""

    def __init__(self, policy: RLPolicy, *, state: str, params: StrategyParams) -> None:
        self.policy, self.state = policy, state
        # The live geometry is the one the policy was trained with (its rr / stop ATR)
        self.params = StrategyParams(rr=policy.env.rr, orb_minutes=params.orb_minutes,
                                     range_lookback=params.range_lookback,
                                     vwap_band_pct=params.vwap_band_pct)
        self.last: dict[str, dict] = {}

    @property
    def version(self) -> str:
        return self.policy.version

    @classmethod
    def from_config(cls, config: Any, *, paper: bool,
                    params: StrategyParams = StrategyParams()) -> Optional["RLRuntime"]:
        if getattr(config, "rl_enabled", False) is not True:
            return None
        from skopaq.adaptive.rl_agents import rl_registry, torch_available
        from skopaq.learning.registry import model_dir

        if not torch_available():
            logger.warning("RL: SKOPAQ_RL_ENABLED but PyTorch is not installed — no policy")
            return None
        if not paper and getattr(config, "rl_allow_live", False) is not True:
            logger.warning("RL: live account and SKOPAQ_RL_ALLOW_LIVE is off — no policy")
            return None
        reg = rl_registry(model_dir(config))
        entry, state = reg.champion(), "champion"
        if entry is None and paper and getattr(config, "rl_paper_use_shadow", True):
            entry, state = reg.shadow(), "shadow"
        if entry is None:
            logger.info("RL: no %s policy in %s", "champion" if not paper else
                        "champion or shadow", reg.dir)
            return None
        try:
            policy = RLPolicy(entry["model"]["path"], version=entry["version"])
        except Exception as exc:
            logger.error("RL: policy %s not loaded: %s", entry.get("version"), exc)
            return None
        logger.info("RL: %s policy %s (%s) active, %s account", state, policy.version,
                    policy.algo.upper(), "paper" if paper else "LIVE")
        return cls(policy, state=state, params=params)

    def entry_setup(self, symbol: str, candles: Sequence[Any]) -> Optional[Setup]:
        try:
            d = self.policy.decide(candles)
        except Exception:
            logger.exception("RL: %s decision failed", symbol)
            return None
        self.last[symbol] = d
        if d["action"] != ACTIONS[ENTER]:
            return None
        s = entry_geometry(candles, self.params, self.policy.env.stop_atr)
        if s is None:
            return None
        sc = d.get("scores", {}).get("enter")
        what = f"P(enter) {sc:.2f}" if d["score_kind"] == "probability" and sc is not None \
            else f"Q(enter) {sc}"
        return Setup(STRATEGY, s.entry, s.stop, s.target,
                     f"{self.policy.algo.upper()} {self.state} {self.version}: {what}")

    def wants_exit(self, symbol: str, candles: Sequence[Any], *, entry: float,
                   bars_held: int) -> bool:
        try:
            d = self.policy.decide(candles, entry=entry, bars_held=bars_held)
        except Exception:
            logger.exception("RL: %s exit decision failed", symbol)
            return False
        self.last[symbol] = d
        return d["action"] == ACTIONS[EXIT]

    def evidence(self, symbol: str) -> dict:
        d = self.last.get(symbol) or {}
        return {"rl": {"version": self.version, "state": self.state, "algo": self.policy.algo,
                       "action": d.get("action"), "scores": d.get("scores")}}
