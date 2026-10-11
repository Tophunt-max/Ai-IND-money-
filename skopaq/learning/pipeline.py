"""Train → validate → test → register → gate, one reproducible run.

``train_candidate`` never touches the champion: it fits the baseline model on the
chronological training set, picks the gate threshold on validation, reports on the
held-out test set (and walk-forward folds), registers the candidate with its dataset
fingerprint, and runs the promotion gates (passing only reaches ``shadow``).
Too few labelled examples → ``InsufficientData``, nothing registered.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Sequence

from skopaq.learning.dataset import Example, chronological_split, walk_forward_folds
from skopaq.learning.evaluation import choose_threshold, drift_report, evaluate_gate
from skopaq.learning.model import MODEL_KINDS
from skopaq.learning.registry import Gates, ModelRegistry


class InsufficientData(ValueError):
    pass


def _fingerprint(examples: Sequence[Example]) -> str:
    h = hashlib.sha256()
    for e in sorted(examples, key=lambda e: e.decided_at):
        h.update(f"{e.decided_at.isoformat()}|{e.symbol}|{e.label}|{e.net_pnl}".encode())
    return h.hexdigest()[:16]


def train_candidate(examples: Sequence[Example], *, strategy_id: str,
                    registry: Optional[ModelRegistry] = None, gates: Gates = Gates(),
                    min_examples: int = 200, leakage: Sequence[str] = (),
                    notes: str = "", model_kind: str = "logistic") -> dict:
    labelled = [e for e in examples if e.label is not None]
    if len(labelled) < min_examples:
        raise InsufficientData(f"{len(labelled)} labelled examples < {min_examples}: "
                               "collect more paper/backtest outcomes first")
    if len({e.label for e in labelled}) < 2:
        raise InsufficientData("every example has the same outcome: nothing to learn")
    train, val, test = chronological_split(labelled)
    if len({e.label for e in train}) < 2 or not test:
        raise InsufficientData("the chronological split left a set without both outcomes")
    if model_kind not in MODEL_KINDS:
        raise ValueError(f"model_kind must be one of {sorted(MODEL_KINDS)}")
    make = MODEL_KINDS[model_kind]
    model = make().fit(train)
    threshold = choose_threshold(model, val)
    report = evaluate_gate(model, test, threshold)
    report["leakage"] = list(leakage)
    folds = []
    for tr, te in walk_forward_folds(labelled):
        if len({e.label for e in tr}) < 2:
            continue
        m = make().fit(tr)
        r = evaluate_gate(m, te, threshold)
        folds.append({"train": len(tr), "test": len(te), "baseline_net": r["baseline"]["net"],
                      "model_net": (r.get("model") or {}).get("net")})
    report["walk_forward"] = folds
    report["drift_test_vs_train"] = drift_report(train, test, model.names)
    dataset = {
        "fingerprint": _fingerprint(labelled), "labelled": len(labelled),
        "train": len(train), "validation": len(val), "test": len(test),
        "first": labelled and min(e.decided_at for e in labelled).isoformat(),
        "last": labelled and max(e.decided_at for e in labelled).isoformat(),
        "symbols": sorted({e.symbol for e in labelled}),
    }
    # A bounded sample of each training feature: the drift monitor compares live
    # features against it (skopaq/learning/monitor.py)
    step = max(1, len(train) // 300)
    dataset["feature_sample"] = {
        n: [round(e.features[n], 6) for e in train[::step]
            if isinstance(e.features.get(n), float) and e.features[n] == e.features[n]]
        for n in model.names}
    params = {"threshold": threshold}
    entry = {"model": model.to_dict(), "report": report, "params": params,
             "dataset": dataset, "strategy_id": strategy_id}
    if registry is not None:
        entry = registry.register(model=model.to_dict(), report=report, params=params,
                                  dataset=dataset, strategy_id=strategy_id, notes=notes)
        entry["gate_failures"] = registry.evaluate_candidate(entry["version"], gates)
        entry = registry.get(entry["version"]) or entry
    return entry
