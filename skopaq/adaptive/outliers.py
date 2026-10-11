"""Outlier handling, FreqAI-style: the Dissimilarity Index for live points and robust
filtering of the training set.

**Dissimilarity Index (DI)**: a live feature vector's distance to its nearest training
point (in the training set's standardised space), divided by the training set's mean
nearest-neighbour distance. DI well above 1 means the market looks like nothing the model
was trained on: its prediction is not trusted and no entry is taken on it
(``is_outlier``).

**Training filter**: rows with any feature beyond ``z_max`` robust z-scores (median /
MAD, robust to the outliers themselves) are dropped before training, optionally followed
by an Isolation Forest when scikit-learn is installed.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


def _medians(X: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(X, axis=0)
    return np.where(np.isnan(med), 0.0, med)


def _impute(X: np.ndarray, med: np.ndarray) -> np.ndarray:
    X = np.array(X, dtype=float, copy=True)
    mask = np.isnan(X)
    if mask.any():
        X[mask] = np.take(med, np.where(mask)[1])
    return X


def _pairwise(A: np.ndarray, B: Optional[np.ndarray] = None) -> np.ndarray:
    B = A if B is None else B
    aa = (A * A).sum(axis=1)[:, None]
    bb = (B * B).sum(axis=1)[None, :]
    return np.sqrt(np.maximum(aa + bb - 2 * A @ B.T, 0.0))


@dataclass
class DissimilarityIndex:
    medians: list[float] = field(default_factory=list)   # imputation
    mean: list[float] = field(default_factory=list)      # standardisation centre
    scale: list[float] = field(default_factory=list)
    reference: list[list[float]] = field(default_factory=list)   # standardised sample
    mean_nn: float = 1.0
    threshold: float = 1.5

    @classmethod
    def fit(cls, X: np.ndarray, *, threshold: float = 1.5, max_points: int = 1500,
            seed: int = 7) -> "DissimilarityIndex":
        X = np.asarray(X, float)
        if X.ndim != 2 or len(X) < 3:
            raise ValueError("need at least 3 training rows for the dissimilarity index")
        med = _medians(X)
        Xi = _impute(X, med)
        mu = Xi.mean(axis=0)
        sd = Xi.std(axis=0)
        sd = np.where(sd < 1e-12, 1.0, sd)
        Z = (Xi - mu) / sd
        if len(Z) > max_points:              # fixed seed: reproducible reference sample
            idx = np.random.default_rng(seed).choice(len(Z), max_points, replace=False)
            Z = Z[np.sort(idx)]
        d = _pairwise(Z)
        np.fill_diagonal(d, np.inf)
        mean_nn = float(np.mean(d.min(axis=1)))
        return cls(medians=med.tolist(), mean=mu.tolist(), scale=sd.tolist(),
                   reference=Z.round(5).tolist(), mean_nn=mean_nn if mean_nn > 0 else 1.0,
                   threshold=threshold)

    def score(self, X: np.ndarray) -> np.ndarray:
        """DI of each row of ``X``."""
        Xi = _impute(np.atleast_2d(np.asarray(X, float)), np.array(self.medians))
        Z = (Xi - np.array(self.mean)) / np.array(self.scale)
        d = _pairwise(Z, np.asarray(self.reference, float))
        return d.min(axis=1) / self.mean_nn

    def is_outlier(self, x: Sequence[float]) -> tuple[bool, float]:
        di = float(self.score(np.asarray([x], float))[0])
        return di > self.threshold, di

    def to_dict(self) -> dict:
        return {"medians": self.medians, "mean": self.mean, "scale": self.scale,
                "reference": self.reference, "mean_nn": self.mean_nn,
                "threshold": self.threshold}

    @classmethod
    def from_dict(cls, d: dict) -> "DissimilarityIndex":
        return cls(medians=list(d["medians"]), mean=list(d["mean"]), scale=list(d["scale"]),
                   reference=[list(r) for r in d["reference"]], mean_nn=float(d["mean_nn"]),
                   threshold=float(d["threshold"]))


def filter_training(X: np.ndarray, *, z_max: float = 6.0, isolation_forest: bool = False,
                    seed: int = 7) -> np.ndarray:
    """Boolean mask of the rows kept for training."""
    X = np.asarray(X, float)
    med = _medians(X)
    Xi = _impute(X, med)
    mad = np.median(np.abs(Xi - med), axis=0) * 1.4826
    mad = np.where(mad < 1e-12, np.inf, mad)        # a constant column never drops a row
    keep = (np.abs((Xi - med) / mad) <= z_max).all(axis=1)
    if isolation_forest and keep.sum() > 50:
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            return keep
        iso = IsolationForest(random_state=seed, contamination="auto").fit(Xi[keep])
        idx = np.where(keep)[0]
        keep = np.zeros(len(X), bool)
        keep[idx[iso.predict(Xi[idx]) == 1]] = True
    return keep
