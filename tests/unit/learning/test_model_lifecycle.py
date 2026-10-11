"""Learning lifecycle (skopaq/learning/dataset, model, evaluation, registry, pipeline):
point-in-time features, chronological splits, a deterministic model, out-of-sample
gates, champion/challenger with rollback, and immutable safety limits."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from skopaq.learning.dataset import (
    FEATURE_NAMES,
    Example,
    check_point_in_time,
    chronological_split,
    features_from_evidence,
    from_journal,
    walk_forward_folds,
)
from skopaq.learning.evaluation import drift_report, psi, trade_stats
from skopaq.learning.model import LogisticModel, calibration_bins
from skopaq.learning.pipeline import InsufficientData, train_candidate
from skopaq.learning.registry import (
    Gates,
    ModelRegistry,
    SafetyKeyError,
    assert_no_safety_keys,
    check_gates,
)

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=IST)


def synthetic(n=400, seed=1, edge=True):
    """Examples where a high 'rsi' feature predicts a net win (when ``edge``)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        x = rng.normal()
        win = (x + rng.normal() * 0.8 > 0) if edge else rng.random() > 0.5
        feats = {name: float(rng.normal()) for name in FEATURE_NAMES}
        feats["rsi"] = x
        pnl = 120.0 if win else -100.0
        at = T0 + timedelta(hours=3 * i)
        out.append(Example(decided_at=at, features=feats, label=int(win), net_pnl=pnl,
                           symbol="TCS", closed_at=at + timedelta(minutes=20)))
    return out


def test_features_are_point_in_time_and_leakage_is_caught():
    f = features_from_evidence({"indicators": {"close": 100, "rsi": 55, "ema_fast": 101,
                                               "ema_slow": 100}}, entry=100, stop=99,
                               target=102, decided_at=T0)
    assert set(f) == set(FEATURE_NAMES)
    assert f["rsi"] == 55 and f["reward_risk"] == 2 and math.isnan(f["adx"])
    good = Example(decided_at=T0, features=f, label=1, net_pnl=1.0,
                   evidence_last_bar=T0 - timedelta(minutes=1))
    assert check_point_in_time([good]) == []
    leaky = Example(decided_at=T0, features={**f, "net_pnl": 5.0}, label=1, net_pnl=5.0,
                    evidence_last_bar=T0 + timedelta(minutes=1),
                    closed_at=T0 - timedelta(minutes=1))
    problems = " ".join(check_point_in_time([leaky]))
    assert "outcome features" in problems and "after the decision" in problems
    assert "closed before" in problems


def test_journal_rows_without_outcomes_are_unlabelled_not_losses():
    rows = [
        {"status": "QUALIFIED", "decided_at": T0.isoformat(), "entry": 100, "stop": 99,
         "target": 102, "evidence": {}, "execution": {"status": "FILLED"},
         "outcome": {"net_pnl": -3.0, "closed_at": (T0 + timedelta(minutes=5)).isoformat()}},
        {"status": "QUALIFIED", "decided_at": T0.isoformat(), "entry": 100, "stop": 99,
         "target": 102, "evidence": {}, "execution": {"status": "UNCONFIRMED"},
         "outcome": None},
        {"status": "REJECTED", "decided_at": T0.isoformat()},
    ]
    rep = from_journal(rows)
    assert [e.label for e in rep.examples] == [0, None]
    assert rep.unlabelled == 1 and rep.rejected_signals == 1 and not rep.leakage


def test_chronological_split_never_shuffles_and_embargoes_boundaries():
    ex = synthetic(100)
    tr, va, te = chronological_split(list(reversed(ex)), embargo=timedelta(hours=4))
    assert max(e.decided_at for e in tr) < min(e.decided_at for e in va)
    assert max(e.decided_at for e in va) < min(e.decided_at for e in te)
    assert len(tr) + len(va) + len(te) < 100                 # embargo dropped boundary rows
    for train, test in walk_forward_folds(ex, folds=3, min_train=40):
        assert max(e.decided_at for e in train) < min(e.decided_at for e in test)


def test_the_model_is_deterministic_and_learns_a_real_edge():
    ex = synthetic(400)
    tr, _, te = chronological_split(ex)
    a, b = LogisticModel().fit(tr), LogisticModel().fit(tr)
    assert a.to_dict() == b.to_dict() and a.fingerprint() == b.fingerprint()
    probs = a.predict_proba(te)
    labels = np.array([e.label for e in te])
    assert probs[labels == 1].mean() > probs[labels == 0].mean() + 0.1
    again = LogisticModel.from_dict(a.to_dict())
    assert np.allclose(again.predict_proba(te), probs)
    assert sum(b_["n"] for b_ in calibration_bins(probs, labels)) == len(te)
    with pytest.raises(ValueError):
        LogisticModel().fit([e for e in tr if e.label == 1])


def test_insufficient_samples_are_rejected_before_anything_is_registered(tmp_path):
    reg = ModelRegistry(tmp_path)
    with pytest.raises(InsufficientData):
        train_candidate(synthetic(50), strategy_id="scalp.orb", registry=reg)
    assert reg.versions() == []


def test_a_noise_model_fails_the_gates_and_stays_a_candidate(tmp_path):
    reg = ModelRegistry(tmp_path)
    entry = train_candidate(synthetic(400, edge=False), strategy_id="scalp.orb", registry=reg)
    assert entry["state"] == "candidate" and entry["gate_failures"]
    assert reg.champion() is None and reg.shadow() is None


def test_gates_shadow_promotion_and_rollback(tmp_path):
    clock = {"now": datetime(2026, 2, 1, tzinfo=timezone.utc)}
    reg = ModelRegistry(tmp_path, clock=lambda: clock["now"])
    report = {"samples": 200, "leakage": [], "brier": 0.2, "brier_base_rate": 0.25,
              "baseline": {"net": 100.0, "max_drawdown": 500.0},
              "model": {"trades": 60, "net": 900.0, "profit_factor": 1.5,
                        "expectancy_ci90": [2.0, 25.0], "max_drawdown": 300.0}}
    first = reg.register(model={"kind": "x"}, report=report, params={"threshold": 0.55},
                         dataset={}, strategy_id="scalp.orb")
    assert reg.evaluate_candidate(first["version"]) == []
    assert reg.get(first["version"])["state"] == "shadow"
    with pytest.raises(ValueError, match="days of paper"):
        reg.promote(first["version"], approved_by="admin", reason="ok")
    clock["now"] += timedelta(days=8)
    with pytest.raises(ValueError, match="human approval"):
        reg.promote(first["version"], approved_by=" ", reason="ok")
    reg.promote(first["version"], approved_by="admin@example.com", reason="beat baseline")
    assert reg.champion()["version"] == first["version"]
    # A challenger must beat the champion's test result too
    clock["now"] += timedelta(minutes=1)
    weaker = reg.register(model={"kind": "x"}, report={**report, "model": {
        **report["model"], "net": 800.0}}, params={}, dataset={}, strategy_id="scalp.orb")
    assert any("champion" in f for f in reg.evaluate_candidate(weaker["version"]))
    clock["now"] += timedelta(minutes=1)
    better = reg.register(model={"kind": "x"}, report={**report, "model": {
        **report["model"], "net": 1500.0}}, params={}, dataset={}, strategy_id="scalp.orb")
    assert reg.evaluate_candidate(better["version"]) == []
    clock["now"] += timedelta(days=8)
    reg.promote(better["version"], approved_by="admin@example.com", reason="better")
    assert reg.get(first["version"])["state"] == "retired"
    assert reg.rollback(by="admin@example.com", reason="drift") == first["version"]
    assert reg.champion()["version"] == first["version"]
    events = [h["event"] for h in reg.history()]
    assert events.count("promoted") == 2 and events[-1] == "rollback"


def test_check_gates_lists_every_failure():
    fails = check_gates({"samples": 10, "leakage": ["x"], "baseline": {"net": 5},
                         "model": {"trades": 3, "net": 1, "profit_factor": 0.8,
                                   "expectancy_ci90": [-1, 2], "max_drawdown": 9},
                         "brier": 0.3, "brier_base_rate": 0.25}, Gates())
    text = " ".join(fails)
    for needle in ("leakage", "test samples", "gated test trades", "baseline",
                   "profit factor", "CI", "Brier"):
        assert needle in text, needle


def test_learning_can_never_set_a_hard_safety_limit(tmp_path):
    for bad in ({"max_daily_loss_pct": 0.5}, {"nested": {"max_open_positions": 50}},
                {"list": [{"trading_mode": "live"}]}, {"max_consecutive_losses": 100}):
        with pytest.raises(SafetyKeyError):
            assert_no_safety_keys(bad)
    with pytest.raises(SafetyKeyError):
        ModelRegistry(tmp_path).register(model={}, report={}, params={"max_position_pct": 1},
                                         dataset={}, strategy_id="x")
    assert_no_safety_keys({"threshold": 0.6, "weights": [1, 2]})


def test_trade_stats_and_drift():
    s = trade_stats([10, -5, 10, -5, 10, -5, 10, -5, 10, -5])
    assert s.trades == 10 and s.net == 25 and s.profit_factor == 2 and s.max_drawdown == 5
    assert s.expectancy_ci90 is not None
    assert trade_stats([]).expectancy is None
    rng = np.random.default_rng(0)
    same = psi(rng.normal(size=500), rng.normal(size=500))
    shifted = psi(rng.normal(size=500), rng.normal(loc=2, size=500))
    assert same < 0.1 and shifted > 0.25
    a = [Example(T0, {"rsi": float(x)}, 1, 1.0) for x in rng.normal(size=200)]
    b = [Example(T0, {"rsi": float(x)}, 1, 1.0) for x in rng.normal(loc=3, size=200)]
    assert drift_report(a, b, ["rsi"])["alerts"] == ["rsi"]
