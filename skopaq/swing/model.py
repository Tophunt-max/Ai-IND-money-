"""The swing model: which daily setups to take, learned from their simulated net outcomes.

``build_dataset`` walks each symbol's daily history; on every bar where a setup fires
it records the point-in-time features (``Series.features`` + the setup) and the outcome
of trading it (``setups.simulate``: net of delivery charges and slippage). The label is
"made money after charges".

``train_and_register`` trains on the earlier dates, picks a probability threshold on a
validation slice, and tests on the latest dates it never saw — every setup (the
baseline) vs only those the model takes — in the registry's gate format. The result is
saved pickle-free (the model library's own format, the pipeline as JSON, a SHA-256
manifest) and registered as a ``candidate`` in the swing registry (``<model_dir>/swing``);
passing the gates makes it the shadow model, and an admin promotes it to champion after
the shadow days. ``load_active`` returns what the live swing engine uses: the champion,
or on a paper account the shadow model.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from skopaq.adaptive import artifacts
from skopaq.learning.evaluation import trade_stats
from skopaq.learning.registry import Gates, ModelRegistry
from skopaq.swing.setups import SETUPS, WARMUP, Series, SwingParams, simulate

logger = logging.getLogger(__name__)

KIND = "swing-model-v1"
FEATURES = (
    "ret_1",
    "ret_5",
    "ret_20",
    "ret_60",
    "rsi",
    "atr_pct",
    "dist_ema20",
    "dist_sma50",
    "dist_sma200",
    "sma50_slope",
    "vol_ratio",
    "range_pos20",
    "close_pos",
    "gap",
) + tuple(f"setup_{s}" for s in SETUPS)
SWING_GATES = Gates(min_test_samples=40, min_gated_trades=20)


@dataclass(frozen=True)
class SwingTrainConfig:
    model_kind: str = "lightgbm"
    params: SwingParams = field(default_factory=SwingParams)
    train_frac: float = 0.6
    val_frac: float = 0.15
    seed: int = 7

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def vector(feats: Mapping[str, float], setup: str) -> list[float]:
    row = [float(feats.get(k, math.nan)) for k in FEATURES if not k.startswith("setup_")]
    return row + [1.0 if setup == s else 0.0 for s in SETUPS]


def build_dataset(
    history: Mapping[str, Sequence[Any]], p: SwingParams = SwingParams()
) -> list[dict]:
    """One row per fired setup: ``{symbol, date, setup, x, net, win, exit}``, by date."""
    rows = []
    for sym, candles in history.items():
        s = Series(candles)
        for i in range(WARMUP, len(s) - 1):
            st = s.setup(i, p)
            if st is None:
                continue
            out = simulate(s, i, st, p)
            if out is None:
                continue
            x = vector(s.features(i), st.name)
            if not all(math.isfinite(v) for v in x):
                continue
            rows.append(
                {
                    "symbol": sym,
                    "date": s.candles[i].start.date().isoformat(),
                    "setup": st.name,
                    "x": x,
                    "net": out["net"],
                    "win": out["net"] > 0,
                    "exit": out["exit"],
                    "held": out["held_days"],
                }
            )
    rows.sort(key=lambda r: (r["date"], r["symbol"]))
    return rows


def _split(rows: list[dict], cfg: SwingTrainConfig):
    dates = sorted({r["date"] for r in rows})
    if len(dates) < 10:
        raise ValueError(f"only {len(dates)} setup dates: fetch more history")
    a = dates[int(len(dates) * cfg.train_frac)]
    b = dates[int(len(dates) * (cfg.train_frac + cfg.val_frac))]
    tr = [r for r in rows if r["date"] < a]
    va = [r for r in rows if a <= r["date"] < b]
    te = [r for r in rows if r["date"] >= b]
    return tr, va, te


def _threshold(prob: np.ndarray, net: np.ndarray, min_trades: int) -> float:
    best, best_net = 0.5, -math.inf
    for t in (0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7):
        take = prob >= t
        if take.sum() >= min_trades and net[take].sum() > best_net:
            best, best_net = t, float(net[take].sum())
    return best


class SwingModel:
    """A trained (or loaded) swing model: ``prob(features, setup)``."""

    def __init__(
        self, model, pipeline, threshold: float, version: str = "", meta: Optional[dict] = None
    ):
        self.model, self.pipeline, self.threshold = model, pipeline, threshold
        self.version, self.meta = version, meta or {}

    def prob(self, feats: Mapping[str, float], setup: str) -> Optional[float]:
        x = np.array([vector(feats, setup)], float)
        if not np.isfinite(x).all():
            return None
        return float(self.model.predict(self.pipeline.transform(x))[0])

    def probs(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self.model.predict(self.pipeline.transform(X)), float)


def fit(train: list[dict], val: list[dict], cfg: SwingTrainConfig) -> SwingModel:
    from skopaq.adaptive.models import make
    from skopaq.adaptive.pipeline import Pipeline, PipelineConfig

    X = np.array([r["x"] for r in train], float)
    y = np.array([float(r["win"]) for r in train])
    if len(set(y)) < 2:
        raise ValueError("the training setups all won or all lost: nothing to learn")
    pipe = Pipeline(PipelineConfig(scaler="standard"))
    Z, (y,) = pipe.fit_transform(X, list(FEATURES), y)
    Xv = np.array([r["x"] for r in val], float) if val else None
    yv = np.array([float(r["win"]) for r in val]) if val else None
    m = make(cfg.model_kind, "classifier", {}, cfg.seed)
    m.fit(Z, y, None, pipe.transform(Xv) if Xv is not None else None, yv)
    model = SwingModel(m, pipe, 0.5)
    if val:
        model.threshold = _threshold(
            model.probs(Xv), np.array([r["net"] for r in val]), max(5, len(val) // 10)
        )
    return model


def importance(model: SwingModel, X: np.ndarray, y: np.ndarray, *, seed: int = 7,
               top: int = 12) -> list[dict]:
    """Permutation importance on the test rows: how much the Brier score worsens when one
    feature (the setup one-hots together) is shuffled. Model-agnostic; ``share`` sums to 1
    over the features that matter (≥ 0)."""
    if len(X) < 10:
        return []
    rng = np.random.default_rng(seed)
    base = float(np.mean((model.probs(X) - y) ** 2))
    groups: dict[str, list[int]] = {}
    for j, name in enumerate(FEATURES):
        groups.setdefault("setup" if name.startswith("setup_") else name, []).append(j)
    out = []
    for name, cols in groups.items():
        Xp = X.copy()
        perm = rng.permutation(len(X))
        Xp[:, cols] = X[perm][:, cols]
        out.append((name, float(np.mean((model.probs(Xp) - y) ** 2)) - base))
    total = sum(max(0.0, v) for _, v in out) or 1.0
    out.sort(key=lambda kv: -kv[1])
    return [{"feature": n, "brier_increase": round(v, 6), "share": round(max(0.0, v) / total, 4)}
            for n, v in out[:top]]


def evaluate(model: SwingModel, test: list[dict]) -> dict:
    if not test:
        return {"samples": 0}
    X = np.array([r["x"] for r in test], float)
    net = np.array([r["net"] for r in test])
    y = np.array([float(r["win"]) for r in test])
    p = model.probs(X)
    take = p >= model.threshold
    rate = float(y.mean())
    by_setup = {}
    for s in SETUPS:
        sel = np.array([r["setup"] == s for r in test])
        if sel.any():
            by_setup[s] = {
                "setups": int(sel.sum()),
                "taken": int((sel & take).sum()),
                "net_all": round(float(net[sel].sum()), 2),
                "net_taken": round(float(net[sel & take].sum()), 2),
            }
    return {
        "importance": importance(model, X, y),
        "samples": len(test),
        "baseline": trade_stats(list(net)).as_dict(),
        "model": trade_stats(list(net[take])).as_dict(),
        "brier": float(np.mean((p - y) ** 2)),
        "brier_base_rate": float(np.mean((rate - y) ** 2)),
        "base_rate": rate,
        "threshold": model.threshold,
        "by_setup": by_setup,
        "leakage": [],
        "first_test_date": test[0]["date"],
        "last_test_date": test[-1]["date"],
    }


# ── storage and registry ─────────────────────────────────────────────────────


def swing_registry(model_root: Path | str) -> ModelRegistry:
    return ModelRegistry(Path(model_root).expanduser() / "swing")


def save(model: SwingModel, directory: Path, *, name: str, extra: Mapping[str, Any]) -> Path:
    d = Path(directory) / name
    (d / "model").mkdir(parents=True)
    files = model.model.save(d / "model")
    artifacts.write_json_gz(d / "pipeline.json.gz", model.pipeline.to_dict())
    meta = {
        "kind": KIND,
        "features": list(FEATURES),
        "threshold": model.threshold,
        "model": model.model.meta(),
        "files": files,
        **extra,
    }
    (d / "swing.json").write_text(json.dumps(meta, indent=1, default=str))
    artifacts.write_manifest(d, version=name, extra={"format_kind": KIND})
    return d


def load(path: Path | str, version: str = "") -> SwingModel:
    from skopaq.adaptive.models import from_meta
    from skopaq.adaptive.pipeline import Pipeline

    path = Path(path)
    artifacts.verify(path)  # IntegrityError: not loaded
    meta = json.loads((path / "swing.json").read_text())
    if tuple(meta.get("features", ())) != FEATURES:
        raise ValueError(f"{path.name}: trained on other features")
    m = from_meta(meta["model"]).load(path / "model")
    pipe = Pipeline.from_dict(artifacts.read_json_gz(path / "pipeline.json.gz"))
    return SwingModel(m, pipe, float(meta["threshold"]), version or path.name, meta)


def train_and_register(
    history: Mapping[str, Sequence[Any]],
    cfg: SwingTrainConfig = SwingTrainConfig(),
    *,
    registry: Optional[ModelRegistry] = None,
) -> dict:
    rows = build_dataset(history, cfg.params)
    if len(rows) < 60:
        raise ValueError(f"only {len(rows)} historical setups: use more symbols or years")
    tr, va, te = _split(rows, cfg)
    model = fit(tr, va, cfg)
    report = evaluate(model, te)
    report.update(
        model_kind=cfg.model_kind,
        setups=len(rows),
        train=len(tr),
        validation=len(va),
        test=len(te),
        params=asdict(cfg.params),
    )
    out: dict[str, Any] = {"report": report, "model": model}
    if registry is None:
        return out
    now = datetime.now(timezone.utc)
    name = f"swing-{cfg.model_kind}-{now.strftime('%Y%m%dT%H%M%S%f')[:-3]}"
    dataset = {
        "symbols": sorted(history),
        "setups": len(rows),
        "first": rows[0]["date"],
        "last": rows[-1]["date"],
    }
    path = save(
        model,
        registry.dir / "artifacts",
        name=name,
        extra={"dataset": dataset, "trained_at": now.isoformat(), "params": asdict(cfg.params)},
    )
    entry = registry.register(
        model={"kind": KIND, "bundle": name, "path": str(path)},
        report=report,
        params={"threshold": model.threshold},
        dataset=dataset,
        strategy_id=f"swing.{cfg.model_kind}",
        notes=f"{cfg.model_kind} on {len(history)} symbols",
    )
    fails = registry.evaluate_candidate(entry["version"], gates=SWING_GATES)
    out.update(
        version=entry["version"],
        path=str(path),
        gate_failures=fails,
        state="candidate" if fails else "shadow",
    )
    return out


def load_active(config: Any, *, paper: bool) -> tuple[Optional[SwingModel], str]:
    """``(model, state)``: the champion, else (paper only) the shadow model."""
    from skopaq.learning.registry import model_dir

    reg = swing_registry(model_dir(config))
    entry, state = reg.champion(), "champion"
    if entry is None and paper:
        entry, state = reg.shadow(), "shadow"
    if entry is None:
        return None, "none"
    try:
        return load(entry["model"]["path"], entry["version"]), state
    except Exception as exc:
        logger.error("Swing model %s not loaded: %s", entry.get("version"), exc)
        return None, "unloadable"
