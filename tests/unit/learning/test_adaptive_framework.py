"""The adaptive framework (skopaq/adaptive/{strategy,pipeline,models,artifacts,framework}):
user strategies with a look-ahead guard, the FreqAI-style pipeline (scaler, PCA, SVM,
DI, DBSCAN, noise), every model library, recency weights, rich predictions, and
verifiable bundles."""

from __future__ import annotations

import json
import math
import tarfile
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from skopaq.adaptive import artifacts
from skopaq.adaptive.framework import (
    AdaptiveBundle,
    TrainConfig,
    build_dataset,
    config_with,
    fit,
    split,
    train_bundle,
)
from skopaq.adaptive.models import KINDS, available, make, recency_weights
from skopaq.adaptive.pipeline import Pipeline, PipelineConfig
from skopaq.adaptive.strategy import (
    AdaptiveStrategy,
    LookAheadError,
    build_frame,
    check_lookahead,
    feature_columns,
    load_strategy,
    target_columns,
)
from skopaq.learning.registry import ModelRegistry
from tests.unit.learning.test_adaptive import make_days

EXAMPLE = str(Path(__file__).resolve().parents[3] / "examples" /
              "adaptive_strategy_example.py")


class Small(AdaptiveStrategy):
    name = "small"
    timeframes = (60, 300)
    periods = (10,)
    shifted = 1
    correlated = ("IDX",)


@pytest.fixture(scope="module")
def hist():
    return {"AAA": make_days(1, 16), "BBB": make_days(2, 16, base=500)}


@pytest.fixture(scope="module")
def corr():
    return {"IDX": make_days(9, 16, base=20000)}


# ── strategies ───────────────────────────────────────────────────────────────


def test_user_hooks_build_features_and_targets_point_in_time(hist, corr):
    candles = [c for d in hist["AAA"][:3] for c in d]
    f = build_frame(Small(), candles, "AAA", {"IDX": [c for d in corr["IDX"][:3] for c in d]})
    feats, tgts = feature_columns(f), target_columns(f)
    assert "%-rsi-period_10@60s" in feats and "%-rsi-period_10@300s" in feats
    assert "%-rsi-period_10_shift-1@60s" in feats and "%-ema_gap-gen@300s" in feats
    assert "IDX|%-roc-period_10@60s" in feats and "%-minute_of_session@std" in feats
    assert tgts == ["&-win"]
    # A 5-minute bar is joined only once it closed: on the first day nothing of the
    # 09:15–09:20 bar is visible before it closes (later days see the previous day's bars)
    t = pd.to_datetime(f["date"])
    day1 = t.dt.date == t.dt.date.iloc[0]
    early = f.loc[day1 & (t.dt.strftime("%H:%M") < "09:19"), "%-rsi-period_10@300s"]
    assert early.isna().all()
    raw = f.loc[day1, "%-roc-period_10@300s"]
    assert raw.iloc[:4].isna().all()
    check_lookahead(Small(), candles, correlated={"IDX": [c for d in corr["IDX"][:3]
                                                          for c in d]})


def test_lookahead_in_a_feature_is_refused(hist):
    class Cheat(Small):
        def feature_engineering_expand_basic(self, df, metadata):
            df["%-next"] = df["close"].shift(-1)
            return df

    class Centred(Small):
        def feature_engineering_expand_all(self, df, period, metadata):
            df["%-centred"] = df["close"].rolling(period, center=True).mean()
            return df

    candles = [c for d in hist["AAA"][:2] for c in d]
    for cls in (Cheat, Centred):
        with pytest.raises(LookAheadError):
            check_lookahead(cls(), candles)
    with pytest.raises(LookAheadError):
        build_dataset(Cheat(), {"AAA": hist["AAA"][:3]}, TrainConfig())


def test_loading_a_strategy_file():
    s = load_strategy(EXAMPLE + ":MomentumRegressor")
    assert s.name == "momentum_regressor" and s.model_type == "regressor"
    assert isinstance(load_strategy(""), AdaptiveStrategy)
    with pytest.raises(ValueError):
        load_strategy(EXAMPLE + ":NotThere")
    with pytest.raises(ValueError):
        load_strategy("no-colon")


# ── pipeline ─────────────────────────────────────────────────────────────────


def test_pipeline_steps_and_json_round_trip():
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, 8))
    X[:6] += 25                                      # gross outliers
    X[:, 3] = 5.0                                    # constant: dropped
    X[:, 4] = X[:, 0] * 2                            # redundant: PCA folds it
    y = rng.integers(0, 2, 600)
    cfg = PipelineConfig(scaler="minmax", pca=True, svm=True, dbscan=True, di_threshold=1.5,
                         noise_std=0.01)
    pipe = Pipeline(cfg)
    Z, (y_kept,) = pipe.fit_transform(X, [f"c{i}" for i in range(8)], y)
    assert 3 not in pipe.keep and len(pipe.pca_components) < 7
    assert len(Z) == len(y_kept) < 600 and pipe.removed            # outliers removed
    again = Pipeline.from_dict(json.loads(json.dumps(pipe.to_dict())))
    probe = np.vstack([rng.normal(size=(5, 8)), np.full((1, 8), 40.0)])
    probe[:, 3] = 5.0
    assert np.allclose(again.transform(probe), pipe.transform(probe))
    o = again.outliers(again.transform(probe))
    assert o["any"][-1] and o["di"][-1] and not o["any"][:5].all()
    assert np.isfinite(o["di_values"]).all()
    for scaler in ("standard", "robust", "none"):
        p = Pipeline(PipelineConfig(scaler=scaler))
        Zs, _ = p.fit_transform(X, [f"c{i}" for i in range(8)])
        assert Zs.shape[0] == 600 and np.allclose(p.transform(X), Zs)   # no noise: equal


def test_recency_weights():
    w = recency_weights(100, 0.9)
    assert w[-1] == 1.0 and w[0] < w[50] < w[-1]
    assert (recency_weights(5, 0) == 1).all()


# ── models ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("task", ["classifier", "regressor"])
def test_every_model_kind_learns_saves_and_reloads(kind, task, tmp_path):
    if not available(kind):
        pytest.skip(f"{kind} not installed")
    rng = np.random.default_rng(1)
    X = rng.normal(size=(800, 6))
    signal = X[:, 0] - 0.5 * X[:, 1]
    y = (signal + rng.normal(scale=0.5, size=800) > 0).astype(float) if task == "classifier" \
        else signal + rng.normal(scale=0.3, size=800)
    params = {"epochs": 15} if kind in ("mlp", "ft_transformer") else {}
    m = make(kind, task, params).fit(X[:600], y[:600], recency_weights(600, 0.9),
                                     X[600:700], y[600:700])
    p = m.predict(X[700:])
    if task == "classifier":
        assert ((p > 0.5) == (y[700:] > 0.5)).mean() > 0.7
    else:
        assert np.corrcoef(p, y[700:])[0, 1] > 0.6
    files = m.save(tmp_path)
    assert files and not any(f.endswith((".pkl", ".pickle", ".joblib")) for f in files)
    from skopaq.adaptive.models import from_meta

    again = from_meta(json.loads(json.dumps(m.meta()))).load(tmp_path)
    assert np.allclose(again.predict(X[700:]), p, atol=1e-5)
    assert m.importance


# ── training, predictions, bundles ───────────────────────────────────────────


@pytest.fixture(scope="module")
def trained(tmp_path_factory, hist, corr):
    reg = ModelRegistry(tmp_path_factory.mktemp("models"))
    cfg = config_with(TrainConfig(model_kind="linear", weight_factor=0.9, min_rows=200),
                      di_threshold=1.5)
    entry = train_bundle(Small(), hist, cfg, correlated=corr, registry=reg)
    return reg, entry


def test_training_reports_and_registers_a_candidate_bundle(trained):
    reg, entry = trained
    rep = entry["report"]
    assert entry["state"] in ("candidate", "shadow") and reg.champion() is None
    assert rep["samples"] > 50 and rep["rule"]["type"] == "probability"
    assert rep["brier"] < rep["brier_base_rate"]
    assert rep["pipeline"]["di"] and rep["importance"] and rep["rolling"]["retrains"] >= 0
    assert entry["model"]["kind"] == "adaptive-bundle-v1"


def test_split_is_chronological_with_the_label_embargo(hist, corr):
    frame = build_dataset(Small(), hist, TrainConfig(), corr)
    tr, va, te = split(frame, TrainConfig())
    assert tr["_closed_at"].max() < va["_decided_at"].min()
    assert va["_closed_at"].max() < te["_decided_at"].min()
    assert frame["_net"].notna().all() and (frame["_closed_at"] > frame["_decided_at"]).all()


def test_rich_prediction_output(trained, corr):
    from skopaq.learning.model import load_model

    reg, entry = trained
    b = load_model(entry["model"])
    assert isinstance(b, AdaptiveBundle)
    day = make_days(1, 17)[16]
    idx = make_days(9, 17, base=20000)[16]
    b.seed_history("AAA", [c for d in make_days(1, 16) for c in d])
    b.seed_history("IDX", [c for d in make_days(9, 16, base=20000) for c in d])
    r = b.predict_full(day[:150], day[149].start + timedelta(minutes=1), {"IDX": idx[:150]},
                       "AAA")
    assert r["do_predict"] in (0, 1) and r["missing_features"] == 0
    t = r["targets"]["&-win"]
    assert {"value", "label_mean", "label_std", "pred_mean", "pred_std", "zscore"} <= set(t)
    assert set(r["detectors"]) == {"svm", "di", "dbscan"} and r["di"] is not None
    assert isinstance(r["entry"], bool) and r["entry_rule"].startswith("P ≥")
    assert r["top_features"] and r["age_hours"] is not None
    cold = b.predict_full(day[:5], day[4].start + timedelta(minutes=1), {}, "ZZZ")
    assert cold["do_predict"] == -1 and cold["entry"] is False
    for _ in range(25):                          # live prediction statistics take over
        b.predict_features({c: 0.0 for c in b.features}, symbol="AAA")
    r2 = b.predict_features({c: 0.0 for c in b.features}, log=False)
    assert r2["targets"]["&-win"]["stats_source"] == "live"


def test_regressor_strategy_with_its_own_entry_signal(tmp_path, hist, corr):
    s = load_strategy(EXAMPLE + ":MomentumRegressor")
    s.correlated = ("IDX",)
    out = train_bundle(s, {"AAA": hist["AAA"]}, TrainConfig(model_kind="linear",
                                                            min_rows=200), correlated=corr)
    rep = out["report"]
    assert rep["targets"] == ["&-s_close"] and rep["rule"]["type"] == "zscore"
    assert "mae" in rep and rep["mae"] <= rep["mae_mean_baseline"] * 1.5
    fitted = out["fitted"]
    preds = {"&-s_close": np.array([10.0, -10.0])}
    assert list(fitted.entries(preds)) == [True, False]   # the strategy's own rule


def test_bundle_integrity_export_import_and_purge(trained, tmp_path):
    reg, entry = trained
    bdir = Path(entry["model"]["path"])
    m = artifacts.verify(bdir)
    assert m["libraries"]["numpy"] and "pipeline.json.gz" in m["files"]
    archive = artifacts.export_bundle(bdir, tmp_path / "b.tar.gz")
    dest = artifacts.import_bundle(archive, tmp_path / "imported")
    assert artifacts.verify(dest)["version"] == m["version"]
    with pytest.raises(artifacts.IntegrityError):
        artifacts.import_bundle(archive, tmp_path / "imported")       # already there
    # tamper with a model file: loading refuses it
    target = next(p for p in dest.rglob("*") if p.is_file() and p.suffix == ".json"
                  and p.name not in ("manifest.json",))
    target.write_text(target.read_text() + " ")
    with pytest.raises(artifacts.IntegrityError):
        AdaptiveBundle(dest)
    # an archive with a path-traversal member is refused
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        p = tmp_path / "x.txt"
        p.write_text("x")
        tar.add(p, arcname="../x.txt")
    with pytest.raises(artifacts.IntegrityError):
        artifacts.import_bundle(evil, tmp_path / "other")
    # purge keeps protected bundles
    root = tmp_path / "purge"
    for i in range(4):
        d = root / f"b{i}"
        d.mkdir(parents=True)
        (d / "f.txt").write_text(str(i))
        artifacts.write_manifest(d, version=f"b{i}", extra={})
    removed = artifacts.purge(root, keep=1, protected={"b0"})
    assert "b0" not in removed and len(removed) == 2


def test_gate_uses_the_bundle_entry_rule_when_the_model_gate_is_on(trained):
    from skopaq.learning.model import load_model
    from skopaq.strategy.gate import SignalGate
    from skopaq.strategy.signal import QualifyRules

    reg, entry = trained
    gate = SignalGate(journal=None, rules=QualifyRules(require_reliable_regime=False))
    gate.models.champion = load_model(entry["model"])
    gate.models.champion_version = entry["version"]
    gate.rules = QualifyRules(require_reliable_regime=False, require_model_entry=True)
    day = make_days(4, 1)[0]
    now = day[30].start + timedelta(minutes=1)
    c, d = gate.decide(strategy_id="scalp.orb", symbol="NEW", product="equity_intraday",
                       direction=1, decided_at=now, timeframe_s=60, entry=day[30].close,
                       stop=day[30].close * 0.99, target=day[30].close * 1.03, qty=10,
                       reason="t", candles=day[:31])
    assert c.evidence["adaptive"]["do_predict"] == -1          # no warm-up history
    assert any("not enough data" in r for r in d.reasons)
    assert c.model_prob is None or math.isnan(c.model_prob) or c.model_prob >= 0


def test_fit_refuses_a_strategy_without_targets(hist):
    class NoTarget(Small):
        def set_targets(self, df, metadata):
            return df

    frame = build_dataset(Small(), {"AAA": hist["AAA"][:8]}, TrainConfig())
    with pytest.raises(ValueError, match="no targets"):
        fit(NoTarget(), frame.drop(columns=target_columns(frame)), frame.iloc[:0],
            TrainConfig(model_kind="linear"))
