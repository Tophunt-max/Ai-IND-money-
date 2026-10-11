"""RL agents (skopaq/adaptive/rl_env.py, rl_agents.py, rl_live.py): the environment
follows the live scalper's rules, PPO / A2C / DQN learn, policies are saved verifiably,
and a policy only trades through the scalper's gates — shadow on paper, an approved
champion (plus the .env switch) live."""

from __future__ import annotations

import random
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import numpy as np
import pytest

from skopaq.adaptive import rl_env
from skopaq.adaptive.rl_env import ENTER, EXIT, HOLD, EnvConfig, RLTradingEnv, rl_features
from skopaq.broker.models import ExecutionResult
from skopaq.market.candles import Candle, LiveSeries
from skopaq.scalping.strategies import Setup

IST = timezone(timedelta(hours=5, minutes=30))


def trend_days(seed: int, n: int = 16, drift: float = 0.0015, noise: float = 0.005):
    """Volatile enough that a 1.5-ATR scalp clears the charges (as on a real stock it
    often does not: then the environment refuses the entry like the live scalper)."""
    rng = random.Random(seed)
    out = []
    for d in range(n):
        t0 = datetime(2026, 6, 1, 9, 15, tzinfo=IST) + timedelta(days=d)
        p, day = 1000.0, []
        for i in range(375):
            c = p * (1 + drift + rng.gauss(0, noise))
            day.append(Candle(t0 + timedelta(minutes=i), p, max(p, c) * (1 + noise / 4),
                              min(p, c) * (1 - noise / 4), c, 1000 + rng.randint(0, 300)))
            p = c
        out.append(day)
    return out


# ── environment (no PyTorch needed) ──────────────────────────────────────────


def test_features_are_point_in_time():
    day = trend_days(1, 1)[0]
    full, prefix = rl_features(day), rl_features(day[:150])
    assert full.shape == (375, len(rl_env.FEATURES))
    assert np.allclose(full[:150], prefix)
    assert np.isfinite(full).all()


def test_rewards_add_up_to_net_pnl_and_exits_follow_the_live_rules():
    rng = random.Random(3)
    env = RLTradingEnv(trend_days(2, 1, drift=0.0)[0])
    env.reset()
    total = 0.0
    while not env.done:
        _, r, _ = env.step(rng.choice([HOLD, ENTER, EXIT]))
        total += r
    assert env.trades > 0 and env.charges > 0 and not env.in_position
    assert total * env.cfg.notional / 100 == pytest.approx(env.net, abs=1e-6)
    assert sum(env.trade_pnls) == pytest.approx(env.net, abs=1e-6)
    assert set(env.exits) <= {"agent", "stop", "trail", "target", "time", "flatten"}


def test_actions_that_do_not_apply_and_entries_outside_the_window_are_holds():
    env = RLTradingEnv(trend_days(3, 1)[0], EnvConfig(entry_start=datetime(2026, 1, 1, 10, 0)
                                                      .time()))
    env.reset()                                   # 09:45: before the entry window
    env.step(ENTER)
    assert not env.in_position and env.trades == 0
    env.step(EXIT)                                # flat: exit is a hold
    assert env.net == 0.0
    assert list(env.mask()) == [True, True, False]
    while env.day[env.t].start.time() < env.cfg.entry_start:
        env.step(HOLD)
    env.step(ENTER)
    assert env.in_position and list(env.mask()) == [True, False, True]
    p = env.pos
    assert p.stop < p.entry < p.target            # the live geometry, from the fill


def test_an_entry_the_live_scalper_would_refuse_on_cost_is_not_taken():
    quiet = trend_days(6, 1, drift=0.0, noise=0.0003)[0]      # tiny ATR: charges dominate
    env = RLTradingEnv(quiet)
    env.reset()
    while not env.done:
        env.step(ENTER)
    assert env.trades == 0 and env.refused > 0 and env.net == 0.0


def test_a_held_position_is_squared_off_by_the_flatten_time():
    env = RLTradingEnv(trend_days(4, 1, drift=0.0)[0], EnvConfig(rr=50, max_hold_minutes=999))
    env.reset()
    while not env.done:
        env.step(ENTER if not env.in_position else HOLD)
    assert not env.in_position
    assert env.day[env.t].start.time() >= env.cfg.flatten_at or env.t == len(env.day) - 1


# ── agents (PyTorch) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("algo", ["ppo", "a2c", "dqn"])
def test_each_algorithm_learns_an_obvious_edge_out_of_sample(algo):
    pytest.importorskip("torch")
    pytest.importorskip("safetensors")
    from skopaq.adaptive.rl_agents import RLConfig, train_and_register

    out = train_and_register({"UP": trend_days(1)}, RLConfig(algo=algo, timesteps=12_000))
    rep = out["report"]
    assert rep["test_sessions"] == 5 and rep["train_sessions"] == 11
    assert rep["model"]["trades"] > 30 and rep["model"]["net"] > 0
    assert rep["model"]["charges"] > 0 and rep["samples"] > 100


def _registered(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("safetensors")
    from skopaq.adaptive.rl_agents import RLConfig, rl_registry, train_and_register

    monkeypatch.setenv("SKOPAQ_MODEL_DIR", str(tmp_path))
    reg = rl_registry(tmp_path)
    out = train_and_register({"UP": trend_days(1)}, RLConfig(algo="ppo", timesteps=4_000),
                             registry=reg)
    if out["state"] != "shadow":          # this test is about what happens after the gates
        entry = reg.get(out["version"])
        entry.update(state="shadow", shadow_since=datetime.now(timezone.utc).isoformat())
        reg._write_json(reg._path(out["version"]), entry)
        reg._set_pointer("shadow", out["version"])
    return reg, out


def test_gates_shadow_on_paper_and_live_only_for_an_approved_champion(tmp_path, monkeypatch):
    from skopaq.adaptive.rl_agents import RL_GATES
    from skopaq.adaptive.rl_live import RLRuntime
    from skopaq.learning.registry import ModelRegistry

    reg, out = _registered(tmp_path, monkeypatch)
    v = out["version"]
    cfg = types.SimpleNamespace(rl_enabled=True, rl_allow_live=False, rl_paper_use_shadow=True,
                                model_dir=str(tmp_path))
    assert RLRuntime.from_config(types.SimpleNamespace(rl_enabled=False), paper=True) is None
    paper = RLRuntime.from_config(cfg, paper=True)
    assert paper is not None and paper.state == "shadow" and paper.version == v
    assert RLRuntime.from_config(cfg, paper=False) is None          # no .env switch
    cfg.rl_allow_live = True
    assert RLRuntime.from_config(cfg, paper=False) is None          # no champion yet
    with pytest.raises(ValueError, match="days of paper"):
        reg.promote(v, approved_by="admin", reason="early", gates=RL_GATES)
    later = ModelRegistry(reg.dir, clock=lambda: datetime.now(timezone.utc)
                          + timedelta(days=RL_GATES.shadow_days + 1))
    with pytest.raises(ValueError, match="named human"):
        later.promote(v, approved_by=" ", reason="nobody", gates=RL_GATES)
    later.promote(v, approved_by="admin@x", reason="7 paper days reviewed", gates=RL_GATES)
    live = RLRuntime.from_config(cfg, paper=False)
    assert live is not None and live.state == "champion"
    later.rollback(by="admin@x", reason="test")
    assert RLRuntime.from_config(cfg, paper=False) is None


def test_a_tampered_policy_is_not_loaded(tmp_path, monkeypatch):
    from skopaq.adaptive.artifacts import IntegrityError
    from skopaq.adaptive.rl_live import RLPolicy, RLRuntime

    _, out = _registered(tmp_path, monkeypatch)
    policy = RLPolicy(out["path"])
    d = policy.decide(trend_days(9, 1)[0][:120])
    assert d["action"] in ("hold", "enter") and d["do_predict"] == 1
    assert set(d["scores"]) == {"hold", "enter", "exit"} and d["scores"]["exit"] == 0.0
    assert policy.decide(trend_days(9, 1)[0][:5])["do_predict"] == -1     # warm-up
    with open(f"{out['path']}/policy.safetensors", "ab") as fh:
        fh.write(b"0")
    with pytest.raises(IntegrityError):
        RLPolicy(out["path"])
    cfg = types.SimpleNamespace(rl_enabled=True, rl_paper_use_shadow=True,
                                model_dir=str(tmp_path))
    assert RLRuntime.from_config(cfg, paper=True) is None


# ── the scalper: one more strategy behind the same gates ─────────────────────


class FakeRL:
    version = "rl-test"

    def __init__(self, enter=True, exit=False):
        self._enter, self._exit = enter, exit
        self.asked = []

    def entry_setup(self, symbol, candles):
        self.asked.append(("entry", symbol))
        if not self._enter:
            return None
        c = candles[-1].close
        return Setup("rl_policy", c, round(c - 5.0, 2), round(c + 15.0, 2), "PPO shadow")

    def wants_exit(self, symbol, candles, *, entry, bars_held):
        self.asked.append(("exit", symbol, bars_held))
        return self._exit

    def evidence(self, symbol):
        return {"rl": {"version": self.version, "action": "enter"}}


def _engine(executor, strategies="rl_policy"):
    from skopaq.scalping.engine import ScalpEngine

    router = types.SimpleNamespace(mode="paper", get_funds=AsyncMock(return_value=types
                                   .SimpleNamespace(total_collateral=1_000_000,
                                                    available_cash=1_000_000)))
    cfg = types.SimpleNamespace(scalp_strategies=strategies, scalp_qualification_enabled=False)
    engine = ScalpEngine(cfg, executor, AsyncMock(), router, halted=lambda: False)
    engine.codes["TCS"] = "NSE_11536"
    engine.series["TCS"] = LiveSeries()
    day = trend_days(5, 1)[0][:60]
    engine.series["TCS"].seed(day)
    engine.prices["TCS"] = day[-1].close
    return engine, day


def test_settings_accept_the_rl_strategy_only_when_named():
    from skopaq.scalping.settings import ScalpSettings

    s = ScalpSettings.from_config(types.SimpleNamespace(scalp_strategies="orb,rl_policy"))
    assert s.strategies == ("orb", "rl_policy")
    assert "rl_policy" not in ScalpSettings.from_config(types.SimpleNamespace()).strategies


async def test_an_rl_entry_goes_through_the_executor_like_any_scalp():
    executor = types.SimpleNamespace(execute_signal=AsyncMock(return_value=ExecutionResult(
        success=True, mode="paper", fill_price=1000.0)))
    engine, day = _engine(executor)
    engine._rl = FakeRL()
    engine._pending = {"TCS"}
    await engine._entries(day[-1].start.replace(hour=10, minute=30) + timedelta(minutes=1))
    signal = executor.execute_signal.await_args.args[0]
    assert signal.action == "BUY" and "SCALP rl_policy" in signal.reasoning
    assert signal.stop_loss is not None
    pos = engine.positions["TCS"]
    assert pos.strategy == "rl_policy" and pos.stop < pos.entry < pos.target


async def test_rule_strategies_keep_priority_and_no_policy_means_no_rl_trade():
    executor = types.SimpleNamespace(execute_signal=AsyncMock())
    engine, day = _engine(executor)
    engine._pending = {"TCS"}                    # rl_policy named, but no policy loaded
    await engine._entries(day[-1].start.replace(hour=10, minute=30))
    executor.execute_signal.assert_not_awaited()


async def test_the_policy_can_sell_its_own_position_early_and_nothing_else():
    from skopaq.scalping.rules import ScalpPosition

    executor = types.SimpleNamespace(execute_signal=AsyncMock(return_value=ExecutionResult(
        success=True, mode="paper", fill_price=1001.0)))
    engine, day = _engine(executor)
    rl = FakeRL(enter=False, exit=True)
    engine._rl = rl
    now = day[-1].start + timedelta(minutes=1)
    engine.positions["TCS"] = ScalpPosition("TCS", "rl_policy", 10, 1000.0, 990.0, 1015.0,
                                            now - timedelta(minutes=5))
    engine.codes["INFY"] = "NSE_1594"
    engine.series["INFY"] = engine.series["TCS"]
    engine.positions["INFY"] = ScalpPosition("INFY", "orb", 10, 1000.0, 990.0, 1015.0, now)
    engine._pending = {"TCS", "INFY"}
    await engine._entries(now)
    signal = executor.execute_signal.await_args.args[0]
    assert signal.action == "SELL" and "SCALP RL EXIT (rl-test)" in signal.reasoning
    assert "TCS" not in engine.positions and "INFY" in engine.positions   # orb untouched
    assert ("exit", "TCS", 5) in rl.asked and not any(a[1] == "INFY" for a in rl.asked)


def test_the_policy_code_never_places_orders_and_live_is_env_only():
    import inspect

    from skopaq.adaptive import rl_agents, rl_live
    from skopaq.env_overrides import LOCKED

    for mod in (rl_env, rl_agents, rl_live):
        src = inspect.getsource(mod)
        assert "execute_signal(" not in src and "OrderRouter(" not in src
        assert "place_order" not in src
    assert "rl_allow_live" in LOCKED
