"""Model library for the adaptive engine: classifiers and regressors on pipeline-
transformed arrays, with sample weights (``weight_factor``), early stopping on the
validation set, and pickle-free storage in each library's own format.

========================  ======================  =====================================
kind                      library                 saved as
========================  ======================  =====================================
lightgbm                  LightGBM                ``lightgbm.txt`` (text model)
xgboost / xgboost_rf      XGBoost                 ``xgboost.json``
catboost                  CatBoost                ``catboost.cbm`` (CatBoost binary)
random_forest             scikit-learn            ``trees.npz`` (arrays, no pickle)
extra_trees               scikit-learn            ``trees.npz``
linear                    NumPy (logistic/ridge)  ``linear.json``
mlp                       PyTorch                 ``torch.safetensors``
ft_transformer            PyTorch (feature-token  ``torch.safetensors``
                          transformer)
========================  ======================  =====================================

Every model is deterministic for a given seed (one thread where the library allows it).
``task`` is ``classifier`` (probability of class 1) or ``regressor`` (a value).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np

# joblib (scikit-learn) cannot count cores in some containers; the models use one thread
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

KINDS = ("lightgbm", "xgboost", "xgboost_rf", "catboost", "random_forest", "extra_trees",
         "linear", "mlp", "ft_transformer")
_LIB = {"lightgbm": "lightgbm", "xgboost": "xgboost", "xgboost_rf": "xgboost",
        "catboost": "catboost", "random_forest": "sklearn", "extra_trees": "sklearn",
        "mlp": "torch", "ft_transformer": "torch", "linear": None}


def available(kind: str) -> bool:
    lib = _LIB.get(kind)
    if lib is None:
        return kind == "linear"
    try:
        __import__(lib)
        return True
    except ImportError:
        return False


def library_versions() -> dict[str, str]:
    out = {"numpy": np.__version__}
    for lib in ("lightgbm", "xgboost", "catboost", "sklearn", "torch", "safetensors"):
        try:
            out[lib] = __import__(lib).__version__
        except Exception:
            pass
    return out


class BaseModel:
    kind = ""

    def __init__(self, task: str = "classifier", params: Optional[dict] = None,
                 seed: int = 7) -> None:
        if task not in ("classifier", "regressor"):
            raise ValueError("task must be classifier or regressor")
        self.task, self.params, self.seed = task, dict(params or {}), seed
        self.best_iteration: Optional[int] = None
        self.importance: dict[int, float] = {}

    def fit(self, X, y, w=None, X_val=None, y_val=None) -> "BaseModel":
        raise NotImplementedError

    def predict(self, X) -> np.ndarray:
        raise NotImplementedError

    def save(self, d: Path) -> list[str]:
        raise NotImplementedError

    def load(self, d: Path) -> "BaseModel":
        raise NotImplementedError

    def meta(self) -> dict:
        return {"kind": self.kind, "task": self.task, "params": self.params,
                "seed": self.seed, "best_iteration": self.best_iteration}


# ── gradient boosting ────────────────────────────────────────────────────────


class LightGBMModel(BaseModel):
    kind = "lightgbm"

    def fit(self, X, y, w=None, X_val=None, y_val=None):
        import lightgbm as lgb

        p = {"objective": "binary" if self.task == "classifier" else "regression",
             "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 20,
             "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
             "lambda_l2": 1.0, "seed": self.seed, "deterministic": True, "num_threads": 1,
             "verbose": -1, **self.params}
        rounds = int(p.pop("rounds", 400))
        train = lgb.Dataset(X, y, weight=w)
        sets, cbs = [train], []
        if X_val is not None and len(X_val):
            sets.append(lgb.Dataset(X_val, y_val, reference=train))
            cbs.append(lgb.early_stopping(30, verbose=False))
        self._b = lgb.train(p, train, num_boost_round=rounds, valid_sets=sets, callbacks=cbs)
        self.best_iteration = int(self._b.best_iteration or rounds)
        gain = self._b.feature_importance("gain")
        self.importance = {i: float(v) for i, v in enumerate(gain) if v > 0}
        return self

    def predict(self, X):
        return np.asarray(self._b.predict(X, num_iteration=self.best_iteration), float)

    def save(self, d):
        self._b.save_model(str(d / "lightgbm.txt"), num_iteration=self.best_iteration)
        return ["lightgbm.txt"]

    def load(self, d):
        import lightgbm as lgb

        self._b = lgb.Booster(model_file=str(d / "lightgbm.txt"))
        self.best_iteration = None          # the saved model has only the best trees
        return self


class XGBoostModel(BaseModel):
    kind = "xgboost"
    random_forest = False

    def fit(self, X, y, w=None, X_val=None, y_val=None):
        import xgboost as xgb

        p = {"objective": "binary:logistic" if self.task == "classifier" else "reg:squarederror",
             "eta": 0.05, "max_depth": 4, "subsample": 0.8, "colsample_bytree": 0.8,
             "lambda": 1.0, "min_child_weight": 5, "seed": self.seed, "nthread": 1,
             "tree_method": "exact", **self.params}
        rounds = int(p.pop("rounds", 400))
        if self.random_forest:
            # Column sampling per node needs the histogram method (still deterministic)
            p.update(num_parallel_tree=int(p.get("num_parallel_tree", 100)), eta=1.0,
                     subsample=0.8, colsample_bynode=0.8, tree_method="hist")
            rounds = 1
        dtr = xgb.DMatrix(X, label=y, weight=w)
        evals, es = [], None
        if X_val is not None and len(X_val) and not self.random_forest:
            evals, es = [(xgb.DMatrix(X_val, label=y_val), "val")], 30
        self._b = xgb.train(p, dtr, num_boost_round=rounds, evals=evals,
                            early_stopping_rounds=es, verbose_eval=False)
        self.best_iteration = getattr(self._b, "best_iteration", None)
        score = self._b.get_score(importance_type="gain")
        self.importance = {int(k[1:]): float(v) for k, v in score.items() if k.startswith("f")}
        return self

    def _range(self):
        return (0, self.best_iteration + 1) if self.best_iteration is not None else (0, 0)

    def predict(self, X):
        import xgboost as xgb

        return np.asarray(self._b.predict(xgb.DMatrix(X), iteration_range=self._range()), float)

    def save(self, d):
        self._b.save_model(str(d / "xgboost.json"))
        (d / "xgboost_meta.json").write_text(json.dumps({"best_iteration": self.best_iteration}))
        return ["xgboost.json", "xgboost_meta.json"]

    def load(self, d):
        import xgboost as xgb

        self._b = xgb.Booster()
        self._b.load_model(str(d / "xgboost.json"))
        self.best_iteration = json.loads((d / "xgboost_meta.json").read_text())["best_iteration"]
        return self


class XGBoostRFModel(XGBoostModel):
    kind = "xgboost_rf"
    random_forest = True


class CatBoostModel(BaseModel):
    kind = "catboost"

    def fit(self, X, y, w=None, X_val=None, y_val=None):
        from catboost import CatBoostClassifier, CatBoostRegressor

        p = {"iterations": 500, "depth": 5, "learning_rate": 0.05, "l2_leaf_reg": 3.0,
             "random_seed": self.seed, "thread_count": 1, "verbose": False,
             "allow_writing_files": False, **self.params}
        cls = CatBoostClassifier if self.task == "classifier" else CatBoostRegressor
        self._m = cls(**p)
        kw = {}
        if X_val is not None and len(X_val):
            kw = {"eval_set": (X_val, y_val), "early_stopping_rounds": 30}
        self._m.fit(X, y, sample_weight=w, **kw)
        self.best_iteration = int(self._m.get_best_iteration() or p["iterations"])
        imp = self._m.get_feature_importance()
        self.importance = {i: float(v) for i, v in enumerate(imp) if v > 0}
        return self

    def predict(self, X):
        if self.task == "classifier":
            return np.asarray(self._m.predict_proba(X)[:, 1], float)
        return np.asarray(self._m.predict(X), float)

    def save(self, d):
        self._m.save_model(str(d / "catboost.cbm"), format="cbm")
        return ["catboost.cbm"]

    def load(self, d):
        from catboost import CatBoostClassifier, CatBoostRegressor

        self._m = (CatBoostClassifier if self.task == "classifier" else CatBoostRegressor)()
        self._m.load_model(str(d / "catboost.cbm"), format="cbm")
        return self


# ── scikit-learn forests, exported as arrays ─────────────────────────────────


class ForestModel(BaseModel):
    kind = "random_forest"
    extra = False

    def fit(self, X, y, w=None, X_val=None, y_val=None):
        from sklearn import ensemble

        name = ("ExtraTrees" if self.extra else "RandomForest") + (
            "Classifier" if self.task == "classifier" else "Regressor")
        p = {"n_estimators": 200, "max_depth": 8, "min_samples_leaf": 20,
             "random_state": self.seed, "n_jobs": 1, **self.params}
        m = getattr(ensemble, name)(**p).fit(X, y, sample_weight=w)
        self.importance = {i: float(v) for i, v in enumerate(m.feature_importances_) if v > 0}
        self._export(m)
        return self

    def _export(self, m) -> None:
        trees = []
        for est in m.estimators_:
            t = est.tree_
            v = t.value[:, 0, :]
            if self.task == "classifier":
                v = v / np.maximum(v.sum(axis=1, keepdims=True), 1e-12)
                leaf = v[:, -1] if v.shape[1] > 1 else np.zeros(len(v))
            else:
                leaf = v[:, 0]
            trees.append((t.children_left.astype(np.int32), t.children_right.astype(np.int32),
                          t.feature.astype(np.int32), t.threshold.astype(np.float64),
                          leaf.astype(np.float64)))
        self._trees = trees

    def predict(self, X):
        X = np.asarray(X, float)
        out = np.zeros(len(X))
        for left, right, feat, thr, leaf in self._trees:
            node = np.zeros(len(X), dtype=np.int64)
            active = left[node] != -1
            while active.any():
                idx = np.where(active)[0]
                f = feat[node[idx]]
                go_left = X[idx, f] <= thr[node[idx]]
                node[idx] = np.where(go_left, left[node[idx]], right[node[idx]])
                active = left[node] != -1
            out += leaf[node]
        return out / max(1, len(self._trees))

    def save(self, d):
        arrays = {}
        for i, (l_, r, f, t, v) in enumerate(self._trees):
            arrays.update({f"l{i}": l_, f"r{i}": r, f"f{i}": f, f"t{i}": t, f"v{i}": v})
        np.savez_compressed(d / "trees.npz", **arrays)
        return ["trees.npz"]

    def load(self, d):
        z = np.load(d / "trees.npz", allow_pickle=False)
        n = len([k for k in z.files if k.startswith("l")])
        self._trees = [(z[f"l{i}"], z[f"r{i}"], z[f"f{i}"], z[f"t{i}"], z[f"v{i}"])
                       for i in range(n)]
        return self


class ExtraTreesModel(ForestModel):
    kind = "extra_trees"
    extra = True


# ── NumPy linear baseline ────────────────────────────────────────────────────


class LinearModel(BaseModel):
    """Weighted L2 logistic regression (classifier) or ridge regression (regressor)."""

    kind = "linear"

    def fit(self, X, y, w=None, X_val=None, y_val=None):
        X, y = np.asarray(X, float), np.asarray(y, float)
        w = np.ones(len(y)) if w is None else np.asarray(w, float)
        lam = float(self.params.get("l2", 1.0)) / len(y)
        A = np.hstack([X, np.ones((len(X), 1))])
        reg = lam * np.eye(A.shape[1])
        reg[-1, -1] = 0.0
        if self.task == "regressor":
            self.coef = np.linalg.solve(A.T @ (A * w[:, None]) / w.sum() + reg,
                                        A.T @ (w * y) / w.sum())
        else:
            beta = np.zeros(A.shape[1])
            for _ in range(100):
                p = 1 / (1 + np.exp(-A @ beta))
                g = A.T @ (w * (p - y)) / w.sum() + reg @ beta
                H = (A * (w * p * (1 - p))[:, None]).T @ A / w.sum() + reg + 1e-9 * np.eye(
                    A.shape[1])
                step = np.linalg.solve(H, g)
                beta -= step
                if np.max(np.abs(step)) < 1e-8:
                    break
            self.coef = beta
        self.importance = {i: float(abs(v)) for i, v in enumerate(self.coef[:-1]) if v}
        return self

    def predict(self, X):
        z = np.hstack([np.asarray(X, float), np.ones((len(X), 1))]) @ self.coef
        return 1 / (1 + np.exp(-z)) if self.task == "classifier" else z

    def save(self, d):
        (d / "linear.json").write_text(json.dumps({"coef": self.coef.tolist()}))
        return ["linear.json"]

    def load(self, d):
        self.coef = np.array(json.loads((d / "linear.json").read_text())["coef"])
        return self


# ── PyTorch ──────────────────────────────────────────────────────────────────


class _TorchModel(BaseModel):
    def _net(self, n_in: int):
        raise NotImplementedError

    def fit(self, X, y, w=None, X_val=None, y_val=None):
        import torch

        torch.manual_seed(self.seed)
        torch.set_num_threads(int(self.params.get("threads", 1)))
        X = torch.tensor(np.asarray(X, np.float32))
        y = torch.tensor(np.asarray(y, np.float32))
        w = torch.ones(len(y)) if w is None else torch.tensor(np.asarray(w, np.float32))
        self.n_in = X.shape[1]
        self._m = self._net(self.n_in)
        opt = torch.optim.AdamW(self._m.parameters(), lr=float(self.params.get("lr", 1e-3)),
                                weight_decay=float(self.params.get("weight_decay", 1e-4)))
        epochs = int(self.params.get("epochs", 60))
        batch = int(self.params.get("batch_size", 256))
        best, best_state, patience = np.inf, None, 0
        gen = torch.Generator().manual_seed(self.seed)
        Xv = torch.tensor(np.asarray(X_val, np.float32)) if X_val is not None and len(X_val) \
            else None
        yv = torch.tensor(np.asarray(y_val, np.float32)) if Xv is not None else None
        for epoch in range(epochs):
            self._m.train()
            perm = torch.randperm(len(X), generator=gen)
            for i in range(0, len(X), batch):
                idx = perm[i:i + batch]
                out = self._m(X[idx]).squeeze(-1)
                loss = (self._loss(out, y[idx]) * w[idx]).sum() / w[idx].sum()
                opt.zero_grad()
                loss.backward()
                opt.step()
            if Xv is not None:
                self._m.eval()
                with torch.no_grad():
                    v = float(self._loss(self._m(Xv).squeeze(-1), yv).mean())
                if v < best - 1e-6:
                    best, patience = v, 0
                    best_state = {k: t.clone() for k, t in self._m.state_dict().items()}
                    self.best_iteration = epoch + 1
                else:
                    patience += 1
                    if patience >= int(self.params.get("patience", 8)):
                        break
        if best_state is not None:
            self._m.load_state_dict(best_state)
        self._m.eval()
        # Importance: mean |d output / d input| over (up to) 1000 training rows
        sample = X[:1000].clone().requires_grad_(True)
        self._m(sample).sum().backward()
        sal = sample.grad.abs().mean(dim=0).numpy()
        self.importance = {i: float(v) for i, v in enumerate(sal) if v > 0}
        return self

    def _loss(self, out, y):
        import torch.nn.functional as F

        if self.task == "classifier":
            return F.binary_cross_entropy_with_logits(out, y, reduction="none")
        return F.mse_loss(out, y, reduction="none")

    def predict(self, X):
        import torch

        with torch.no_grad():
            out = self._m(torch.tensor(np.asarray(X, np.float32))).squeeze(-1)
            if self.task == "classifier":
                out = torch.sigmoid(out)
        return out.numpy().astype(float)

    def save(self, d):
        from safetensors.torch import save_file

        save_file({k: v.contiguous() for k, v in self._m.state_dict().items()},
                  str(d / "torch.safetensors"))
        (d / "torch_meta.json").write_text(json.dumps({"n_in": self.n_in}))
        return ["torch.safetensors", "torch_meta.json"]

    def load(self, d):
        from safetensors.torch import load_file

        self.n_in = json.loads((d / "torch_meta.json").read_text())["n_in"]
        self._m = self._net(self.n_in)
        self._m.load_state_dict(load_file(str(d / "torch.safetensors")))
        self._m.eval()
        return self


class MLPModel(_TorchModel):
    kind = "mlp"

    def _net(self, n_in):
        from torch import nn

        h = int(self.params.get("hidden", 64))
        drop = float(self.params.get("dropout", 0.1))
        return nn.Sequential(nn.Linear(n_in, h), nn.ReLU(), nn.Dropout(drop),
                             nn.Linear(h, h), nn.ReLU(), nn.Dropout(drop), nn.Linear(h, 1))


class FTTransformerModel(_TorchModel):
    """Tabular transformer: the features are projected into ``tokens`` learned tokens
    (so hundreds of expanded features stay fast), a small transformer encoder mixes them
    with a CLS token, which predicts."""

    kind = "ft_transformer"

    def _net(self, n_in):
        import torch
        from torch import nn

        dim = int(self.params.get("dim", 16))
        heads = int(self.params.get("heads", 2))
        layers = int(self.params.get("layers", 1))
        tokens = int(self.params.get("tokens", 16))

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.proj = nn.Linear(n_in, tokens * dim)
                self.pos = nn.Parameter(torch.zeros(1, tokens, dim))
                self.cls = nn.Parameter(torch.zeros(1, 1, dim))
                enc = nn.TransformerEncoderLayer(dim, heads, dim * 2, dropout=0.1,
                                                 batch_first=True)
                self.enc = nn.TransformerEncoder(enc, layers, enable_nested_tensor=False)
                self.head = nn.Linear(dim, 1)

            def forward(self, x):
                tok = self.proj(x).view(len(x), tokens, dim) + self.pos
                tok = torch.cat([self.cls.expand(len(x), -1, -1), tok], dim=1)
                return self.head(self.enc(tok)[:, 0])

        return Net()


REGISTRY = {"lightgbm": LightGBMModel, "xgboost": XGBoostModel, "xgboost_rf": XGBoostRFModel,
            "catboost": CatBoostModel, "random_forest": ForestModel,
            "extra_trees": ExtraTreesModel, "linear": LinearModel, "mlp": MLPModel,
            "ft_transformer": FTTransformerModel}


def make(kind: str, task: str = "classifier", params: Optional[dict] = None,
         seed: int = 7) -> BaseModel:
    if kind not in REGISTRY:
        raise ValueError(f"model kind must be one of {sorted(REGISTRY)}")
    if not available(kind):
        raise ImportError(f"{kind} needs {_LIB[kind]} (pip install skopaqtrader[ml])")
    return REGISTRY[kind](task, params, seed)


def from_meta(meta: dict) -> BaseModel:
    m = REGISTRY[meta["kind"]](meta["task"], meta.get("params"), meta.get("seed", 7))
    m.best_iteration = meta.get("best_iteration")
    return m


def recency_weights(n: int, weight_factor: float) -> np.ndarray:
    """FreqAI's ``weight_factor``: the newest row weighs 1, older rows exponentially less
    (``exp(-age / (weight_factor × n))``); 0 = equal weights."""
    if weight_factor <= 0 or n <= 0:
        return np.ones(max(n, 0))
    age = np.arange(n)[::-1]
    return np.exp(-age / (weight_factor * n))
