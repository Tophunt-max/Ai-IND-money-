"""Adaptive retraining: fetch history → build the dataset → train → rolling backtest →
register → gate. FreqAI retrains and uses the new model at once; here a retrained model
is only ever a *candidate*, and becomes *shadow* (recorded on signals, never deciding)
when it passes the promotion gates. Champion stays an admin's decision after the shadow
period, and ``rollback`` restores the previous champion.

``retrain_due`` / the state file (``<model_dir>/adaptive_state.json``) let the daemon
retrain at the end of a session once ``adaptive_retrain_hours`` have passed
(``SKOPAQ_ADAPTIVE_AUTO_RETRAIN``); ``skopaq adaptive-train`` runs it by hand.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from skopaq.adaptive.engine import (
    AdaptiveConfig,
    build_forward_dataset,
    build_setup_dataset,
    rolling_backtest,
    train_adaptive,
)
from skopaq.adaptive.features import FeatureConfig

logger = logging.getLogger(__name__)


def config_from(config: Any) -> AdaptiveConfig:
    g = lambda n, d: getattr(config, n, d)  # noqa: E731
    corr = tuple(s.strip().upper() for s in str(g("adaptive_correlated", "") or "").split(",")
                 if s.strip())
    return AdaptiveConfig(
        features=FeatureConfig(correlated=corr),
        model_kind=str(g("adaptive_model_kind", "lightgbm") or "lightgbm"),
        target=str(g("adaptive_target", "forward") or "forward"),
        horizon_bars=int(g("adaptive_horizon_bars", 15) or 15),
        train_days=int(g("adaptive_train_days", 20) or 20),
        retrain_days=int(g("adaptive_retrain_days", 5) or 5),
        di_threshold=float(g("adaptive_di_threshold", 1.5) or 1.5),
    )


def symbols_from(config: Any) -> list[str]:
    raw = getattr(config, "adaptive_symbols", "") or getattr(config, "scalp_symbols", "") or ""
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()][:20]


def _state_path(model_dir: Path) -> Path:
    return Path(model_dir).expanduser() / "adaptive_state.json"


def read_state(model_dir: Path) -> dict:
    try:
        return json.loads(_state_path(model_dir).read_text())
    except (OSError, ValueError):
        return {}


def _write_state(model_dir: Path, state: dict) -> None:
    p = _state_path(model_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    os.replace(tmp, p)


def retrain_due(config: Any, model_dir: Path, now: Optional[datetime] = None) -> bool:
    if getattr(config, "adaptive_auto_retrain", False) is not True:
        return False
    last = read_state(model_dir).get("finished_at")
    if not last:
        return True
    now = now or datetime.now(timezone.utc)
    hours = float(getattr(config, "adaptive_retrain_hours", 24.0) or 24.0)
    try:
        return now - datetime.fromisoformat(last) >= timedelta(hours=hours)
    except ValueError:
        return True


def train_and_register(days_by_symbol: Mapping[str, Sequence[Sequence[Any]]], cfg: AdaptiveConfig,
                       registry: Any, *, correlated: Optional[Mapping] = None,
                       settings: Any = None) -> dict:
    """Build the dataset, train, backtest with rolling retraining, register, gate."""
    if cfg.target == "setup":
        if settings is None:
            raise ValueError("the setup target needs the scalper settings")
        examples = []
        for sym, days in days_by_symbol.items():
            examples += build_setup_dataset(sym, days, settings, cfg, correlated)
    else:
        examples = build_forward_dataset(days_by_symbol, cfg, correlated)
    model, report = train_adaptive(examples, cfg)
    report["rolling"] = rolling_backtest(examples, cfg)
    labelled = [e for e in examples if e.label is not None]
    dataset = {"rows": len(labelled), "symbols": sorted(days_by_symbol),
               "correlated": list(cfg.features.correlated), "target": cfg.target,
               "first": min(e.decided_at for e in labelled).isoformat(),
               "last": max(e.decided_at for e in labelled).isoformat(),
               "features": len(model.names)}
    entry = registry.register(model=model.to_dict(), report=report,
                              params={"threshold": model.threshold}, dataset=dataset,
                              strategy_id=f"adaptive.{cfg.target}",
                              notes=f"{cfg.resolved_kind()} on {len(model.names)} features")
    entry["gate_failures"] = registry.evaluate_candidate(entry["version"])
    return registry.get(entry["version"]) or entry


async def run_retrain(config: Any, client: Any, registry: Any, *,
                      now: Optional[datetime] = None) -> dict:
    """Fetch the history from the broker and retrain (never raises; the result is saved
    in the state file and returned)."""
    from skopaq.scalping.backtest import fetch_days

    cfg = config_from(config)
    started = (now or datetime.now(timezone.utc)).isoformat()
    state: dict = {"started_at": started, "config": cfg.as_dict()}
    try:
        days = {}
        for sym in symbols_from(config):
            try:
                days[sym] = await fetch_days(client, sym, int(getattr(config, "adaptive_days",
                                                                      30) or 30))
            except Exception as exc:
                logger.warning("Adaptive: no history for %s (%s)", sym, exc)
        corr = {}
        for sym in cfg.features.correlated:
            try:
                corr[sym] = await fetch_days(client, sym, int(getattr(config, "adaptive_days",
                                                                      30) or 30))
            except Exception as exc:
                logger.warning("Adaptive: no history for correlated %s (%s)", sym, exc)
        if not any(days.values()):
            raise ValueError("no candle history fetched")
        settings = None
        if cfg.target == "setup":
            from skopaq.scalping.settings import ScalpSettings

            settings = ScalpSettings.from_config(config)
        entry = train_and_register(days, cfg, registry, correlated=corr, settings=settings)
        state.update(ok=True, version=entry["version"], state=entry["state"],
                     gate_failures=entry.get("gate_failures", []),
                     rolling=(entry.get("report") or {}).get("rolling", {}).get("model"))
    except Exception as exc:
        logger.warning("Adaptive retrain failed: %s", exc)
        state.update(ok=False, error=str(exc)[:300])
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(registry.dir, state)
    return state
