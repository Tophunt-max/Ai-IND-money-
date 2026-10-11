"""Hyperparameter search for the scalping strategies (Optuna TPE, fixed seed).

``hyperopt`` searches the strategy parameters on the first ``select_frac`` of the days
(each trial a full backtest with charges and slippage, scored on net P&L minus a
drawdown penalty, trials with fewer than ``min_trades`` trades rejected), then compares
the best parameters with the current ones on the held-out days they never saw. The
result is a *candidate* (``ParamsStore``); the scalper uses parameters only after an admin
approves them, and ``rollback`` restores the previous ones. Risk limits are not in the
search space and cannot be set this way (``assert_no_safety_keys``).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from skopaq.learning.registry import assert_no_safety_keys

# name: (low, high, kind)
SPACE = {
    "rr": (1.0, 3.0, "float"),
    "orb_minutes": (10, 45, "int"),
    "range_lookback": (15, 60, "int"),
    "vwap_band_pct": (0.0005, 0.004, "float"),
    "min_reward_to_cost": (1.5, 5.0, "float"),
    "max_hold_minutes": (10, 90, "int"),
}


def apply_params(settings: Any, params: dict) -> Any:
    """``ScalpSettings`` with ``params`` (clamped to the search space) applied."""
    from datetime import timedelta

    clean = {}
    for k, v in params.items():
        if k in SPACE and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi, kind = SPACE[k]
            v = min(hi, max(lo, v))
            clean[k] = int(round(v)) if kind == "int" else float(v)
    sp = settings.params
    sp = replace(sp, **{k: clean[k] for k in ("rr", "orb_minutes", "range_lookback",
                                              "vwap_band_pct") if k in clean})
    kw: dict = {"params": sp}
    if "min_reward_to_cost" in clean:
        kw["min_reward_to_cost"] = clean["min_reward_to_cost"]
    if "max_hold_minutes" in clean:
        kw["max_hold"] = timedelta(minutes=clean["max_hold_minutes"])
    return replace(settings, **kw)


def current_params(settings: Any) -> dict:
    p = settings.params
    return {"rr": p.rr, "orb_minutes": p.orb_minutes, "range_lookback": p.range_lookback,
            "vwap_band_pct": p.vwap_band_pct, "min_reward_to_cost": settings.min_reward_to_cost,
            "max_hold_minutes": int(settings.max_hold.total_seconds() // 60)}


def _score(days_by_symbol: dict, settings: Any, slippage: float, dd_weight: float) -> dict:
    from skopaq.scalping.backtest import simulate

    trades = []
    for sym, days in days_by_symbol.items():
        trades += simulate(sym, days, settings, slippage_pct=slippage).trades
    trades.sort(key=lambda t: (t.day, t.opened))
    eq = peak = dd = 0.0
    for t in trades:
        eq += t.pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    net = sum(t.pnl for t in trades)
    return {"trades": len(trades), "net": round(net, 2), "max_drawdown": round(dd, 2),
            "score": net - dd_weight * dd}


def hyperopt(days_by_symbol: dict[str, Sequence[Sequence[Any]]], settings: Any, *,
             trials: int = 60, select_frac: float = 0.7, slippage_pct: float = 0.0002,
             min_trades: int = 20, dd_weight: float = 0.5, seed: int = 7) -> dict:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    select, hold = {}, {}
    for sym, days in days_by_symbol.items():
        days = [d for d in days if d]
        n = max(1, int(len(days) * select_frac))
        select[sym], hold[sym] = days[:n], days[n:]

    def objective(trial):
        p = {}
        for k, (lo, hi, kind) in SPACE.items():
            p[k] = (trial.suggest_int(k, lo, hi) if kind == "int"
                    else trial.suggest_float(k, lo, hi))
        r = _score(select, apply_params(settings, p), slippage_pct, dd_weight)
        trial.set_user_attr("result", r)
        return r["score"] if r["trades"] >= min_trades else -1e12

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=trials, show_progress_bar=False)
    best = study.best_trial
    now = current_params(settings)
    out = {
        "trials": trials, "best_params": best.params,
        "selection": best.user_attrs.get("result"),
        "current_params": now,
        "holdout_current": _score(hold, settings, slippage_pct, dd_weight),
        "holdout_best": _score(hold, apply_params(settings, best.params), slippage_pct,
                               dd_weight),
        "holdout_days": sum(len(d) for d in hold.values()),
    }
    hc, hb = out["holdout_current"], out["holdout_best"]
    out["better_on_holdout"] = bool(hb["trades"] >= max(5, min_trades // 4)
                                    and hb["score"] > hc["score"])
    return out


class ParamsStore:
    """Candidate / approved scalper parameters (``<model_dir>/strategy_params*.json``)."""

    def __init__(self, directory: Path | str, clock=lambda: datetime.now(timezone.utc)) -> None:
        self.dir = Path(directory).expanduser()
        self._clock = clock

    def _write(self, name: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{name}.tmp"
        tmp.write_text(json.dumps(data, indent=1, default=str))
        os.replace(tmp, self.dir / f"{name}.json")

    def _read(self, name: str) -> Optional[dict]:
        try:
            return json.loads((self.dir / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    def save_candidate(self, result: dict) -> dict:
        assert_no_safety_keys(result.get("best_params") or {})
        entry = {"version": "params-" + self._clock().strftime("%Y%m%dT%H%M%S"),
                 "created_at": self._clock().isoformat(), **result}
        self._write("strategy_params_candidate", entry)
        return entry

    def candidate(self) -> Optional[dict]:
        return self._read("strategy_params_candidate")

    def approved(self) -> Optional[dict]:
        return self._read("strategy_params")

    def approve(self, *, by: str, reason: str) -> dict:
        cand = self.candidate()
        if cand is None:
            raise ValueError("no candidate parameters to approve")
        if not cand.get("better_on_holdout"):
            raise ValueError("the candidate did not beat the current parameters on the "
                             "holdout: not approvable")
        if not by.strip():
            raise ValueError("approval needs a named person")
        entry = {**cand, "approved_by": by, "approved_at": self._clock().isoformat(),
                 "reason": reason, "previous": self.approved()}
        self._write("strategy_params", entry)
        return entry

    def rollback(self) -> Optional[dict]:
        cur = self.approved()
        prev = (cur or {}).get("previous")
        if prev:
            self._write("strategy_params", prev)
        else:
            try:
                (self.dir / "strategy_params.json").unlink()
            except OSError:
                pass
        return prev

    def params(self) -> Optional[dict]:
        a = self.approved()
        return dict(a["best_params"]) if a and a.get("best_params") else None
