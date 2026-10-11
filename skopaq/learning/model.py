"""A reproducible baseline model: L2-regularised logistic regression in NumPy.

Deterministic (no random state): the same examples give the same weights. Missing
features are imputed with the training median and standardised with training statistics
only, so validation/test data never shape the preprocessing. It predicts the probability
that a QUALIFIED setup ends with a positive *net* P&L.

Scikit-learn, XGBoost or LightGBM can replace it behind the same ``fit`` /
``predict_proba`` / ``to_dict`` interface once a baseline has shown there is anything to
learn; they are not dependencies today.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from skopaq.learning.dataset import FEATURE_NAMES, Example


def _matrix(examples: Sequence[Example], names: Sequence[str]) -> np.ndarray:
    return np.array([[e.features.get(n, math.nan) for n in names] for e in examples],
                    dtype=float).reshape(len(examples), len(names))


@dataclass
class LogisticModel:
    names: tuple[str, ...] = FEATURE_NAMES
    l2: float = 1.0
    iterations: int = 200
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    medians: list[float] = field(default_factory=list)
    means: list[float] = field(default_factory=list)
    stds: list[float] = field(default_factory=list)
    trained_on: int = 0
    kind: str = "logistic-numpy-v1"

    def _prep(self, X: np.ndarray) -> np.ndarray:
        X = X.copy()
        med = np.array(self.medians)
        mask = np.isnan(X)
        X[mask] = np.take(med, np.where(mask)[1])
        return (X - np.array(self.means)) / np.array(self.stds)

    def fit(self, examples: Sequence[Example]) -> "LogisticModel":
        rows = [e for e in examples if e.label is not None]
        if len(rows) < 2 or len({e.label for e in rows}) < 2:
            raise ValueError("need labelled examples of both classes to train")
        X = _matrix(rows, self.names)
        y = np.array([e.label for e in rows], dtype=float)
        with warnings.catch_warnings():        # an all-missing column: median 0
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(X, axis=0)
        med = np.where(np.isnan(med), 0.0, med)
        self.medians = med.tolist()
        Xi = X.copy()
        mask = np.isnan(Xi)
        Xi[mask] = np.take(med, np.where(mask)[1])
        mu, sd = Xi.mean(axis=0), Xi.std(axis=0)
        sd = np.where(sd < 1e-12, 1.0, sd)
        self.means, self.stds = mu.tolist(), sd.tolist()
        Z = (Xi - mu) / sd
        n, d = Z.shape
        w, b = np.zeros(d), math.log((y.mean() + 1e-9) / (1 - y.mean() + 1e-9))
        lam = self.l2 / n
        for _ in range(self.iterations):            # Newton-Raphson (IRLS)
            p = 1 / (1 + np.exp(-(Z @ w + b)))
            g_w = Z.T @ (p - y) / n + lam * w
            g_b = float(np.mean(p - y))
            s = p * (1 - p)
            H = (Z.T * s) @ Z / n + lam * np.eye(d)
            hb = float(np.mean(s)) + 1e-12
            hwb = Z.T @ s / n
            full = np.block([[H, hwb[:, None]], [hwb[None, :], np.array([[hb]])]])
            step = np.linalg.solve(full + 1e-9 * np.eye(d + 1), np.append(g_w, g_b))
            w, b = w - step[:d], b - step[d]
            if float(np.max(np.abs(step))) < 1e-8:
                break
        self.weights, self.bias, self.trained_on = w.tolist(), float(b), n
        return self

    def predict_proba(self, examples: Sequence[Example]) -> np.ndarray:
        if not self.weights:
            raise ValueError("model is not trained")
        if not examples:
            return np.zeros(0)
        Z = self._prep(_matrix(examples, self.names))
        return 1 / (1 + np.exp(-(Z @ np.array(self.weights) + self.bias)))

    def predict_one(self, features: dict[str, float]) -> float:
        return float(self.predict_proba([Example(decided_at=None, features=features,  # type: ignore[arg-type]
                                                 label=None, net_pnl=None)])[0])

    def to_dict(self) -> dict:
        return {"kind": self.kind, "names": list(self.names), "l2": self.l2,
                "iterations": self.iterations, "weights": self.weights, "bias": self.bias,
                "medians": self.medians, "means": self.means, "stds": self.stds,
                "trained_on": self.trained_on}

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticModel":
        if d.get("kind") != "logistic-numpy-v1":
            raise ValueError(f"unknown model kind {d.get('kind')!r}")
        return cls(names=tuple(d["names"]), l2=d["l2"], iterations=d["iterations"],
                   weights=list(d["weights"]), bias=float(d["bias"]),
                   medians=list(d["medians"]), means=list(d["means"]),
                   stds=list(d["stds"]), trained_on=int(d["trained_on"]))

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()
                              ).hexdigest()[:12]


@dataclass
class GBMModel:
    """Optional LightGBM candidate (``pip install skopaqtrader[ml]``), deterministic
    (fixed seed, one thread). Missing features stay NaN (LightGBM routes them). Saved as
    LightGBM's text model: no pickle is ever loaded."""

    names: tuple[str, ...] = FEATURE_NAMES
    params: dict = field(default_factory=lambda: {
        "objective": "binary", "learning_rate": 0.05, "num_leaves": 8,
        "min_data_in_leaf": 20, "feature_fraction": 0.8, "bagging_fraction": 0.8,
        "bagging_freq": 1, "lambda_l2": 1.0, "seed": 7, "deterministic": True,
        "num_threads": 1, "verbose": -1})
    rounds: int = 150
    text: str = ""
    trained_on: int = 0
    kind: str = "lightgbm-v1"
    _booster: object = None

    def fit(self, examples: Sequence[Example]) -> "GBMModel":
        import lightgbm as lgb

        rows = [e for e in examples if e.label is not None]
        if len(rows) < 2 or len({e.label for e in rows}) < 2:
            raise ValueError("need labelled examples of both classes to train")
        X = _matrix(rows, self.names)
        y = np.array([e.label for e in rows], dtype=float)
        self._booster = lgb.train(self.params, lgb.Dataset(X, y, feature_name=list(
            self.names)), num_boost_round=self.rounds)
        self.text = self._booster.model_to_string()
        self.trained_on = len(rows)
        return self

    def _model(self):
        if self._booster is None:
            if not self.text:
                raise ValueError("model is not trained")
            import lightgbm as lgb

            self._booster = lgb.Booster(model_str=self.text)
        return self._booster

    def predict_proba(self, examples: Sequence[Example]) -> np.ndarray:
        if not examples:
            return np.zeros(0)
        return np.asarray(self._model().predict(_matrix(examples, self.names)), float)

    def predict_one(self, features: dict[str, float]) -> float:
        return float(self.predict_proba([Example(decided_at=None, features=features,  # type: ignore[arg-type]
                                                 label=None, net_pnl=None)])[0])

    def to_dict(self) -> dict:
        return {"kind": self.kind, "names": list(self.names), "params": self.params,
                "rounds": self.rounds, "text": self.text, "trained_on": self.trained_on}

    @classmethod
    def from_dict(cls, d: dict) -> "GBMModel":
        return cls(names=tuple(d["names"]), params=dict(d["params"]), rounds=int(d["rounds"]),
                   text=d["text"], trained_on=int(d["trained_on"]))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]


MODEL_KINDS = {"logistic": LogisticModel, "lightgbm": GBMModel}


def load_model(d: dict):
    """A stored model of any kind (``to_dict`` output)."""
    kind = d.get("kind")
    if kind == "logistic-numpy-v1":
        return LogisticModel.from_dict(d)
    if kind == "lightgbm-v1":
        return GBMModel.from_dict(d)
    raise ValueError(f"unknown model kind {kind!r}")


def brier(probs: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    if not len(labels):
        return None
    p, y = np.asarray(probs, float), np.asarray(labels, float)
    return float(np.mean((p - y) ** 2))


def calibration_bins(probs: Sequence[float], labels: Sequence[int], bins: int = 5) -> list[dict]:
    """Reliability table: predicted vs observed win rate per probability bin."""
    out = []
    p, y = np.asarray(probs, float), np.asarray(labels, float)
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges, edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            out.append({"lo": round(float(lo), 2), "hi": round(float(hi), 2),
                        "n": int(m.sum()), "predicted": round(float(p[m].mean()), 4),
                        "observed": round(float(y[m].mean()), 4)})
    return out
