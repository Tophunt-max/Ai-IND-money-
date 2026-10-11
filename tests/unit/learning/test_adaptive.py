"""FreqAI-style adaptive AI (skopaq/adaptive/): point-in-time feature expansion, the
Dissimilarity Index, outlier filtering, training with every model kind, the rolling
retrain backtest, registration as a candidate only, hyperopt with an approval-gated
holdout, and the research-only RL module."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from skopaq.adaptive.engine import (
    AdaptiveConfig,
    build_forward_dataset,
    rolling_backtest,
    train_adaptive,
)
from skopaq.adaptive.features import FeatureConfig, FeatureExpander
from skopaq.adaptive.outliers import DissimilarityIndex, filter_training
from skopaq.market.candles import Candle

IST = timezone(timedelta(hours=5, minutes=30))


def make_days(seed: int, n: int = 24, base: float = 1000.0, edge: bool = True):
    rng = random.Random(seed)
    out = []
    for d in range(n):
        t0 = datetime(2026, 6, 1, 9, 15, tzinfo=IST) + timedelta(days=d)
        p, day = base, []
        for i in range(375):
            drift = 0.0003 * math.sin(i / 25 + d) if edge else 0.0
            c = p * (1 + drift + rng.gauss(0, 0.0008))
            day.append(Candle(t0 + timedelta(minutes=i), p,
                              max(p, c) * (1 + abs(rng.gauss(0, 0.0003))),
                              min(p, c) * (1 - abs(rng.gauss(0, 0.0003))), c,
                              1000 + rng.randint(0, 500)))
            p = c
        out.append(day)
    return out


CFG = AdaptiveConfig(features=FeatureConfig(timeframes=(60, 300), periods=(10,),
                                            shifts=(1,), correlated=("IDX",), window=40),
                     stride=6, train_days=12, retrain_days=4, min_rows=200)


def test_expanded_features_are_point_in_time():
    day = make_days(1, 1)[0]
    exp = FeatureExpander(CFG.features)
    as_of = day[100].start + timedelta(minutes=1)
    a = exp.expand(day[:101], as_of, {"IDX": day})
    future = day[:101] + [Candle(c.start, c.open * 3, c.high * 3, c.low * 3, c.close * 3, 9)
                          for c in day[101:]]
    b = exp.expand(future, as_of, {"IDX": future})
    assert {k: v for k, v in a.items() if v == v} == {k: v for k, v in b.items() if v == v}
    assert "rsi_10@300s" in a and "rsi_10@60s_s1" in a and "IDX:roc_10@60s" in a
    assert set(exp.names()) == set(a)
    assert FeatureConfig.from_dict(CFG.features.as_dict()) == CFG.features


def test_dissimilarity_index_flags_unseen_markets_and_round_trips():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 6))
    di = DissimilarityIndex.fit(X, threshold=1.5)
    inside, d_in = di.is_outlier(rng.normal(size=6))
    outside, d_out = di.is_outlier(np.full(6, 8.0))
    assert not inside and outside and d_out > 5 * d_in
    again = DissimilarityIndex.from_dict(di.to_dict())
    assert np.allclose(again.score(X[:5]), di.score(X[:5]))
    with pytest.raises(ValueError):
        DissimilarityIndex.fit(X[:2])
    X2 = X.copy()
    X2[3] = 50.0
    keep = filter_training(X2)
    assert not keep[3] and keep.sum() >= 495


@pytest.fixture(scope="module")
def dataset():
    return build_forward_dataset({"AAA": make_days(1), "BBB": make_days(2, base=500)}, CFG,
                                 correlated={"IDX": make_days(9, base=20000)})


def test_forward_dataset_labels_are_net_of_costs_and_after_the_decision(dataset):
    assert len(dataset) > 1000
    e = dataset[0]
    assert e.closed_at > e.decided_at and e.evidence_last_bar < e.decided_at
    t0 = datetime(2026, 6, 1, 9, 15, tzinfo=IST)
    flat_day = [Candle(t0 + timedelta(minutes=i), 100, 100, 100, 100, 10) for i in range(375)]
    flat = build_forward_dataset({"Z": [flat_day]}, CFG)
    assert flat and all(x.label == 0 and x.net_pnl < 0 for x in flat)   # charges only


@pytest.mark.parametrize("kind", ["logistic", "lightgbm", "xgboost", "catboost"])
def test_train_adaptive_every_model_kind(dataset, kind):
    if kind != "logistic":
        pytest.importorskip(kind)
    cfg = AdaptiveConfig(**{**CFG.__dict__, "model_kind": kind})
    model, rep = train_adaptive(dataset, cfg)
    assert rep["model_kind"] == kind and rep["samples"] > 100
    assert rep["brier"] < rep["brier_base_rate"]              # the synthetic edge is learnt
    assert 0 <= rep["outlier_rate"] <= 1
    from skopaq.learning.model import load_model

    again = load_model(model.to_dict())
    f = dataset[-1].features
    assert again.predict_features(f) == pytest.approx(model.predict_features(f))


def test_rolling_backtest_is_out_of_sample_each_block(dataset):
    rb = rolling_backtest(dataset, CFG)
    assert rb["retrains"] >= 2
    starts = [b["from"] for b in rb["blocks"]]
    assert starts == sorted(starts)
    assert rolling_backtest(dataset[:50], AdaptiveConfig(train_days=99))["retrains"] == 0


def test_retrain_registers_a_candidate_never_a_champion(tmp_path):
    from skopaq.adaptive.retrain import read_state, retrain_due, train_and_register
    from skopaq.learning.registry import ModelRegistry

    reg = ModelRegistry(tmp_path)
    entry = train_and_register({"AAA": make_days(1)}, CFG, reg,
                               correlated={"IDX": make_days(9, base=20000)})
    assert entry["state"] in ("candidate", "shadow") and reg.champion() is None
    assert entry["strategy_id"] == "adaptive.forward" and entry["report"]["rolling"]
    cfg = SimpleNamespace(adaptive_auto_retrain=True, adaptive_retrain_hours=24)
    assert retrain_due(cfg, tmp_path) and not read_state(tmp_path)
    assert not retrain_due(SimpleNamespace(adaptive_auto_retrain=False), tmp_path)


async def test_run_retrain_records_its_state_and_survives_failures(tmp_path, monkeypatch):
    from skopaq.adaptive import retrain
    from skopaq.learning.registry import ModelRegistry

    async def fetch(client, sym, days):
        raise RuntimeError("no token")
    monkeypatch.setattr("skopaq.scalping.backtest.fetch_days", fetch)
    config = SimpleNamespace(adaptive_symbols="AAA", adaptive_days=10, adaptive_retrain_hours=24,
                             adaptive_auto_retrain=True)
    state = await retrain.run_retrain(config, object(), ModelRegistry(tmp_path))
    assert state["ok"] is False and "no candle history" in state["error"]
    assert retrain.read_state(tmp_path)["finished_at"]
    assert not retrain.retrain_due(config, tmp_path)          # waits for the interval


def test_gate_feeds_candles_to_an_adaptive_champion(tmp_path, dataset):
    from skopaq.adaptive.engine import fit_model
    from skopaq.strategy.gate import SignalGate
    from skopaq.strategy.signal import QualifyRules

    model = fit_model(dataset[:600], CFG)
    gate = SignalGate(journal=None, rules=QualifyRules(require_reliable_regime=False,
                                                       block_model_outliers=True))
    gate.models.champion, gate.models.champion_version = model, "adaptive-x"
    day = make_days(3, 1)[0]
    now = day[200].start + timedelta(minutes=1)
    c, d = gate.decide(strategy_id="scalp.orb", symbol="AAA", product="equity_intraday",
                       direction=1, decided_at=now, timeframe_s=60, entry=day[200].close,
                       stop=day[200].close * 0.99, target=day[200].close * 1.03, qty=10,
                       reason="t", candles=day[:201], correlated={"IDX": day})
    ad = c.evidence["adaptive"]
    assert 0 <= c.model_prob <= 1 and ad["di"] is not None and "outlier" in ad
    weird = [Candle(x.start, x.open * 5, x.high * 9, x.low, x.close * 7, 10 ** 7)
             for x in day[:201]]
    c2, d2 = gate.decide(strategy_id="scalp.orb", symbol="AAA", product="equity_intraday",
                         direction=1, decided_at=now, timeframe_s=60, entry=100.0, stop=99.0,
                         target=103.0, qty=1, reason="t", candles=weird)
    assert c2.evidence["adaptive"]["outlier"]
    assert any("unlike the model's training data" in r for r in d2.reasons)


def test_hyperopt_proves_on_the_holdout_and_needs_approval(tmp_path):
    pytest.importorskip("optuna")
    from skopaq.adaptive.hyperopt import ParamsStore, apply_params, current_params, hyperopt
    from skopaq.learning.registry import SafetyKeyError
    from skopaq.scalping.settings import ScalpSettings

    settings = ScalpSettings.from_config(SimpleNamespace())
    res = hyperopt({"AAA": make_days(1, 10)}, settings, trials=6, min_trades=1)
    assert set(res["best_params"]) == {"rr", "orb_minutes", "range_lookback", "vwap_band_pct",
                                       "min_reward_to_cost", "max_hold_minutes"}
    assert res["holdout_days"] == 3
    store = ParamsStore(tmp_path)
    store.save_candidate({**res, "better_on_holdout": False})
    with pytest.raises(ValueError, match="holdout"):
        store.approve(by="admin", reason="x")
    store.save_candidate({**res, "better_on_holdout": True})
    store.approve(by="admin", reason="beat the holdout")
    applied = ScalpSettings.from_config(SimpleNamespace(model_dir=str(tmp_path)))
    assert current_params(applied)["rr"] == pytest.approx(
        min(3.0, max(1.0, res["best_params"]["rr"])))
    assert store.rollback() is None and store.params() is None
    clamped = apply_params(settings, {"rr": 99, "max_daily_loss": 1})
    assert clamped.params.rr == 3.0 and clamped.max_daily_loss == settings.max_daily_loss
    with pytest.raises(SafetyKeyError):
        store.save_candidate({"best_params": {"max_daily_loss_pct": 0.5}})


def test_rl_is_research_only_and_rewards_add_up_to_net_pnl():
    from skopaq.adaptive import rl

    assert rl.RESEARCH_ONLY is True
    import skopaq.adaptive.rl as mod
    src = open(mod.__file__).read()
    assert "execute_signal" not in src and "OrderRouter" not in src.replace(
        "``OrderRouter``", "")
    day = make_days(4, 1)[0]
    env = rl.TradingEnv(day)
    total, a = 0.0, 0
    env.reset()
    while not env.done:
        a = 1 - a if env.t % 40 == 0 else a
        _, r, _ = env.step(a)
        total += r
    assert env.qty == 0 and env.trades > 0 and env.charges > 0
    assert total == pytest.approx(env.net)
    res = rl.research_run(make_days(5, 10), epochs=1)
    assert res["research_only"] and res["test_days"] == 3 and "baseline_buy_hold" in res
