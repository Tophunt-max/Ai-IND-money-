"""Champion / challenger model registry with promotion gates and rollback.

Lifecycle of a model version (``~/.skopaq/models``, ``SKOPAQ_MODEL_DIR``):

``candidate`` (trained, evaluated out of sample) → ``shadow`` (gates passed: its
probability is recorded on live/paper signals but never decides) → ``champion`` (after
``SafetyRules.mandatory_paper_days_for_new_strategy`` days in shadow and a named human
approval, as ``human_approval_for_major_changes`` requires) → ``retired``.
``rollback`` makes the previous champion the champion again. Every transition is
appended to ``history.jsonl`` with its reason.

The registry stores model weights and strategy parameters only. ``assert_no_safety_keys``
refuses any artifact that names a hard safety limit, a broker permission or an order-state
rule: learning can never relax them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from skopaq.constants import SAFETY_RULES, SafetyRules

_DEFAULT_DIR = "~/.skopaq/models"
_PROTECTED_CONFIG = {
    "trading_mode", "max_shares_per_order", "max_lots_per_order", "max_consecutive_losses",
    "allow_sell_without_order_book", "trading_halted", "entry_lock_wait_seconds",
    "risk_per_trade_pct", "scalp_max_daily_loss_inr", "fno_max_daily_loss_inr",
    "scalp_max_position_value_inr", "fno_max_lots", "fno_max_premium_inr",
    "scalp_max_trades_per_day", "fno_max_trades_per_day", "scheduler_confirm_live",
}
PROTECTED_KEYS = frozenset({f.name for f in fields(SafetyRules)} | _PROTECTED_CONFIG)


class SafetyKeyError(ValueError):
    """A learning artifact tried to set a hard safety limit."""


def assert_no_safety_keys(params: Any, path: str = "") -> None:
    if isinstance(params, dict):
        for k, v in params.items():
            if str(k).lower() in PROTECTED_KEYS:
                raise SafetyKeyError(f"{path}{k} is a hard safety limit: learning may not set it")
            assert_no_safety_keys(v, f"{path}{k}.")
    elif isinstance(params, (list, tuple)):
        for i, v in enumerate(params):
            assert_no_safety_keys(v, f"{path}{i}.")


@dataclass(frozen=True)
class Gates:
    min_test_samples: int = 100
    min_gated_trades: int = 30
    require_beats_baseline: bool = True
    require_beats_champion: bool = True
    min_profit_factor: float = 1.1
    require_ci_above_zero: bool = True       # 90 % CI of expectancy must exclude ≤ 0
    max_drawdown_vs_baseline: float = 1.0    # gated max DD ≤ baseline's
    require_better_brier: bool = True
    shadow_days: int = SAFETY_RULES.mandatory_paper_days_for_new_strategy
    require_human_approval: bool = SAFETY_RULES.human_approval_for_major_changes


def check_gates(report: dict, gates: Gates, champion_report: Optional[dict] = None) -> list[str]:
    """Why the candidate may not leave ``candidate`` (empty: every gate passed)."""
    fails = []
    model = report.get("model") or {}
    base = report.get("baseline") or {}
    if report.get("leakage"):
        fails.append(f"leakage checks failed: {report['leakage'][:3]}")
    if report.get("samples", 0) < gates.min_test_samples:
        fails.append(f"test samples {report.get('samples', 0)} < {gates.min_test_samples}")
    if model.get("trades", 0) < gates.min_gated_trades:
        fails.append(f"gated test trades {model.get('trades', 0)} < {gates.min_gated_trades}")
    if gates.require_beats_baseline and model.get("net", 0) <= base.get("net", 0):
        fails.append(f"test net {model.get('net')} does not beat the baseline {base.get('net')}")
    pf = model.get("profit_factor")
    if pf is None or pf < gates.min_profit_factor:
        fails.append(f"profit factor {pf} < {gates.min_profit_factor}")
    ci = model.get("expectancy_ci90")
    if gates.require_ci_above_zero and (not ci or ci[0] <= 0):
        fails.append(f"expectancy 90% CI {ci} does not exclude zero")
    if base.get("max_drawdown") is not None and model.get("max_drawdown", 0) > \
            gates.max_drawdown_vs_baseline * max(base.get("max_drawdown", 0), 1e-9):
        fails.append(f"max drawdown {model.get('max_drawdown')} worse than the baseline "
                     f"{base.get('max_drawdown')}")
    if gates.require_better_brier and (report.get("brier") is None
                                       or report.get("brier_base_rate") is None
                                       or report["brier"] >= report["brier_base_rate"]):
        fails.append(f"Brier {report.get('brier')} not better than the base rate "
                     f"{report.get('brier_base_rate')}")
    if gates.require_beats_champion and champion_report:
        champ = (champion_report.get("model") or {}).get("net")
        if champ is not None and model.get("net", 0) <= champ:
            fails.append(f"test net {model.get('net')} does not beat the champion {champ}")
    return fails


def model_dir(config: object = None) -> Path:
    env = os.environ.get("SKOPAQ_MODEL_DIR", "").strip()
    value = getattr(config, "model_dir", None) if config is not None else None
    return Path(env or (value if isinstance(value, str) and value else _DEFAULT_DIR)).expanduser()


class ModelRegistry:
    def __init__(self, directory: Path | str,
                 clock=lambda: datetime.now(timezone.utc)) -> None:
        self.dir = Path(directory).expanduser()
        self._clock = clock
        self.mirror: Any = None          # skopaq.db.mirror.Mirror (optional)

    # ── storage ─────────────────────────────────────────────────────────

    def _path(self, version: str) -> Path:
        return self.dir / "versions" / f"{version}.json"

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
        os.replace(tmp, path)

    def _event(self, **line: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        line["ts"] = self._clock().isoformat()
        with open(self.dir / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
        if self.mirror is not None:
            try:
                self.mirror.put("model_events", {
                    "version": line.get("version"), "event": line.get("event"),
                    "by_user": line.get("approved_by") or line.get("by"),
                    "payload": json.loads(json.dumps(line, default=str))})
            except Exception:
                pass

    def get(self, version: str) -> Optional[dict]:
        try:
            return json.loads(self._path(version).read_text())
        except (OSError, ValueError):
            return None

    def versions(self) -> list[dict]:
        out = []
        for p in sorted((self.dir / "versions").glob("*.json")):
            try:
                d = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            d.pop("model", None)
            (d.get("dataset") or {}).pop("feature_sample", None)
            out.append(d)
        return sorted(out, key=lambda d: d.get("created_at", ""))

    def history(self, limit: int = 100) -> list[dict]:
        try:
            lines = (self.dir / "history.jsonl").read_text().splitlines()
        except OSError:
            return []
        out = []
        for raw in lines[-limit:]:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    def _pointer(self, name: str) -> Optional[str]:
        try:
            return json.loads((self.dir / f"{name}.json").read_text()).get("version")
        except (OSError, ValueError):
            return None

    def _set_pointer(self, name: str, version: Optional[str], **extra: Any) -> None:
        self._write_json(self.dir / f"{name}.json", {"version": version, **extra})

    def champion(self) -> Optional[dict]:
        v = self._pointer("champion")
        return self.get(v) if v else None

    def shadow(self) -> Optional[dict]:
        v = self._pointer("shadow")
        return self.get(v) if v else None

    # ── lifecycle ───────────────────────────────────────────────────────

    def register(self, *, model: dict, report: dict, params: dict, dataset: dict,
                 strategy_id: str, notes: str = "") -> dict:
        """Store a trained, evaluated candidate (never the champion directly)."""
        assert_no_safety_keys(params)
        assert_no_safety_keys(model)
        now = self._clock()
        version = f"{strategy_id.replace('.', '-')}-{now.strftime('%Y%m%dT%H%M%S')}"
        entry = {"version": version, "strategy_id": strategy_id, "state": "candidate",
                 "created_at": now.isoformat(), "report": report, "params": params,
                 "dataset": dataset, "model": model, "notes": notes,
                 "gate_failures": [], "shadow_since": None}
        self._write_json(self._path(version), entry)
        self._event(event="registered", version=version, strategy_id=strategy_id)
        return entry

    def evaluate_candidate(self, version: str, gates: Gates = Gates()) -> list[str]:
        """Gate a candidate; passing moves it to ``shadow`` (recorded, never deciding)."""
        entry = self.get(version)
        if entry is None:
            raise KeyError(version)
        champ = self.champion()
        fails = check_gates(entry["report"], gates, champ.get("report") if champ else None)
        entry["gate_failures"] = fails
        if not fails and entry["state"] == "candidate":
            entry["state"] = "shadow"
            entry["shadow_since"] = self._clock().isoformat()
            self._set_pointer("shadow", version)
            self._event(event="shadow", version=version, reason="all gates passed")
        else:
            self._event(event="gates_failed", version=version, failures=fails)
        self._write_json(self._path(version), entry)
        return fails

    def promote(self, version: str, *, approved_by: str, reason: str,
                gates: Gates = Gates()) -> dict:
        """Shadow → champion after the shadow period and a named human approval."""
        entry = self.get(version)
        if entry is None:
            raise KeyError(version)
        if entry["state"] != "shadow":
            raise ValueError(f"{version} is {entry['state']}: only a shadow model can be "
                             "promoted (pass the gates first)")
        since = datetime.fromisoformat(entry["shadow_since"])
        if self._clock() - since < timedelta(days=gates.shadow_days):
            raise ValueError(f"{version} has been in shadow since {since.date()}: "
                             f"{gates.shadow_days} days of paper evaluation are required")
        if gates.require_human_approval and not approved_by.strip():
            raise ValueError("promotion needs a named human approval")
        previous = self._pointer("champion")
        if previous:
            old = self.get(previous)
            if old:
                old["state"] = "retired"
                self._write_json(self._path(previous), old)
        entry["state"] = "champion"
        entry["promoted_at"] = self._clock().isoformat()
        entry["approved_by"] = approved_by
        entry["rollback_to"] = previous
        self._write_json(self._path(version), entry)
        self._set_pointer("champion", version, previous=previous)
        if self._pointer("shadow") == version:
            self._set_pointer("shadow", None)
        self._event(event="promoted", version=version, previous=previous,
                    approved_by=approved_by, reason=reason)
        return entry

    def rollback(self, *, by: str, reason: str) -> Optional[str]:
        """The previous champion is champion again (None: nothing to roll back to)."""
        current = self._pointer("champion")
        entry = self.get(current) if current else None
        previous = entry.get("rollback_to") if entry else None
        if entry:
            entry["state"] = "retired"
            self._write_json(self._path(current), entry)
        if previous:
            old = self.get(previous)
            if old:
                old["state"] = "champion"
                self._write_json(self._path(previous), old)
        self._set_pointer("champion", previous)
        self._event(event="rollback", version=current, to=previous, by=by, reason=reason)
        return previous
