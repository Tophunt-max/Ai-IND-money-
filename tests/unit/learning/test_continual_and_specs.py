"""Continual learning (adaptive models and RL policies) and no-code user strategies
(skopaq/adaptive/spec.py): validated specs, look-ahead safety, selection, and bundles
that rebuild their strategy from the stored spec."""

from __future__ import annotations

import types
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from skopaq.adaptive.framework import TrainConfig, load_bundle_model, train_bundle
from skopaq.adaptive.models import KINDS, available, from_meta, make
from skopaq.adaptive.spec import (
    FEATURES,
    SpecError,
    SpecStrategy,
    StrategyStore,
    check_spec,
    resolve,
    validate,
)
from skopaq.adaptive.strategy import AdaptiveStrategy, build_frame, feature_columns
from skopaq.learning.registry import ModelRegistry
from tests.unit.learning.test_adaptive import make_days
from tests.unit.learning.test_adaptive_framework import Small

SPEC = {"name": "my_mom", "timeframes": [60, 300], "periods": [10], "shifted": 1,
        "features": ["rsi", "roc", "atr_pct", "ema_gap", "macd_hist", "vwap_dist",
                     "minute_of_session"],
        "targets": [{"type": "win", "horizon": 10, "min_move_pct": 0.1}],
        "entry": {"type": "probability", "min": 0.55}}


@pytest.fixture(scope="module")
def hist():
    return {"AAA": make_days(1, 16), "BBB": make_days(2, 16, base=500)}


@pytest.fixture(scope="module")
def corr():
    return {"IDX": make_days(9, 16, base=20000)}


# ── continual learning: models ───────────────────────────────────────────────


@pytest.mark.parametrize("kind", KINDS)
def test_each_model_kind_continues_or_says_it_cannot(kind, tmp_path):
    if not available(kind):
        pytest.skip(f"{kind} not installed")
    rng = np.random.default_rng(0)
    X, X2 = rng.normal(size=(600, 5)), rng.normal(size=(600, 5))
    y, y2 = (X[:, 0] > 0).astype(float), (X2[:, 0] > 0).astype(float)
    params = {"epochs": 5} if kind in ("mlp", "ft_transformer") else {}
    first = make(kind, "classifier", params).fit(X[:500], y[:500], None, X[500:], y[500:])
    first.save(tmp_path)
    parent = from_meta(first.meta()).load(tmp_path)
    m = make(kind, "classifier", params).fit(X2[:500], y2[:500], None, X2[500:], y2[500:],
                                             init=parent)
    assert m.continued is m.supports_continual
    assert m.meta()["continued"] is m.continued
    if not m.supports_continual:
        assert "cannot be continued" in m.can_continue(parent)
    acc = ((m.predict(X2[500:]) > 0.5) == y2[500:]).mean()
    assert acc > 0.7
    assert make(kind, "regressor").can_continue(parent)          # task mismatch: refused


def test_trees_are_added_to_the_previous_ensemble(tmp_path):
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(1)
    X = rng.normal(size=(800, 4))
    y = (X[:, 0] + rng.normal(0, 0.3, 800) > 0).astype(float)
    a = make("lightgbm").fit(X[:600], y[:600], None, X[600:], y[600:])
    a.save(tmp_path)
    parent = from_meta(a.meta()).load(tmp_path)
    b = make("lightgbm").fit(X[:600], y[:600], None, X[600:], y[600:], init=parent)
    assert b._b.num_trees() > parent._b.num_trees()


# ── continual learning: the framework ────────────────────────────────────────


def test_a_continual_run_continues_the_previous_bundle_with_its_pipeline(tmp_path, hist,
                                                                          corr):
    reg = ModelRegistry(tmp_path)
    cfg = TrainConfig(model_kind="linear", min_rows=200, continual=True)
    first = train_bundle(Small(), hist, cfg, correlated=corr, registry=reg)
    assert first["report"]["continual"] == {"enabled": True, "parent": None,
                                            "fresh": "no previous model"}
    second = train_bundle(Small(), hist, cfg, correlated=corr, registry=reg)
    c = second["report"]["continual"]
    assert c["parent"] == first["version"] and c["models"] == {"&-win": "continued"}
    assert c["pipeline"] == "kept from the parent"
    a, b = load_bundle_model(first["model"]), load_bundle_model(second["model"])
    assert a.pipeline.to_dict()["s_a"] == b.pipeline.to_dict()["s_a"]          # frozen
    assert b.info["continual"]["parent"] == first["version"]
    assert second["state"] in ("candidate", "shadow")          # gated like any candidate
    # another model kind or strategy: no parent → fresh, with the reason
    other = train_bundle(Small(), hist, replace(cfg, model_kind="random_forest")
                         if available("random_forest") else replace(cfg, continual=True,
                                                                     model_kind="linear"),
                         correlated=corr, registry=ModelRegistry(tmp_path / "x"))
    assert other["report"]["continual"]["parent"] is None
    fresh = train_bundle(Small(), hist, replace(cfg, continual=False), correlated=corr,
                         registry=reg)
    assert fresh["report"]["continual"] == {"enabled": False, "parent": None}


def test_changed_features_mean_a_fresh_fit(tmp_path, hist, corr):
    reg = ModelRegistry(tmp_path)
    cfg = TrainConfig(model_kind="linear", min_rows=200, continual=True)

    class Small2(Small):
        name = "small"                      # same strategy name, other features
        periods = (14,)

    train_bundle(Small(), hist, cfg, correlated=corr, registry=reg)
    e = train_bundle(Small2(), hist, cfg, correlated=corr, registry=reg)
    assert e["report"]["continual"]["parent"] is None
    assert "other features" in e["report"]["continual"]["fresh"]


def test_the_rolling_backtest_chains_its_retrains(tmp_path):
    hist = {"AAA": make_days(1, 26)}
    cfg = TrainConfig(model_kind="linear", min_rows=200, continual=True)

    class NoCorr(Small):
        correlated = ()

    e = train_bundle(NoCorr(), hist, cfg, registry=ModelRegistry(tmp_path))
    blocks = e["report"]["rolling"]["blocks"]
    assert e["report"]["rolling"]["continual"] is True and len(blocks) >= 2
    assert blocks[0]["continued"] is False and all(b["continued"] for b in blocks[1:])


# ── continual learning: RL ───────────────────────────────────────────────────


def test_an_rl_policy_continues_the_previous_one_of_its_algorithm(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("safetensors")
    from skopaq.adaptive.rl_agents import RLConfig, rl_registry, train_and_register
    from tests.unit.learning.test_rl_agents import trend_days

    reg = rl_registry(tmp_path)
    h = {"UP": trend_days(1, 10)}
    first = train_and_register(h, RLConfig(algo="ppo", timesteps=2_000, continual=True),
                               registry=reg)
    assert first["report"]["continual"]["fresh"] == "no previous policy"
    second = train_and_register(h, RLConfig(algo="ppo", timesteps=2_000, continual=True),
                                registry=reg)
    assert second["report"]["continual"]["parent"] == first["version"]
    assert second["report"]["training"]["continued_from"] == first["version"]
    dqn = train_and_register(h, RLConfig(algo="dqn", timesteps=1_500, continual=True,
                                         learn_start=200), registry=reg)
    assert dqn["report"]["continual"]["parent"] is None
    assert "DQN" in dqn["report"]["continual"]["fresh"]


# ── no-code strategies ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad, msg", [
    ({"name": "X"}, "name"),
    ({"timeframes": [300]}, "base"),
    ({"timeframes": [60, 420]}, "not in"),
    ({"features": ["rsi", "magic"]}, "unknown"),
    ({"features": []}, "at least one"),
    ({"periods": [1]}, "periods"),
    ({"targets": [{"type": "win", "horizon": 5}, {"type": "max_up", "horizon": 5}]}, "not both"),
    ({"entry": {"type": "zscore", "min": 1}}, "regressor"),
    ({"code": "import os"}, "unknown field"),
    ({"correlated": ["A" * 30]}, "correlated"),
])
def test_invalid_specs_are_refused_with_the_reason(bad, msg):
    with pytest.raises(SpecError, match=msg):
        validate({**SPEC, **bad})


def test_a_spec_builds_only_its_features_point_in_time_and_its_targets(hist):
    s = SpecStrategy(SPEC)
    candles = [c for d in hist["AAA"][:3] for c in d]
    f = build_frame(s, candles, "AAA")
    feats = feature_columns(f)
    assert "%-rsi-period_10@60s" in feats and "%-macd_hist-gen@300s" in feats
    assert "%-vwap_dist-gen@60s" in feats and "%-minute_of_session@std" in feats
    assert not any("bb_width" in x for x in feats)
    assert s.model_type == "classifier" and s.primary_target == "&-win_10"
    check_spec(SPEC)                                # look-ahead guard passes
    every = {**SPEC, "features": list(FEATURES), "name": "every_feature"}
    check_spec(every)


def test_regression_targets_look_ahead_only_in_the_label():
    s = SpecStrategy({**SPEC, "targets": [{"type": "max_up", "horizon": 3},
                                          {"type": "forward_return", "horizon": 3}],
                      "entry": {"type": "zscore", "min": 1.0}})
    df = pd.DataFrame({"date": pd.date_range("2026-01-05 09:15", periods=8, freq="min"),
                       "open": [10.0] * 8, "high": [10, 11, 12, 13, 14, 15, 16, 17.0],
                       "low": [9.0] * 8, "close": [10, 10, 10, 10, 10, 10, 10, 12.0],
                       "volume": [1.0] * 8})
    out = s.set_targets(df, {})
    assert out["&-max_up_3"].iloc[0] == pytest.approx(13 / 10 - 1)       # bars 1..3
    assert out["&-forward_return_3"].iloc[4] == pytest.approx(12 / 10 - 1)
    assert np.isnan(out["&-max_up_3"].iloc[5])
    assert s.entry_signal({"targets": {"&-max_up_3": {"value": 0.03, "pred_mean": 0.01,
                                                      "pred_std": 0.01}}}, {}) is True


def test_store_selection_and_precedence(tmp_path):
    store = StrategyStore(tmp_path)
    cfg = types.SimpleNamespace(model_dir=str(tmp_path), adaptive_strategy="")
    assert type(resolve(cfg)) is AdaptiveStrategy           # nothing selected: default
    e = store.save(SPEC, by="a@x")
    assert e["revision"] == 1 and store.save(SPEC, by="a@x")["revision"] == 2
    with pytest.raises(KeyError):
        store.activate("nope", by="a@x")
    store.activate("my_mom", by="a@x", reason="try it")
    s = resolve(cfg)
    assert isinstance(s, SpecStrategy) and s.name == "my_mom"
    with pytest.raises(SpecError, match="active"):
        store.delete("my_mom", by="a@x")
    store.activate("default", by="a@x")
    assert type(resolve(cfg)) is AdaptiveStrategy
    store.delete("my_mom", by="a@x")
    assert store.list() == []
    events = [h["event"] for h in store.history()]
    assert events == ["saved", "saved", "activated", "activated", "deleted"]


def test_a_bundle_trained_on_a_spec_rebuilds_it_without_any_env(tmp_path, hist, monkeypatch):
    monkeypatch.delenv("SKOPAQ_ADAPTIVE_STRATEGY", raising=False)
    e = train_bundle(SpecStrategy(SPEC), {"AAA": hist["AAA"]},
                     TrainConfig(model_kind="linear", min_rows=200),
                     registry=ModelRegistry(tmp_path))
    b = load_bundle_model(e["model"])
    assert isinstance(b.strategy, SpecStrategy) and b.strategy.spec == validate(SPEC)
    day = make_days(4, 1)[0]
    r = b.predict_full(day, day[-1].start, None, "AAA")
    assert r["do_predict"] in (0, 1) and r["entry_rule"] == "strategy.entry_signal"
