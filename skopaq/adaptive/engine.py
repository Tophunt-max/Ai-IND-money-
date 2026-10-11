"""The adaptive AI engine: FreqAI-style training, prediction and backtesting with periodic
retraining, inside Skopaq's safety gates.

Targets (``AdaptiveConfig.target``):

* ``forward`` — every ``stride``-th closed bar: does a long entered at the next bar's open
  and closed ``horizon_bars`` later make money *after* round-trip charges and slippage on
  ``notional_inr``? (FreqAI's "predict the future move" setup, labelled net of costs.)
* ``setup`` — the strategies' own setups (``skopaq.scalping.backtest.simulate``): did the
  trade end with a positive net P&L?

Training (``train_adaptive``): chronological train / validation / test with an embargo,
robust outlier filter on the training rows, Dissimilarity Index fitted on them, model
fitted (LightGBM / XGBoost / CatBoost / logistic), threshold chosen on validation, report
on test — with and without the DI gate.

``rolling_backtest`` emulates live retraining: a model trained on the last ``train_days``
predicts the next ``retrain_days``, then is retrained, and so on; every prediction is out
of sample. ``AdaptiveModel.predict`` is the live inference used by ``SignalGate``.

Nothing here places orders or bypasses the risk gateway: a trained model is a registry
*candidate*; the gates, the shadow period and an admin's approval decide whether it may
ever influence an entry.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from skopaq.adaptive.features import FeatureConfig, FeatureExpander
from skopaq.adaptive.outliers import DissimilarityIndex, filter_training
from skopaq.finance.charges import EQUITY_INTRADAY, round_trip
from skopaq.learning.dataset import Example, chronological_split
from skopaq.learning.evaluation import choose_threshold, evaluate_gate, trade_stats
from skopaq.learning.model import MODEL_KINDS, load_model, make_model

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptiveConfig:
    features: FeatureConfig = field(default_factory=FeatureConfig)
    model_kind: str = "lightgbm"
    target: str = "forward"              # forward | setup
    horizon_bars: int = 15
    stride: int = 3
    notional_inr: float = 50_000.0
    slippage_pct: float = 0.0002
    product: str = EQUITY_INTRADAY
    train_days: int = 20
    retrain_days: int = 5
    outlier_z: float = 6.0
    di_threshold: float = 1.5
    isolation_forest: bool = False
    min_rows: int = 300
    entry_start_min: int = 15            # minutes after 09:15 before rows are taken
    entry_end_min: int = 330             # … and the last one (14:45)

    def as_dict(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "features"}
        out["features"] = self.features.as_dict()
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "AdaptiveConfig":
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "features"}
        return cls(features=FeatureConfig.from_dict(d.get("features") or {}), **kw)

    def resolved_kind(self) -> str:
        """The configured model kind, or ``logistic`` when its library is missing."""
        lib = {"lightgbm": "lightgbm", "xgboost": "xgboost", "catboost": "catboost"}.get(
            self.model_kind)
        if lib is None:
            return self.model_kind if self.model_kind in MODEL_KINDS else "logistic"
        try:
            __import__(lib)
            return self.model_kind
        except ImportError:
            logger.warning("%s is not installed: training the logistic baseline instead", lib)
            return "logistic"


# ── Datasets ─────────────────────────────────────────────────────────────────


def _by_date(days: Sequence[Sequence[Any]]) -> dict[date, list]:
    return {d[0].start.date(): list(d) for d in days if d}


def build_forward_dataset(days_by_symbol: Mapping[str, Sequence[Sequence[Any]]],
                          cfg: AdaptiveConfig,
                          correlated: Optional[Mapping[str, Sequence[Sequence[Any]]]] = None
                          ) -> list[Example]:
    """One row every ``stride`` closed bars: point-in-time expanded features, label = net
    win of a fixed-notional long over ``horizon_bars``."""
    exp = FeatureExpander(cfg.features)
    corr_days = {s: _by_date(d) for s, d in (correlated or {}).items()}
    step = timedelta(seconds=cfg.features.base_seconds)
    keep = (cfg.features.window + 2) * max(cfg.features.timeframes) // cfg.features.base_seconds
    out: list[Example] = []
    for symbol, days in days_by_symbol.items():
        history: list = []
        for day in days:
            day = list(day)
            if not day:
                continue
            corr = {s: m.get(day[0].start.date(), []) for s, m in corr_days.items()}
            session0 = day[0].start.replace(hour=9, minute=15, second=0, microsecond=0)
            for i in range(0, len(day) - cfg.horizon_bars - 1, max(1, cfg.stride)):
                bar = day[i]
                minute = (bar.start - session0).total_seconds() / 60
                if not (cfg.entry_start_min <= minute <= cfg.entry_end_min):
                    continue
                as_of = bar.start + step
                known = (history + day[:i + 1])[-keep:]
                feats = exp.expand(known, as_of, corr)
                entry = day[i + 1].open * (1 + cfg.slippage_pct)
                exit_ = day[i + 1 + cfg.horizon_bars].close * (1 - cfg.slippage_pct)
                qty = max(1, int(cfg.notional_inr // entry))
                net = (exit_ - entry) * qty - round_trip(cfg.product, entry, exit_, qty).total
                out.append(Example(
                    decided_at=as_of, features=feats, label=int(net > 0), net_pnl=round(net, 2),
                    strategy_id="adaptive.forward", symbol=symbol,
                    closed_at=day[i + 1 + cfg.horizon_bars].start + step,
                    evidence_last_bar=bar.start))
            history = (history + day)[-keep:]
    return out


def build_setup_dataset(symbol: str, days: Sequence[Sequence[Any]], settings: Any,
                        cfg: AdaptiveConfig,
                        correlated: Optional[Mapping[str, Sequence[Sequence[Any]]]] = None
                        ) -> list[Example]:
    """The strategies' setups from a backtest, with expanded features at each decision."""
    from skopaq.scalping.backtest import simulate

    exp = FeatureExpander(cfg.features)
    corr_days = {s: _by_date(d) for s, d in (correlated or {}).items()}

    def on_decision(candles, setup, decided_at):
        corr = {s: m.get(decided_at.date(), []) for s, m in corr_days.items()}
        return {"features": exp.expand(candles, decided_at, corr), "setup": setup}

    res = simulate(symbol, days, settings, slippage_pct=cfg.slippage_pct,
                   on_decision=on_decision)
    out = []
    for t in res.trades:
        tag = t.tag or {}
        decided = datetime.fromisoformat(t.decided_at) if t.decided_at else None
        if decided is None:
            continue
        out.append(Example(decided_at=decided, features=tag.get("features") or {},
                           label=int(t.pnl > 0), net_pnl=t.pnl,
                           strategy_id=f"scalp.{t.strategy}", symbol=symbol,
                           closed_at=decided.replace(hour=int(t.closed[:2]),
                                                     minute=int(t.closed[3:5])),
                           meta={"strategy": t.strategy}))
    return out


# ── The model ────────────────────────────────────────────────────────────────


@dataclass
class AdaptiveModel:
    config: AdaptiveConfig
    names: tuple[str, ...]
    inner: Any
    di: Optional[DissimilarityIndex]
    threshold: float = 0.5
    trained_at: str = ""
    trained_rows: int = 0
    kind: str = "adaptive-v1"
    candles_based: bool = True            # SignalGate feeds candles, not evidence

    def _x(self, features: Mapping[str, float]) -> list[float]:
        return [float(features.get(n, math.nan)) for n in self.names]

    def predict_features(self, features: Mapping[str, float]) -> tuple[float, float, bool]:
        """(probability, dissimilarity index, outlier?) of one feature vector."""
        prob = float(self.inner.predict_one(dict(features)))
        if self.di is None:
            return prob, math.nan, False
        out, di = self.di.is_outlier(self._x(features))
        return prob, di, out

    def predict(self, candles: Sequence[Any], as_of: datetime,
                correlated: Optional[Mapping[str, Sequence[Any]]] = None
                ) -> tuple[float, float, bool]:
        feats = FeatureExpander(self.config.features).expand(candles, as_of, correlated)
        return self.predict_features(feats)

    def predict_proba(self, examples: Sequence[Example]) -> np.ndarray:
        return self.inner.predict_proba(examples)

    def predict_one(self, features: dict[str, float]) -> float:
        return self.predict_features(features)[0]

    def outliers(self, examples: Sequence[Example]) -> np.ndarray:
        if self.di is None or not examples:
            return np.zeros(len(examples), bool)
        X = np.array([self._x(e.features) for e in examples], float)
        return self.di.score(X) > self.di.threshold

    def to_dict(self) -> dict:
        return {"kind": self.kind, "config": self.config.as_dict(), "names": list(self.names),
                "inner": self.inner.to_dict(), "di": self.di.to_dict() if self.di else None,
                "threshold": self.threshold, "trained_at": self.trained_at,
                "trained_rows": self.trained_rows}

    @classmethod
    def from_dict(cls, d: dict) -> "AdaptiveModel":
        return cls(config=AdaptiveConfig.from_dict(d["config"]), names=tuple(d["names"]),
                   inner=load_model(d["inner"]),
                   di=DissimilarityIndex.from_dict(d["di"]) if d.get("di") else None,
                   threshold=float(d.get("threshold", 0.5)),
                   trained_at=d.get("trained_at", ""), trained_rows=int(d.get("trained_rows", 0)))

    def fingerprint(self) -> str:
        return self.inner.fingerprint()


def _names(examples: Sequence[Example]) -> tuple[str, ...]:
    names: set[str] = set()
    for e in examples[:200]:
        names |= set(e.features)
    return tuple(sorted(names))


def fit_model(train: Sequence[Example], cfg: AdaptiveConfig,
              names: Optional[Sequence[str]] = None) -> AdaptiveModel:
    """Outlier-filtered fit of the configured model plus the Dissimilarity Index."""
    names = tuple(names or _names(train))
    X = np.array([[e.features.get(n, math.nan) for n in names] for e in train], float)
    keep = filter_training(X, z_max=cfg.outlier_z, isolation_forest=cfg.isolation_forest)
    rows = [e for e, k in zip(train, keep) if k]
    if len({e.label for e in rows}) < 2:
        raise ValueError("the outlier-filtered training set has one class only")
    inner = make_model(cfg.resolved_kind(), names).fit(rows)
    di = DissimilarityIndex.fit(X[keep], threshold=cfg.di_threshold)
    return AdaptiveModel(config=cfg, names=names, inner=inner, di=di,
                         trained_at=max(e.decided_at for e in rows).isoformat(),
                         trained_rows=len(rows))


def _gated_stats(model: AdaptiveModel, test: Sequence[Example], thr: float) -> dict:
    probs = model.predict_proba(test)
    out = model.outliers(test)
    with_di = [e.net_pnl for e, p, o in zip(test, probs, out) if p >= thr and not o]
    return {"model_di": trade_stats(with_di).as_dict(),
            "outlier_rate": round(float(out.mean()), 4) if len(out) else None}


def train_adaptive(examples: Sequence[Example], cfg: AdaptiveConfig) -> tuple[AdaptiveModel, dict]:
    """Train on the chronological train set; threshold on validation; report on test."""
    labelled = [e for e in examples if e.label is not None]
    if len(labelled) < cfg.min_rows:
        raise ValueError(f"{len(labelled)} rows < {cfg.min_rows}: not enough history")
    train, val, test = chronological_split(labelled)
    model = fit_model(train, cfg)
    thr = choose_threshold(model, val)
    model.threshold = thr
    report = evaluate_gate(model, test, thr)
    report.update(_gated_stats(model, test, thr))
    report["features"] = len(model.names)
    report["model_kind"] = cfg.resolved_kind()
    report["leakage"] = []
    return model, report


def rolling_backtest(examples: Sequence[Example], cfg: AdaptiveConfig) -> dict:
    """FreqAI-style backtest: retrain every ``retrain_days`` on the previous
    ``train_days`` (a sliding window), predict the next block out of sample."""
    rows = sorted((e for e in examples if e.label is not None), key=lambda e: e.decided_at)
    days = sorted({e.decided_at.date() for e in rows})
    if len(days) <= cfg.train_days:
        return {"retrains": 0, "reason": f"{len(days)} day(s) <= train_days {cfg.train_days}"}
    blocks, base, gated, gated_di = [], [], [], []
    i = cfg.train_days
    names = _names(rows)
    while i < len(days):
        train_days = set(days[i - cfg.train_days:i])
        test_days = set(days[i:i + cfg.retrain_days])
        tr = [e for e in rows if e.decided_at.date() in train_days]
        edge = max((e.closed_at or e.decided_at) for e in tr)
        te = [e for e in rows if e.decided_at.date() in test_days and e.decided_at > edge]
        i += cfg.retrain_days
        if not te or len({e.label for e in tr}) < 2:
            continue
        split = int(len(tr) * 0.8)
        try:
            m = fit_model(tr[:split], cfg, names)
        except ValueError:
            continue
        thr = choose_threshold(m, tr[split:], min_trades=5)
        probs, outs = m.predict_proba(te), m.outliers(te)
        b = [e.net_pnl for e in te]
        g = [e.net_pnl for e, p in zip(te, probs) if p >= thr]
        gd = [e.net_pnl for e, p, o in zip(te, probs, outs) if p >= thr and not o]
        base += b
        gated += g
        gated_di += gd
        blocks.append({"from": min(test_days).isoformat(), "to": max(test_days).isoformat(),
                       "train_rows": len(tr), "test_rows": len(te), "threshold": thr,
                       "baseline_net": round(sum(b), 2), "model_net": round(sum(g), 2),
                       "model_di_net": round(sum(gd), 2),
                       "outlier_rate": round(float(outs.mean()), 4)})
    return {"retrains": len(blocks), "blocks": blocks,
            "baseline": trade_stats(base).as_dict(), "model": trade_stats(gated).as_dict(),
            "model_di": trade_stats(gated_di).as_dict()}
