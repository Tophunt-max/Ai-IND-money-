"""The Adaptive AI swing book: setups, the trade simulator, the swing model and its
registry, the SwingEngine (scanner + entries) and the daemon's use of it."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from skopaq.finance.charges import EQUITY_DELIVERY, round_trip
from skopaq.learning.registry import Gates, ModelRegistry
from skopaq.market.candles import Candle
from skopaq.swing import model as swing_model
from skopaq.swing.engine import SwingEngine, SwingIdea, params_from
from skopaq.swing.setups import SETUPS, WARMUP, Series, Setup, SwingParams, simulate

IST = timezone(timedelta(hours=5, minutes=30))
DAY0 = datetime(2021, 1, 1, tzinfo=IST)

# Lets a synthetic-data model through, so the registry plumbing can be tested
OPEN_GATES = Gates(min_test_samples=1, min_gated_trades=1, require_beats_baseline=False,
                   require_beats_champion=False, min_profit_factor=0.0,
                   require_ci_above_zero=False, max_drawdown_vs_baseline=1e9,
                   require_better_brier=False)


def walk(n=700, seed=0, drift=0.0006, vol=0.016, start=100.0):
    """Daily candles of a trending random walk with volume spikes."""
    rng = np.random.default_rng(seed)
    out, close = [], start
    for i in range(n):
        regime = 1.0 if (i // 120) % 3 != 2 else -1.0
        o = close * (1 + rng.normal(0, vol / 4))
        close = max(1.0, o * (1 + regime * drift + rng.normal(0, vol)))
        hi = max(o, close) * (1 + abs(rng.normal(0, vol / 2)))
        lo = min(o, close) * (1 - abs(rng.normal(0, vol / 2)))
        v = int(100_000 * (3 if rng.random() < 0.08 else 1) * (0.7 + rng.random() * 0.6))
        out.append(Candle(DAY0 + timedelta(days=i), o, hi, lo, close, v))
    return out


def flat(n, price=100.0, rng=1.0):
    return [Candle(DAY0 + timedelta(days=i), price, price + rng, price - rng, price, 1000)
            for i in range(n)]


def history(symbols=6, n=700):
    return {f"S{k}": walk(n, seed=k) for k in range(symbols)}


def last_bar_setups(candles, p=SwingParams()):
    """Indices where a setup fires (so a series cut there fires on its last bar)."""
    s = Series(candles)
    return [i for i in range(WARMUP, len(s)) if s.setup(i, p) is not None]


# ── setups ───────────────────────────────────────────────────────────────────


def test_features_and_setups_are_point_in_time():
    candles = walk(500, seed=3)
    full = Series(candles)
    fired = last_bar_setups(candles)
    assert fired, "the synthetic walk should fire some setups"
    for i in fired[:10] + [WARMUP, 350, 499]:
        cut = Series(candles[: i + 1])
        a, b = full.features(i), cut.features(i)
        assert a.keys() == b.keys()
        for k in a:
            assert (math.isnan(a[k]) and math.isnan(b[k])) or a[k] == pytest.approx(b[k]), k
        assert full.setup(i) == cut.setup(i)


def test_a_setup_has_an_atr_stop_and_an_r_multiple_target():
    candles = walk(500, seed=3)
    s = Series(candles)
    p = SwingParams(atr_mult=1.5, rr=3.0)
    i = last_bar_setups(candles, p)[0]
    st = s.setup(i, p)
    assert st.name in SETUPS
    assert st.entry == pytest.approx(s.c[i], abs=0.01)
    assert st.stop == pytest.approx(st.entry - 1.5 * s.atr[i], abs=0.011)
    assert st.target == pytest.approx(st.entry + 3.0 * st.risk, abs=0.011)
    assert s.setup(WARMUP - 1, p) is None   # no decision before the SMA 200 exists


# ── simulate ─────────────────────────────────────────────────────────────────


def _series(after):
    """WARMUP flat bars, the decision bar, then ``after`` (open, high, low, close)."""
    bars = flat(WARMUP + 1)
    for k, (o, h, lo, c) in enumerate(after):
        bars.append(Candle(DAY0 + timedelta(days=WARMUP + 1 + k), o, h, lo, c, 1000))
    return Series(bars)


P = SwingParams(rr=2.0, max_hold_days=3, slippage_pct=0.0, notional_inr=10_000.0)
SETUP = Setup("breakout", 100.0, 95.0, 110.0, "test")


def test_simulate_books_the_target_net_of_delivery_charges():
    s = _series([(100, 101, 99, 100), (100, 111, 99, 110), (110, 110, 110, 110)])
    out = simulate(s, WARMUP, SETUP, P)
    assert out["exit"] == "target" and out["held_days"] == 2
    qty = 100
    assert out["gross"] == pytest.approx((110 - 100) * qty)
    assert out["charges"] == pytest.approx(round_trip(EQUITY_DELIVERY, 100, 110, qty).total)
    assert out["charges"] > 0 and out["net"] == pytest.approx(out["gross"] - out["charges"])


def test_simulate_takes_the_stop_first_and_a_gap_fills_at_the_open():
    both = _series([(100, 112, 94, 100)] + [(100, 101, 99, 100)] * 3)
    assert simulate(both, WARMUP, SETUP, P)["exit"] == "stop"   # same bar: stop first
    gap = simulate(_series([(100, 101, 99, 100), (90, 91, 89, 90)] + [(90, 91, 89, 90)] * 2),
                   WARMUP, SETUP, P)
    assert gap["exit"] == "stop" and gap["gross"] == pytest.approx((90 - 100) * 100)


def test_simulate_time_exit_and_unfinished_trades():
    s = _series([(100, 101, 99, 100), (100, 101, 99, 101), (101, 102, 100, 102)])
    out = simulate(s, WARMUP, SETUP, P)
    assert out["exit"] == "time" and out["held_days"] == 3
    assert out["gross"] == pytest.approx((102 - 100) * 100)
    unfinished = _series([(100, 101, 99, 100)])
    assert simulate(unfinished, WARMUP, SETUP, P) is None   # outcome not known yet
    assert simulate(_series([]), WARMUP, SETUP, P) is None  # no next bar to buy at


def test_simulate_shifts_the_levels_to_the_fill():
    s = _series([(102, 103, 101, 102), (102, 112.5, 101, 112), (112, 112, 112, 112)])
    out = simulate(s, WARMUP, SETUP, P)
    assert out["fill"] == pytest.approx(102) and out["exit"] == "target"
    assert out["gross"] == pytest.approx((112 - 102) * int(10_000 // 102))


# ── the model and its registry ───────────────────────────────────────────────


def test_build_dataset_is_sorted_and_matches_the_simulator():
    hist = history(3, 600)
    rows = swing_model.build_dataset(hist)
    assert rows and [(r["date"], r["symbol"]) for r in rows] == sorted(
        (r["date"], r["symbol"]) for r in rows)
    r = rows[len(rows) // 2]
    s = Series(hist[r["symbol"]])
    i = next(k for k, c in enumerate(s.candles) if c.start.date().isoformat() == r["date"])
    st = s.setup(i)
    assert st.name == r["setup"]
    assert simulate(s, i, st)["net"] == pytest.approx(r["net"])
    assert r["win"] == (r["net"] > 0) and len(r["x"]) == len(swing_model.FEATURES)


@pytest.fixture
def trained(tmp_path, monkeypatch):
    monkeypatch.setattr(swing_model, "SWING_GATES", OPEN_GATES)
    config = SimpleNamespace(model_dir=str(tmp_path))
    monkeypatch.delenv("SKOPAQ_MODEL_DIR", raising=False)
    reg = swing_model.swing_registry(tmp_path)
    out = swing_model.train_and_register(
        history(), swing_model.SwingTrainConfig(model_kind="linear"), registry=reg)
    return config, reg, out


def test_train_and_register_writes_a_gated_pickle_free_bundle(trained):
    _config, reg, out = trained
    assert out["state"] == "shadow" and out["gate_failures"] == []
    entry = reg.get(out["version"])
    assert entry["state"] == "shadow" and reg.shadow()["version"] == out["version"]
    report = entry["report"]
    assert report["samples"] == report["test"] > 0
    assert {"baseline", "model", "threshold", "by_setup"} <= report.keys()
    path = out["path"]
    from pathlib import Path

    files = [p.name for p in Path(path).rglob("*") if p.is_file()]
    assert not [f for f in files if f.endswith((".pkl", ".pickle", ".joblib"))]
    meta = json.loads((Path(path) / "swing.json").read_text())
    assert meta["kind"] == swing_model.KIND and meta["features"] == list(swing_model.FEATURES)


def test_the_real_gates_keep_a_weak_model_a_candidate(tmp_path):
    reg = swing_model.swing_registry(tmp_path)
    out = swing_model.train_and_register(
        history(), swing_model.SwingTrainConfig(model_kind="linear"), registry=reg)
    if out["gate_failures"]:
        assert out["state"] == "candidate" and reg.shadow() is None
    else:  # a synthetic edge passed every gate: it may only be a shadow, never champion
        assert out["state"] == "shadow" and reg.champion() is None


def test_load_active_paper_uses_the_shadow_live_only_a_champion(trained):
    config, reg, out = trained
    model, state = swing_model.load_active(config, paper=True)
    assert state == "shadow" and model.version == out["version"]
    assert swing_model.load_active(config, paper=False) == (None, "none")

    later = ModelRegistry(reg.dir, clock=lambda: datetime.now(timezone.utc) + timedelta(days=30))
    later.promote(out["version"], approved_by="admin", reason="paper record ok")
    model, state = swing_model.load_active(config, paper=False)
    assert state == "champion" and model.version == out["version"]
    p = model.prob(Series(history(1)["S0"]).features(400), "breakout")
    assert p is None or 0.0 <= p <= 1.0


def test_a_tampered_bundle_is_refused(trained):
    config, _reg, out = trained
    from pathlib import Path

    meta = Path(out["path"]) / "swing.json"
    data = json.loads(meta.read_text())
    data["threshold"] = 0.0           # take every setup
    meta.write_text(json.dumps(data))
    with pytest.raises(Exception):
        swing_model.load(out["path"])
    assert swing_model.load_active(config, paper=True) == (None, "unloadable")


def test_two_models_trained_in_the_same_second_keep_separate_versions(trained):
    """A second registration must not overwrite the shadow's entry (a gate bypass)."""
    _config, reg, first = trained
    reg._clock = lambda: datetime.fromisoformat(reg.get(first["version"])["created_at"])
    a = reg.register(model={}, report={}, params={}, dataset={}, strategy_id="swing.linear")
    b = reg.register(model={}, report={}, params={}, dataset={}, strategy_id="swing.linear")
    assert len({first["version"], a["version"], b["version"]}) == 3
    assert (a["version"], b["version"]) == (first["version"] + "-2", first["version"] + "-3")
    assert reg.shadow()["version"] == first["version"]
    assert reg.get(first["version"])["state"] == "shadow"


def test_too_little_history_is_refused():
    with pytest.raises(ValueError):
        swing_model.train_and_register({"S0": walk(260)})


# ── the engine ───────────────────────────────────────────────────────────────


class FakeModel:
    version = "swing-test-1"

    def __init__(self, p, threshold=0.6):
        self.p, self.threshold = p, threshold

    def prob(self, feats, setup):
        return self.p


def _firing(seed=3):
    candles = walk(500, seed=seed)
    i = last_bar_setups(candles)[-1]
    return candles[: i + 1]


def _engine(model=None, *, paper=True, rules_only=False, candles=None, gate=None):
    data = {"FIRES": _firing(), "QUIET": flat(300)}

    async def source(symbol):
        if symbol not in data:
            raise RuntimeError("no data")
        return candles if candles is not None else data[symbol]

    return SwingEngine(SimpleNamespace(swing_rules_only=rules_only), paper=paper, model=model,
                       model_state="shadow" if model else "none", gate=gate, candles=source)


async def test_scan_keeps_ideas_above_the_models_threshold():
    ideas = await _engine(FakeModel(0.7)).scan(["FIRES", "QUIET", "MISSING"])
    assert [i.symbol for i in ideas] == ["FIRES"] and ideas[0].prob == 0.7
    d = ideas[0].as_dict()
    assert d["setup"] in SETUPS and d["stop"] < d["entry"] < d["target"]
    assert await _engine(FakeModel(0.5)).scan(["FIRES"]) == []


async def test_scan_without_a_model_trades_nothing_unless_rules_only_on_paper():
    assert await _engine(None).scan(["FIRES"]) == []
    ideas = await _engine(None, rules_only=True).scan(["FIRES"])
    assert [i.symbol for i in ideas] == ["FIRES"] and ideas[0].prob is None
    assert await _engine(None, paper=False, rules_only=True).scan(["FIRES"]) == []


async def test_scan_ranks_and_limits():
    engine = _engine(FakeModel(0.9))
    assert len(await engine.scan(["FIRES"] * 4, limit=2)) == 2


def test_params_follow_the_config():
    p = params_from(SimpleNamespace(atr_multiplier=1.5, swing_target_rr=3.0))
    assert (p.atr_mult, p.rr) == (1.5, 3.0)
    # The live book sells every position the same day, so the model learns same-day
    # outcomes: buy at the open, out at the stop, the target or the close
    assert p.max_hold_days == SwingParams().max_hold_days == 1


async def test_trade_goes_through_the_gate_then_the_executor(tmp_path):
    from skopaq.broker.models import ExecutionResult
    from skopaq.strategy.gate import SignalGate
    from skopaq.strategy.journal import SignalJournal
    from skopaq.strategy.signal import QualifyRules

    gate = SignalGate(journal=SignalJournal(tmp_path), model_dir=None, regime_dir=None,
                      rules=QualifyRules(require_reliable_regime=False, min_stop_pct=0.005,
                                         max_stop_pct=0.15, min_net_rr=1.0))
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    daily = [Candle(today - timedelta(days=80 - i), 100 + i, 102 + i, 99 + i, 101 + i, 10_000)
             for i in range(80)]
    engine = _engine(FakeModel(0.7), gate=gate)
    executor = SimpleNamespace(execute_signal=AsyncMock(side_effect=lambda s, **k: ExecutionResult(
        success=True, signal=s, mode="paper", fill_price=180.0)))

    idea = SwingIdea("TCS", Setup("trend_pullback", 180.0, 172.8, 195.2, "test"), 0.7, 0.7,
                     {}, daily)
    result = await engine.trade(idea, executor, trade_date="2026-10-12", regime_scale=0.5)
    sent = executor.execute_signal.await_args.args[0]
    assert sent.action == "BUY" and sent.stop_loss == 172.8 and sent.confidence == 70
    assert sent.agent_state["target_rr"] == 2.0 and sent.agent_state["signal_id"]
    assert executor.execute_signal.await_args.kwargs["regime_scale"] == 0.5
    assert result.execution.success and result.evidence["model"] == "swing-test-1"
    row = SignalJournal(tmp_path).signals()[0]
    assert row["strategy_id"] == "swing.trend_pullback" and row["status"] == "QUALIFIED"

    # A poor reward:risk after charges never reaches the executor
    executor.execute_signal.reset_mock()
    poor = SwingIdea("TCS", Setup("breakout", 180.0, 172.8, 181.0, "test"), 0.7, 0.7, {}, daily)
    refused = await engine.trade(poor, executor)
    assert not refused.execution.success and "Not qualified" in refused.execution.rejection_reason
    executor.execute_signal.assert_not_awaited()


async def test_trade_never_raises():
    engine = _engine(FakeModel(0.7))
    executor = SimpleNamespace(execute_signal=AsyncMock(side_effect=RuntimeError("broker down")))
    idea = SwingIdea("TCS", Setup("breakout", 180.0, 172.8, 195.2, "t"), 0.7, 0.7, {}, [])
    result = await engine.trade(idea, executor)
    assert "execution failed" in result.error


def test_the_exit_plan_uses_the_setups_r_multiple():
    from skopaq.execution.exit_plan import ExitPlanner

    off = ExitPlanner(SimpleNamespace(monitor_target_mode="off"))
    plan = off.make("TCS", "paper", 100.0, 10, 96.0, target_rr=2.5)
    assert plan.stop_loss == 96.0 and plan.target == 110.0
    assert off.make("TCS", "paper", 100.0, 10, 96.0).target is None
    rr = ExitPlanner(SimpleNamespace(monitor_target_mode="rr", monitor_target_rr=2.0))
    assert rr.make("TCS", "paper", 100.0, 10, 96.0, target_rr=2.5).target == 108.0


def test_the_executor_passes_the_signals_target_rr():
    from skopaq.broker.models import ExecutionResult, TradingSignal
    from skopaq.execution.executor import Executor

    planner = MagicMock()
    planner.plan_for_entry.return_value = SimpleNamespace(stop_loss=96.0, target=110.0)
    ex = Executor.__new__(Executor)
    ex._planner = planner
    sig = TradingSignal(symbol="TCS", action="BUY", confidence=70, stop_loss=96.0,
                        agent_state={"target_rr": 2.5})
    ex._plan_exit(sig, ExecutionResult(success=True, signal=sig, mode="paper",
                                       fill_price=100.0), 10)
    assert planner.plan_for_entry.call_args.kwargs["target_rr"] == 2.5
    assert sig.target == 110.0


# ── the daemon ───────────────────────────────────────────────────────────────


def test_the_daemon_builds_the_swing_engine_for_its_mode(monkeypatch):
    from skopaq.execution.daemon import TradingDaemon

    built = []

    def from_config(config, *, paper):
        built.append(paper)
        return SimpleNamespace(model=None, model_state="none", rules_only=False)

    monkeypatch.setattr(SwingEngine, "from_config", staticmethod(from_config))
    d = TradingDaemon.__new__(TradingDaemon)
    d._wire_swing_engine(SimpleNamespace(), True)
    d._wire_swing_engine(SimpleNamespace(), False)
    assert built == [True, False] and d._swing.model_state == "none"

    def broken(config, *, paper):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(SwingEngine, "from_config", staticmethod(broken))
    d._wire_swing_engine(SimpleNamespace(), True)
    assert d._swing is None   # no swing entries, PRE_OPEN goes on
