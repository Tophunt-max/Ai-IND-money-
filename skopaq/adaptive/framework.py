"""The adaptive framework: your strategy's features and targets, a configurable data
pipeline, any model library, rich predictions, verifiable bundles.

``train_bundle`` (one training run):

1. ``build_dataset`` — ``strategy.build_frame`` per symbol (features ``%``, targets ``&``),
   after ``check_lookahead`` proved the features use no future data; rows with a missing
   feature or target are dropped (FreqAI does the same); every row also gets ``_net``: the
   net P&L of a fixed-notional long over the target's horizon, after Indian charges and
   slippage — the money the evaluation counts, whatever the target is.
2. Chronological train / validation / test with an embargo of the label horizon.
3. ``Pipeline`` fitted on the training rows (variance threshold, scaler, PCA, SVM / DI /
   DBSCAN outlier removal, noise); recency ``weight_factor`` weights.
4. One model per target (classifier or regressor, ``strategy.model_type``), early-stopped
   on the validation rows.
5. Entry rule chosen on validation: classifier — probability ≥ threshold; regressor —
   prediction ≥ mean + k·std of the validation predictions (FreqAI's adaptive
   ``&-target_mean/_std`` pattern); or ``strategy.entry_signal``.
6. Test report: every row vs the model's entries vs entries without outliers.
7. Saved as a bundle (``skopaq.adaptive.artifacts``).

``AdaptiveBundle.predict_full`` is the live output: per-target value, training label
mean/std, live prediction mean/std (rolling, like FreqAI's ``fit_live_predictions``),
z-score, ``do_predict`` (1 ok, 0 outlier, -1 not enough data), DI value and every
detector's flag, the entry decision and its rule, the model's age, the most important
features. ``rolling_backtest`` retrains on a sliding window and predicts the next block.

**Continual learning** (``TrainConfig.continual``, ``SKOPAQ_ADAPTIVE_CONTINUAL_LEARNING``,
``--continual``): instead of starting from scratch, a run continues from the previous
model of the same strategy and model kind (the champion, else the shadow, else the newest
bundle — ``find_parent``). The parent's fitted pipeline is kept frozen (the continued model
keeps its input space) and each target's model is warm-started from the parent's
(``models.BaseModel.fit(init=…)``). A parent with other features, targets or task, or a
model kind that cannot be continued, means a fresh fit, with the reason in
``report["continual"]``. The rolling backtest then chains its retrains the same way, so it
emulates continual retraining. A continued model is still only a candidate: the same
gates, shadow period and admin approval apply.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from skopaq.adaptive import artifacts
from skopaq.adaptive.models import from_meta, make, recency_weights
from skopaq.adaptive.pipeline import Pipeline, PipelineConfig
from skopaq.adaptive.strategy import (
    AdaptiveStrategy,
    build_frame,
    check_lookahead,
    feature_columns,
    last_row_features,
    target_columns,
)
from skopaq.finance.charges import EQUITY_INTRADAY, round_trip
from skopaq.learning.evaluation import trade_stats

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainConfig:
    model_kind: str = "lightgbm"
    model_params: dict = field(default_factory=dict)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    weight_factor: float = 0.0
    val_frac: float = 0.15
    test_frac: float = 0.15
    stride: int = 3
    notional_inr: float = 50_000.0
    slippage_pct: float = 0.0002
    product: str = EQUITY_INTRADAY
    entry_start_min: int = 15
    entry_end_min: int = 330
    min_rows: int = 300
    seed: int = 7
    continual: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["pipeline"] = self.pipeline.as_dict()
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TrainConfig":
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "pipeline"}
        return cls(pipeline=PipelineConfig.from_dict(d.get("pipeline") or {}), **kw)


def _flat(days_or_candles: Sequence[Any]) -> list:
    if days_or_candles and isinstance(days_or_candles[0], (list, tuple)):
        return [c for d in days_or_candles for c in d]
    return list(days_or_candles)


def build_dataset(strategy: AdaptiveStrategy, history: Mapping[str, Sequence[Any]],
                  cfg: TrainConfig, correlated: Optional[Mapping[str, Sequence[Any]]] = None,
                  *, verify_lookahead: bool = True) -> pd.DataFrame:
    base = strategy.timeframes[0]
    corr = {s: _flat(v) for s, v in (correlated or {}).items()}
    parts = []
    checked = False
    for sym, data in history.items():
        candles = _flat(data)
        if len(candles) < 60:
            continue
        if verify_lookahead and not checked:
            check_lookahead(strategy, candles[-3000:], correlated=corr)
            checked = True
        f = build_frame(strategy, candles, sym, corr)
        dt = pd.to_datetime(f["date"])
        minute = dt.dt.hour * 60 + dt.dt.minute - (9 * 60 + 15)
        lp = strategy.label_period
        entry = f["_open"].shift(-1) * (1 + cfg.slippage_pct)
        exit_ = f["_close"].shift(-lp) * (1 - cfg.slippage_pct)
        # the horizon must stay inside the day (intraday positions are flat by the close)
        same_day = dt.dt.date == dt.shift(-lp).dt.date
        qty = (cfg.notional_inr // entry).clip(lower=1)
        gross = (exit_ - entry) * qty
        costs = [round_trip(cfg.product, a, b, q).total if a == a and b == b else math.nan
                 for a, b, q in zip(entry, exit_, qty)]
        f = pd.concat([f, pd.DataFrame({
            "_net": gross - np.array(costs), "_symbol": sym,
            "_decided_at": dt + pd.Timedelta(seconds=base),
            "_closed_at": dt + pd.Timedelta(seconds=base * (lp + 1))}, index=f.index)], axis=1)
        keep = (minute >= cfg.entry_start_min) & (minute <= cfg.entry_end_min) & same_day
        f = f[keep]
        f = f.iloc[::max(1, cfg.stride)]
        parts.append(f)
    if not parts:
        return pd.DataFrame()
    frame = pd.concat(parts, ignore_index=True)
    feats, tgts = feature_columns(frame), target_columns(frame)
    frame = frame.dropna(subset=feats + tgts + ["_net"])
    return frame.sort_values("_decided_at").reset_index(drop=True)


def split(frame: pd.DataFrame, cfg: TrainConfig) -> tuple[pd.DataFrame, pd.DataFrame,
                                                          pd.DataFrame]:
    n = len(frame)
    a = int(n * (1 - cfg.val_frac - cfg.test_frac))
    b = int(n * (1 - cfg.test_frac))
    tr, va, te = frame.iloc[:a], frame.iloc[a:b], frame.iloc[b:]
    if len(tr):
        va = va[va["_decided_at"] > tr["_closed_at"].max()]
    if len(va):
        te = te[te["_decided_at"] > va["_closed_at"].max()]
    elif len(tr):
        te = te[te["_decided_at"] > tr["_closed_at"].max()]
    return tr, va, te


@dataclass
class Fitted:
    strategy_name: str
    features: list[str]
    targets: list[str]
    task: str
    pipeline: Pipeline
    models: dict[str, Any]
    label_stats: dict[str, dict]
    pred_stats: dict[str, dict]
    primary: str
    rule: dict
    train_cfg: TrainConfig
    importance: list[tuple[str, float]] = field(default_factory=list)
    trained_at: str = ""
    rows: dict = field(default_factory=dict)
    strategy: Optional[AdaptiveStrategy] = None
    continual: dict = field(default_factory=dict)

    # ── scoring ─────────────────────────────────────────────────────────

    def score_matrix(self, X: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
        Z = self.pipeline.transform(X)
        preds = {t: m.predict(Z) for t, m in self.models.items()}
        return preds, self.pipeline.outliers(Z)

    def entries(self, preds: Mapping[str, np.ndarray]) -> np.ndarray:
        """The entry decisions: the strategy's ``entry_signal`` when it defines one (fed
        the same per-target values and statistics as live), else the validated rule."""
        if self.strategy is not None and type(self.strategy).entry_signal is not \
                AdaptiveStrategy.entry_signal:
            out = np.zeros(len(preds[self.primary]), bool)
            default = self._rule_entries(preds)
            for i in range(len(out)):
                targets = {t: {"value": float(v[i]), **{f"pred_{k}": self.pred_stats[t][k]
                                                       for k in ("mean", "std")},
                               **{f"label_{k}": self.label_stats[t][k] for k in ("mean", "std")}}
                           for t, v in preds.items()}
                r = self.strategy.entry_signal({"targets": targets, "primary": self.primary},
                                               {"symbol": ""})
                out[i] = default[i] if r is None else bool(r)
            return out
        return self._rule_entries(preds)

    def _rule_entries(self, preds: Mapping[str, np.ndarray]) -> np.ndarray:
        p = preds[self.primary]
        if self.rule["type"] == "probability":
            return p >= self.rule["threshold"]
        st = self.pred_stats[self.primary]
        return p >= st["mean"] + self.rule["k"] * st["std"]


def _choose_rule(task: str, pred: np.ndarray, net: np.ndarray, min_trades: int) -> dict:
    best, best_net = None, -math.inf
    if task == "classifier":
        for t in (0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7):
            take = pred >= t
            if take.sum() >= min_trades and net[take].sum() > best_net:
                best, best_net = {"type": "probability", "threshold": t}, net[take].sum()
        return best or {"type": "probability", "threshold": 0.5}
    mu, sd = float(pred.mean()), float(pred.std() or 1e-12)
    for k in (0.0, 0.25, 0.5, 1.0, 1.5, 2.0):
        take = pred >= mu + k * sd
        if take.sum() >= min_trades and net[take].sum() > best_net:
            best, best_net = {"type": "zscore", "k": k}, net[take].sum()
    return best or {"type": "zscore", "k": 1.0}


def parent_problem(parent: Any, feats: list[str], tgts: list[str], task: str) -> Optional[str]:
    """Why ``parent`` cannot be continued with these columns (None: it can)."""
    if parent is None:
        return "no previous model"
    if list(parent.features) != list(feats):
        return "the previous model has other features (strategy or data changed)"
    if getattr(parent, "task", task) != task:
        return f"the previous model is a {parent.task}"
    missing = [t for t in tgts if t not in parent.models]
    if missing:
        return f"the previous model has no {missing[0]} target"
    return None


def fit(strategy: AdaptiveStrategy, train: pd.DataFrame, val: pd.DataFrame,
        cfg: TrainConfig, parent: Any = None) -> Fitted:
    """``parent``: a previous ``Fitted`` / ``AdaptiveBundle`` to continue (continual
    learning); ignored, with the reason recorded, when it does not match."""
    feats, tgts = feature_columns(train), target_columns(train)
    if not tgts:
        raise ValueError("the strategy set no targets (columns starting with '&')")
    task = strategy.model_type
    X = train[feats].to_numpy(float)
    w = recency_weights(len(train), cfg.weight_factor)
    Y = {t: train[t].to_numpy(float) for t in tgts}
    why_not = parent_problem(parent, feats, tgts, task) if cfg.continual else "off"
    continual: dict[str, Any] = {"enabled": cfg.continual,
                                 "parent": getattr(parent, "version", None)
                                 if why_not is None else None}
    if why_not is None:
        pipe = Pipeline.from_dict(parent.pipeline.to_dict())        # frozen copy
        Z, kept = pipe.apply_frozen(X, w, *Y.values(), noise_std=cfg.pipeline.noise_std,
                                    seed=cfg.seed)
        continual["pipeline"] = "kept from the parent"
    else:
        pipe = Pipeline(cfg.pipeline)
        Z, kept = pipe.fit_transform(X, feats, w, *Y.values())
        if cfg.continual:
            continual["fresh"] = why_not
    w_kept, ys = kept[0], dict(zip(tgts, kept[1:]))
    Zv = pipe.transform(val[feats].to_numpy(float)) if len(val) else None
    models, imp = {}, np.zeros(len(pipe.keep) if not pipe.pca_components
                                else len(pipe.pca_components))
    for t in tgts:
        y = ys[t]
        if task == "classifier" and len(set(np.unique(y))) < 2:
            raise ValueError(f"target {t}: the training rows have one class only")
        m = make(cfg.model_kind, task, cfg.model_params, cfg.seed)
        init = parent.models.get(t) if why_not is None else None
        m.fit(Z, y, w_kept, Zv, val[t].to_numpy(float) if Zv is not None else None,
              init=init)
        if why_not is None:
            continual.setdefault("models", {})[t] = (
                "continued" if m.continued else (m.can_continue(init) or "fresh"))
        models[t] = m
        for i, v in m.importance.items():
            if i < len(imp):
                imp[i] += v
    names = ([feats[i] for i in pipe.keep] if not pipe.pca_components
             else [f"pca_{i}" for i in range(len(pipe.pca_components))])
    order = np.argsort(-imp)[:15]
    importance = [(names[i], round(float(imp[i] / max(imp.sum(), 1e-12)), 5))
                  for i in order if imp[i] > 0]
    label_stats = {t: {"mean": float(np.mean(ys[t])), "std": float(np.std(ys[t]))}
                   for t in tgts}
    primary = getattr(strategy, "primary_target", None) or tgts[0]
    if Zv is not None and len(Zv):
        vp = {t: m.predict(Zv) for t, m in models.items()}
    else:
        vp = {t: m.predict(Z) for t, m in models.items()}
    pred_stats = {t: {"mean": float(v.mean()), "std": float(v.std())} for t, v in vp.items()}
    net_v = val["_net"].to_numpy(float) if len(val) else train["_net"].to_numpy(float)[:len(
        vp[primary])]
    rule = _choose_rule(task, vp[primary], net_v, max(10, len(net_v) // 20))
    return Fitted(strategy_name=strategy.name, features=feats, targets=tgts, task=task,
                  pipeline=pipe, models=models, label_stats=label_stats,
                  pred_stats=pred_stats, primary=primary, rule=rule, train_cfg=cfg,
                  importance=importance,
                  trained_at=pd.Timestamp(train["_decided_at"].max()).isoformat(),
                  rows={"train": len(train), "train_kept": pipe.fitted_rows,
                        "validation": len(val)}, strategy=strategy, continual=continual)


def evaluate(fitted: Fitted, test: pd.DataFrame) -> dict:
    if not len(test):
        return {"samples": 0}
    preds, outs = fitted.score_matrix(test[fitted.features].to_numpy(float))
    net = test["_net"].to_numpy(float)
    take = fitted.entries(preds)
    clean = take & ~outs["any"]
    out = {"samples": len(test), "baseline": trade_stats(net).as_dict(),
           "model": trade_stats(net[take]).as_dict(),
           "model_di": trade_stats(net[clean]).as_dict(),
           "outlier_rate": round(float(outs["any"].mean()), 4),
           "outliers": {k: round(float(outs[k].mean()), 4) for k in ("svm", "di", "dbscan")}}
    p = fitted.primary
    if fitted.task == "classifier":
        y = test[p].to_numpy(float)
        rate = float(y.mean())
        out["brier"] = float(np.mean((preds[p] - y) ** 2))
        out["brier_base_rate"] = float(np.mean((rate - y) ** 2))
        out["base_rate"] = rate
    else:
        y = test[p].to_numpy(float)
        out["mae"] = float(np.mean(np.abs(preds[p] - y)))
        out["mae_mean_baseline"] = float(np.mean(np.abs(y.mean() - y)))
        out["corr"] = float(np.corrcoef(preds[p], y)[0, 1]) if len(y) > 2 else None
    return out


# ── bundles ──────────────────────────────────────────────────────────────────


def save(fitted: Fitted, strategy: AdaptiveStrategy, directory: Path, *, version: str,
         dataset: dict, report: dict) -> Path:
    d = Path(directory) / version
    if d.exists():
        raise FileExistsError(d)
    (d / "models").mkdir(parents=True)
    model_files = {}
    for i, (t, m) in enumerate(fitted.models.items()):
        md = d / "models" / f"t{i}"
        md.mkdir()
        model_files[t] = {"dir": f"models/t{i}", "files": m.save(md), "meta": m.meta()}
    artifacts.write_json_gz(d / "pipeline.json.gz", fitted.pipeline.to_dict())
    info = {"strategy": {"name": strategy.name, "class": type(strategy).__name__,
                         "timeframes": list(strategy.timeframes),
                         "periods": list(strategy.periods), "shifted": strategy.shifted,
                         "correlated": list(strategy.correlated),
                         "label_period": strategy.label_period,
                         "model_type": strategy.model_type,
                         **({"spec": strategy.spec} if getattr(strategy, "spec", None)
                            else {})},
            "continual": fitted.continual,
            "features": fitted.features, "targets": fitted.targets, "task": fitted.task,
            "primary": fitted.primary, "rule": fitted.rule, "label_stats": fitted.label_stats,
            "pred_stats": fitted.pred_stats, "importance": fitted.importance,
            "trained_at": fitted.trained_at, "rows": fitted.rows,
            "train_config": fitted.train_cfg.as_dict(), "models": model_files,
            "pipeline": fitted.pipeline.describe()}
    (d / "bundle.json").write_text(json.dumps(info, indent=1, default=str))
    (d / "report.json").write_text(json.dumps(report, indent=1, default=str))
    artifacts.write_manifest(d, version=version, extra={"dataset": dataset})
    return d


class AdaptiveBundle:
    """A verified, loaded bundle: live predictions (``SignalGate`` feeds it candles)."""

    candles_based = True
    rich = True

    def __init__(self, path: Path, strategy: Optional[AdaptiveStrategy] = None) -> None:
        self.path = Path(path)
        self.manifest = artifacts.verify(self.path)
        info = json.loads((self.path / "bundle.json").read_text())
        self.info = info
        self.version = self.manifest["version"]
        self.features: list[str] = info["features"]
        self.primary, self.rule, self.task = info["primary"], info["rule"], info["task"]
        self.pipeline = Pipeline.from_dict(artifacts.read_json_gz(self.path / "pipeline.json.gz"))
        self.models = {}
        for t, spec in info["models"].items():
            self.models[t] = from_meta(spec["meta"]).load(self.path / spec["dir"])
        self.strategy = strategy or _strategy_from_info(info["strategy"])
        di = self.pipeline.di
        self.di = type("DI", (), {"threshold": di["threshold"]})() if di else None
        self.threshold = info["rule"].get("threshold", 0.5)
        self._log = self.path / "predictions.jsonl"
        # Earlier days' closed candles per symbol (``seed_history``): the live series
        # holds today's bars only, the features need the warm-up the training rows had
        self.history: dict[str, list] = {}

    def seed_history(self, symbol: str, candles: Sequence[Any]) -> None:
        self.history[symbol.upper()] = list(candles)[-3000:]

    def _with_history(self, symbol: str, candles: Sequence[Any]) -> list:
        past = self.history.get((symbol or "").upper())
        if not past:
            return list(candles)
        first = candles[0].start if candles else None
        return [c for c in past if first is None or c.start < first] + list(candles)

    def _live_stats(self, target: str, n: int = 500) -> Optional[dict]:
        try:
            lines = self._log.read_text().splitlines()[-n:]
        except OSError:
            return None
        vals = []
        for raw in lines:
            try:
                v = json.loads(raw).get("values", {}).get(target)
            except ValueError:
                continue
            if isinstance(v, (int, float)):
                vals.append(float(v))
        if len(vals) < 20:
            return None
        return {"mean": float(np.mean(vals)), "std": float(np.std(vals)), "n": len(vals)}

    def predict_features(self, feats: Mapping[str, float], *, symbol: str = "",
                         now: Optional[datetime] = None, log: bool = True) -> dict:
        x = np.array([[float(feats.get(c, math.nan)) for c in self.features]])
        missing = int(np.isnan(x).sum())
        out: dict[str, Any] = {"version": self.version, "missing_features": missing,
                               "trained_at": self.info["trained_at"], "primary": self.primary}
        try:
            age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(
                self.info["trained_at"])
            out["age_hours"] = round(age.total_seconds() / 3600, 1)
        except (TypeError, ValueError):
            out["age_hours"] = None
        if missing:
            out.update(do_predict=-1, entry=False, outlier=False, di=None, score=None,
                       entry_rule="not enough data")
            return out
        Z = self.pipeline.transform(x)
        o = self.pipeline.outliers(Z)
        targets, values = {}, {}
        for t, m in self.models.items():
            v = float(m.predict(Z)[0])
            values[t] = v
            ls, ps = self.info["label_stats"][t], self.info["pred_stats"][t]
            live = self._live_stats(t)
            ref = live or ps
            targets[t] = {"value": round(v, 6), "label_mean": ls["mean"], "label_std": ls["std"],
                          "pred_mean": ref["mean"], "pred_std": ref["std"],
                          "stats_source": "live" if live else "validation",
                          "zscore": round((v - ref["mean"]) / ref["std"], 4)
                          if ref["std"] else None}
        p = values[self.primary]
        if self.rule["type"] == "probability":
            entry = p >= self.rule["threshold"]
            rule = f"P ≥ {self.rule['threshold']}"
        else:
            st = targets[self.primary]
            entry = p >= st["pred_mean"] + self.rule["k"] * st["pred_std"]
            rule = f"prediction ≥ mean + {self.rule['k']}·std"
        custom = self.strategy.entry_signal({"targets": targets, "primary": self.primary},
                                            {"symbol": symbol})
        if custom is not None:
            entry, rule = bool(custom), "strategy.entry_signal"
        outlier = bool(o["any"][0])
        di_v = o["di_values"][0]
        out.update(do_predict=0 if outlier else 1, score=round(p, 6), targets=targets,
                   entry=bool(entry and not outlier), entry_rule=rule, outlier=outlier,
                   di=None if di_v != di_v else round(float(di_v), 4),
                   di_threshold=self.pipeline.di["threshold"] if self.pipeline.di else None,
                   detectors={k: bool(o[k][0]) for k in ("svm", "di", "dbscan")},
                   top_features=[n for n, _ in self.info.get("importance", [])[:5]])
        if log:
            try:
                with open(self._log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": (now or datetime.now(timezone.utc)).isoformat(),
                                         "symbol": symbol, "values": values}) + "\n")
            except OSError:
                pass
        return out

    def predict_full(self, candles: Sequence[Any], as_of: datetime,
                     correlated: Optional[Mapping[str, Sequence[Any]]] = None,
                     symbol: str = "") -> dict:
        corr = {k: self._with_history(k, v) for k, v in (correlated or {}).items()}
        for k in self.strategy.correlated:
            if k not in corr and self.history.get(k):
                corr[k] = list(self.history[k])
        feats = last_row_features(self.strategy, self._with_history(symbol, candles), as_of,
                                  corr)
        return self.predict_features(feats, symbol=symbol, now=as_of)

    def predict(self, candles, as_of, correlated=None,
                symbol: str = "") -> tuple[float, float, bool]:
        """(score, DI, outlier) — the interface ``SignalGate`` calls."""
        r = self.predict_full(candles, as_of, correlated, symbol)
        self.last = r
        score = r.get("score")
        return (float(score) if score is not None else float("nan"),
                float(r["di"]) if r.get("di") is not None else float("nan"),
                bool(r.get("outlier") or r.get("do_predict") == -1))

    def predict_one(self, features: dict[str, float]) -> float:
        return float(self.predict_features(features, log=False).get("score") or math.nan)

    def fingerprint(self) -> str:
        return self.manifest["files"]["bundle.json"]["sha256"][:12]


def _strategy_from_info(info: dict) -> AdaptiveStrategy:
    """A default strategy carrying the stored layout (a custom class is loaded from
    ``SKOPAQ_ADAPTIVE_STRATEGY`` when its name matches)."""
    import os

    from skopaq.adaptive.strategy import load_strategy

    if info.get("spec"):                 # a no-code strategy: rebuilt from its stored spec
        from skopaq.adaptive.spec import SpecStrategy

        return SpecStrategy(info["spec"])
    spec = os.environ.get("SKOPAQ_ADAPTIVE_STRATEGY", "")
    if spec:
        s = load_strategy(spec)
        if s.name == info.get("name"):
            return s
    s = AdaptiveStrategy()
    for k in ("timeframes", "periods", "correlated"):
        setattr(s, k, tuple(info.get(k, getattr(s, k))))
    s.shifted, s.label_period = info.get("shifted", s.shifted), info.get("label_period", 15)
    s.model_type = info.get("model_type", s.model_type)
    return s


def load_bundle_model(d: dict) -> AdaptiveBundle:
    """Registry ``model`` dict → the verified bundle."""
    import os

    paths = [Path(d.get("path", ""))]
    env = os.environ.get("SKOPAQ_MODEL_DIR", "").strip()
    if env:
        paths.append(Path(env).expanduser() / "artifacts" / d["bundle"])
    paths.append(Path("~/.skopaq/models/artifacts").expanduser() / d["bundle"])
    for p in paths:
        if p and (p / artifacts.MANIFEST).is_file():
            b = AdaptiveBundle(p)
            if d.get("manifest_sha") and b.manifest["files"]["bundle.json"]["sha256"] != \
                    d["manifest_sha"]:
                raise artifacts.IntegrityError(f"{p.name}: not the registered bundle")
            return b
    raise FileNotFoundError(f"bundle {d.get('bundle')} not found")


# ── one run, registry, rolling backtest ──────────────────────────────────────


def train_bundle(strategy: AdaptiveStrategy, history: Mapping[str, Sequence[Any]],
                 cfg: TrainConfig, *, correlated=None, registry=None,
                 notes: str = "") -> dict:
    frame = build_dataset(strategy, history, cfg, correlated)
    if len(frame) < cfg.min_rows:
        raise ValueError(f"{len(frame)} rows < {cfg.min_rows}: not enough history")
    tr, va, te = split(frame, cfg)
    parent = find_parent(registry, strategy.name, cfg.model_kind) if (
        cfg.continual and registry is not None) else None
    fitted = fit(strategy, tr, va, cfg, parent=parent)
    report = evaluate(fitted, te)
    report["continual"] = fitted.continual
    report.update(model_kind=cfg.model_kind, features=len(fitted.features),
                  targets=fitted.targets, rule=fitted.rule, pipeline=fitted.pipeline.describe(),
                  importance=fitted.importance[:10], leakage=[],
                  weight_factor=cfg.weight_factor)
    report["rolling"] = rolling_backtest(strategy, frame, cfg, parent=parent)
    dataset = {"rows": len(frame), "symbols": sorted(history),
               "correlated": list(strategy.correlated), "strategy": strategy.name,
               "first": str(frame["_decided_at"].min()), "last": str(frame["_decided_at"].max()),
               "features": len(fitted.features), "train": len(tr), "validation": len(va),
               "test": len(te)}
    if registry is None:
        return {"report": report, "dataset": dataset, "fitted": fitted}
    version = f"adaptive-{strategy.name}-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%f")[:-3]
    bdir = save(fitted, strategy, Path(registry.dir) / "artifacts", version=version,
                dataset=dataset, report=report)
    sha = artifacts.verify(bdir)["files"]["bundle.json"]["sha256"]
    entry = registry.register(
        model={"kind": "adaptive-bundle-v1", "bundle": version, "path": str(bdir),
               "manifest_sha": sha},
        report=report, params={"threshold": fitted.rule.get("threshold")}, dataset=dataset,
        strategy_id=f"adaptive.{strategy.name}",
        notes=notes or (f"{cfg.model_kind}, continued from {fitted.continual['parent']}"
                        if fitted.continual.get("parent") else f"{cfg.model_kind}"))
    entry["gate_failures"] = registry.evaluate_candidate(entry["version"])
    return registry.get(entry["version"]) or entry


def find_parent(registry: Any, strategy_name: str, model_kind: str) -> Any:
    """The model a continual run continues: champion, else shadow, else the newest
    registered bundle — of the same strategy and model kind, and verified on load."""
    if registry is None:
        return None
    sid = f"adaptive.{strategy_name}"
    entries = [registry.champion(), registry.shadow()]
    entries += [registry.get(v["version"]) for v in reversed(registry.versions())
                if v.get("strategy_id") == sid]
    for e in entries:
        if not e or e.get("strategy_id") != sid:
            continue
        m = e.get("model") or {}
        if m.get("kind") != "adaptive-bundle-v1":
            continue
        try:
            b = load_bundle_model(m)
        except Exception as exc:
            logger.warning("Continual learning: %s not usable (%s)", e.get("version"), exc)
            continue
        kinds = {spec["meta"]["kind"] for spec in b.info["models"].values()}
        if kinds == {model_kind}:
            b.version = e["version"]
            return b
    return None


def rolling_backtest(strategy: AdaptiveStrategy, frame: pd.DataFrame, cfg: TrainConfig, *,
                     train_days: int = 15, retrain_days: int = 5, parent: Any = None) -> dict:
    """Retrain every ``retrain_days`` on the previous ``train_days`` (sliding window) and
    predict the next block, out of sample. With ``cfg.continual`` each retrain continues
    the previous block's model (the first one ``parent``, if any), as live would."""
    days = sorted(pd.to_datetime(frame["_decided_at"]).dt.date.unique())
    if len(days) <= train_days:
        return {"retrains": 0, "reason": f"{len(days)} day(s) <= train_days {train_days}"}
    blocks, base, gated, gated_c = [], [], [], []
    dates = pd.to_datetime(frame["_decided_at"]).dt.date
    i = train_days
    while i < len(days):
        trd, ted = set(days[i - train_days:i]), set(days[i:i + retrain_days])
        tr = frame[dates.isin(trd)]
        te = frame[dates.isin(ted)]
        te = te[te["_decided_at"] > tr["_closed_at"].max()]
        i += retrain_days
        if not len(te) or len(tr) < 100:
            continue
        cut = int(len(tr) * 0.8)
        try:
            fitted = fit(strategy, tr.iloc[:cut], tr.iloc[cut:], cfg, parent=parent)
        except ValueError:
            continue
        if cfg.continual:
            fitted.version = f"rolling-{len(blocks)}"          # type: ignore[attr-defined]
            parent = fitted
        preds, outs = fitted.score_matrix(te[fitted.features].to_numpy(float))
        take = fitted.entries(preds)
        net = te["_net"].to_numpy(float)
        base += list(net)
        gated += list(net[take])
        gated_c += list(net[take & ~outs["any"]])
        blocks.append({"from": str(min(ted)), "to": str(max(ted)), "train_rows": len(tr),
                       "test_rows": len(te), "baseline_net": round(float(net.sum()), 2),
                       "model_net": round(float(net[take].sum()), 2),
                       "model_di_net": round(float(net[take & ~outs["any"]].sum()), 2),
                       "continued": bool(fitted.continual.get("parent"))})
    return {"retrains": len(blocks), "blocks": blocks, "continual": cfg.continual,
            "baseline": trade_stats(base).as_dict(),
            "model": trade_stats(gated).as_dict(), "model_di": trade_stats(gated_c).as_dict()}


def config_with(cfg: TrainConfig, **over: Any) -> TrainConfig:
    pipe_keys = set(PipelineConfig.__dataclass_fields__)
    pipe = {k: v for k, v in over.items() if k in pipe_keys and v is not None}
    rest = {k: v for k, v in over.items() if k not in pipe_keys and v is not None}
    return replace(cfg, pipeline=replace(cfg.pipeline, **pipe), **rest)

