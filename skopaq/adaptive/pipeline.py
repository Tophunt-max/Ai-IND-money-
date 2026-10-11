"""The data pipeline (FreqAI's ``define_data_pipeline``), pickle-free.

Fitted on the training features only, applied unchanged to validation, test and live
rows, in this order:

1. ``VarianceThreshold`` — drop constant columns
2. scaler — ``minmax`` (-1..1, FreqAI's default), ``standard`` or ``robust``
3. ``PCA`` (optional) — keep components explaining ``pca_variance`` of the variance, then
   a second min-max scaler
4. outlier detectors (each optional), fitted on the transformed training rows:
   * **SVM** — a one-class SVM (RBF); training outliers are removed, live rows flagged
   * **DI** — the Dissimilarity Index; live rows above ``di_threshold`` flagged
   * **DBSCAN** — density clustering; training noise points removed, a live row is an
     outlier when it is farther than ``eps`` from every core point
5. ``noise`` (optional) — Gaussian noise of ``noise_std`` added to the *training* rows only

Every fitted parameter is a plain array, so the pipeline saves as JSON and reloads without
pickle (the SVM's decision function and DBSCAN's membership test are re-implemented in
NumPy from the fitted support / core points).
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class PipelineConfig:
    scaler: str = "minmax"                 # minmax | standard | robust | none
    pca: bool = False
    pca_variance: float = 0.999
    svm: bool = False
    svm_nu: float = 0.01
    di_threshold: float = 0.0              # 0 = off (FreqAI's convention)
    dbscan: bool = False
    dbscan_min_samples_frac: float = 0.25  # of sqrt(n), as datasieve picks it
    noise_std: float = 0.0
    seed: int = 7
    max_reference: int = 2000              # DI / DBSCAN reference points kept

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PipelineConfig":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def _pairwise(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    aa = (A * A).sum(axis=1)[:, None]
    bb = (B * B).sum(axis=1)[None, :]
    return np.sqrt(np.maximum(aa + bb - 2 * A @ B.T, 0.0))


def _nearest(A: np.ndarray, B: np.ndarray, chunk: int = 2048) -> np.ndarray:
    """Distance from each row of A to its nearest row of B (memory-bounded)."""
    out = np.empty(len(A))
    for i in range(0, len(A), chunk):
        out[i:i + chunk] = _pairwise(A[i:i + chunk], B).min(axis=1)
    return out


@dataclass
class Pipeline:
    config: PipelineConfig = field(default_factory=PipelineConfig)
    columns: list[str] = field(default_factory=list)      # input columns, in order
    keep: list[int] = field(default_factory=list)          # after the variance threshold
    s_a: list[float] = field(default_factory=list)         # scaler: x' = (x - a) / b (+ c)
    s_b: list[float] = field(default_factory=list)
    minmax: bool = True
    pca_mean: list[float] = field(default_factory=list)
    pca_components: list[list[float]] = field(default_factory=list)
    p_a: list[float] = field(default_factory=list)          # post-PCA min-max
    p_b: list[float] = field(default_factory=list)
    svm: Optional[dict] = None
    di: Optional[dict] = None
    dbscan: Optional[dict] = None
    fitted_rows: int = 0
    removed: dict = field(default_factory=dict)

    # ── transforms ──────────────────────────────────────────────────────

    def _scale(self, X: np.ndarray) -> np.ndarray:
        X = X[:, self.keep]
        if self.s_a:
            X = (X - np.array(self.s_a)) / np.array(self.s_b)
            if self.minmax:
                X = X * 2 - 1
        if self.pca_components:
            X = (X - np.array(self.pca_mean)) @ np.array(self.pca_components).T
            X = (X - np.array(self.p_a)) / np.array(self.p_b) * 2 - 1
        return X

    def transform(self, X: np.ndarray) -> np.ndarray:
        return self._scale(np.asarray(X, float))

    # ── outliers on transformed rows ────────────────────────────────────

    def svm_scores(self, Z: np.ndarray) -> np.ndarray:
        """One-class SVM decision function (negative = outlier)."""
        s = self.svm
        k = np.exp(-s["gamma"] * _pairwise(Z, np.array(s["sv"])) ** 2)
        return k @ np.array(s["dual"]) + s["intercept"]

    def di_values(self, Z: np.ndarray) -> np.ndarray:
        d = self.di
        return _nearest(Z, np.array(d["reference"])) / d["mean_nn"]

    def dbscan_outlier(self, Z: np.ndarray) -> np.ndarray:
        d = self.dbscan
        return _nearest(Z, np.array(d["core"])) > d["eps"]

    def outliers(self, Z: np.ndarray) -> dict[str, np.ndarray]:
        """Per-detector flags and the DI values of transformed rows."""
        n = len(Z)
        out = {"svm": np.zeros(n, bool), "di": np.zeros(n, bool), "dbscan": np.zeros(n, bool),
               "di_values": np.full(n, np.nan)}
        if self.svm:
            out["svm"] = self.svm_scores(Z) < 0
        if self.di:
            v = self.di_values(Z)
            out["di_values"] = v
            out["di"] = v > self.di["threshold"]
        if self.dbscan:
            out["dbscan"] = self.dbscan_outlier(Z)
        out["any"] = out["svm"] | out["di"] | out["dbscan"]
        return out

    # ── fit ─────────────────────────────────────────────────────────────

    def fit_transform(self, X: np.ndarray, columns: list[str], *extra: np.ndarray
                      ) -> tuple[np.ndarray, list[np.ndarray]]:
        """Fit on training rows; returns the transformed rows that survive the outlier
        removal (with noise added) and the matching rows of each ``extra`` array."""
        c = self.config
        X = np.asarray(X, float)
        self.columns = list(columns)
        var = X.var(axis=0)
        self.keep = [i for i, v in enumerate(var) if np.isfinite(v) and v > 0]
        Xk = X[:, self.keep]
        if c.scaler == "minmax":
            lo, hi = Xk.min(axis=0), Xk.max(axis=0)
            self.s_a, self.s_b, self.minmax = lo.tolist(), np.where(hi - lo > 0, hi - lo,
                                                                   1.0).tolist(), True
        elif c.scaler == "standard":
            sd = Xk.std(axis=0)
            self.s_a, self.s_b, self.minmax = Xk.mean(axis=0).tolist(), np.where(
                sd > 0, sd, 1.0).tolist(), False
        elif c.scaler == "robust":
            q1, med, q3 = np.percentile(Xk, [25, 50, 75], axis=0)
            iqr = q3 - q1
            self.s_a, self.s_b, self.minmax = med.tolist(), np.where(iqr > 0, iqr,
                                                                    1.0).tolist(), False
        else:
            self.s_a, self.s_b = [], []
        Z = Xk
        if self.s_a:
            Z = (Z - np.array(self.s_a)) / np.array(self.s_b)
            if self.minmax:
                Z = Z * 2 - 1
        if c.pca and Z.shape[1] > 1:
            mu = Z.mean(axis=0)
            _, sv, vt = np.linalg.svd(Z - mu, full_matrices=False)
            ratio = np.cumsum(sv ** 2) / max(float((sv ** 2).sum()), 1e-12)
            k = int(np.searchsorted(ratio, c.pca_variance) + 1)
            comps = vt[:k]
            P = (Z - mu) @ comps.T
            lo, hi = P.min(axis=0), P.max(axis=0)
            self.pca_mean, self.pca_components = mu.tolist(), comps.tolist()
            self.p_a, self.p_b = lo.tolist(), np.where(hi - lo > 0, hi - lo, 1.0).tolist()
            Z = (P - lo) / np.array(self.p_b) * 2 - 1
        rng = np.random.default_rng(c.seed)
        mask = np.ones(len(Z), bool)
        if c.svm and len(Z) > 20:
            from sklearn.svm import OneClassSVM

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sample = Z if len(Z) <= 5000 else Z[np.sort(rng.choice(len(Z), 5000, False))]
                m = OneClassSVM(nu=c.svm_nu, kernel="rbf", gamma="scale").fit(sample)
            self.svm = {"sv": m.support_vectors_.tolist(), "dual": m.dual_coef_[0].tolist(),
                        "intercept": float(m.intercept_[0]), "gamma": float(m._gamma)}
            bad = self.svm_scores(Z) < 0
            self.removed["svm"] = int(bad.sum())
            mask &= ~bad
        if c.dbscan and mask.sum() > 20:
            from sklearn.cluster import DBSCAN

            Zm = Z[mask]
            ref = Zm if len(Zm) <= c.max_reference else Zm[np.sort(
                rng.choice(len(Zm), c.max_reference, False))]
            nn = _pairwise(ref, ref)
            np.fill_diagonal(nn, np.inf)
            min_samples = max(2, int(c.dbscan_min_samples_frac * np.sqrt(len(ref))))
            kth = np.sort(nn, axis=1)[:, min(min_samples, len(ref) - 1) - 1]
            eps = float(np.percentile(kth, 95))
            db = DBSCAN(eps=eps, min_samples=min_samples).fit(ref)
            core = ref[db.core_sample_indices_] if len(db.core_sample_indices_) else ref
            self.dbscan = {"core": core.round(6).tolist(), "eps": eps,
                           "min_samples": min_samples}
            bad = np.zeros(len(Z), bool)
            idx = np.where(mask)[0]
            bad[idx] = self.dbscan_outlier(Z[idx])
            self.removed["dbscan"] = int(bad.sum())
            mask &= ~bad
        if c.di_threshold > 0 and mask.sum() > 3:
            Zm = Z[mask]
            ref = Zm if len(Zm) <= c.max_reference else Zm[np.sort(
                rng.choice(len(Zm), c.max_reference, False))]
            d = _pairwise(ref, ref)
            np.fill_diagonal(d, np.inf)
            mean_nn = float(d.min(axis=1).mean()) or 1.0
            self.di = {"reference": ref.round(6).tolist(), "mean_nn": mean_nn,
                       "threshold": float(c.di_threshold)}
        Zk = Z[mask]
        if c.noise_std > 0:
            Zk = Zk + rng.normal(0.0, c.noise_std, Zk.shape)
        self.fitted_rows = int(mask.sum())
        return Zk, [np.asarray(e)[mask] for e in extra]

    def apply_frozen(self, X: np.ndarray, *extra: np.ndarray,
                     noise_std: float = 0.0, seed: int = 7
                     ) -> tuple[np.ndarray, list[np.ndarray]]:
        """Continual learning: transform new training rows with this *already fitted*
        pipeline (the continued model keeps the input space it learned), drop the rows its
        detectors flag, add training noise. Nothing is refitted."""
        Z = self.transform(np.asarray(X, float))
        mask = ~self.outliers(Z)["any"]
        Zk = Z[mask]
        if noise_std > 0:
            Zk = Zk + np.random.default_rng(seed).normal(0.0, noise_std, Zk.shape)
        self.fitted_rows = int(mask.sum())
        return Zk, [np.asarray(e)[mask] for e in extra]

    # ── storage ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "config"}
        d["config"] = self.config.as_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Pipeline":
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "config"}
        return cls(config=PipelineConfig.from_dict(d.get("config") or {}), **kw)

    def describe(self) -> dict[str, Any]:
        return {"input_columns": len(self.columns), "kept": len(self.keep),
                "pca_components": len(self.pca_components), "svm": bool(self.svm),
                "di": bool(self.di), "dbscan": bool(self.dbscan),
                "fitted_rows": self.fitted_rows, "removed": self.removed}
