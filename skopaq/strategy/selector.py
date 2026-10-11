"""Regime-adaptive strategy selection from out-of-sample evidence.

``build_regime_map(examples)`` groups held-out trade outcomes by (regime, strategy) and
allows a strategy in a regime only when it has ``min_trades`` net results whose 90 %
bootstrap interval of expectancy is above zero. The map is a learning artifact: it is
saved as a *candidate* (``RegimeMapStore.save_candidate``) and used only after an admin
approves it (``approve``). Without an approved map every configured strategy runs
(the rule system as before: the safe fallback); with one, a regime that no validated
strategy covers means the engine stays idle (NO_TRADE). ``rollback`` restores the
previous approved map. Every decision still passes the same risk gateway.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from skopaq.learning.evaluation import trade_stats
from skopaq.learning.registry import assert_no_safety_keys


def build_regime_map(examples: Iterable[Any], *, min_trades: int = 30) -> dict:
    """{"regimes": {trend: {strategy: {...stats, "allowed": bool}}}, "min_trades": n}."""
    groups: dict[tuple[str, str], list[float]] = {}
    for e in examples:
        if e.net_pnl is None:
            continue
        regime = (e.meta or {}).get("regime") or "unknown"
        groups.setdefault((regime, e.strategy_id), []).append(float(e.net_pnl))
    out: dict[str, dict] = {}
    for (regime, strategy), pnls in sorted(groups.items()):
        st = trade_stats(pnls)
        ci = st.expectancy_ci90
        allowed = st.trades >= min_trades and ci is not None and ci[0] > 0
        out.setdefault(regime, {})[strategy] = {**st.as_dict(), "allowed": allowed}
    return {"regimes": out, "min_trades": min_trades}


@dataclass
class RegimeMap:
    regimes: dict
    version: str = ""

    def allowed(self, strategy_id: str, regime: str) -> bool:
        row = (self.regimes.get(regime) or {}).get(strategy_id)
        return bool(row and row.get("allowed"))

    def strategies_for(self, regime: str, candidates: Iterable[str],
                       prefix: str = "scalp.") -> list[str]:
        return [n for n in candidates if self.allowed(f"{prefix}{n}", regime)]


class RegimeMapStore:
    def __init__(self, directory: Path | str,
                 clock=lambda: datetime.now(timezone.utc)) -> None:
        self.dir = Path(directory).expanduser()
        self._clock = clock

    def _write(self, name: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{name}.tmp"
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
        os.replace(tmp, self.dir / f"{name}.json")

    def _read(self, name: str) -> Optional[dict]:
        try:
            return json.loads((self.dir / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    def save_candidate(self, regime_map: dict, *, dataset: dict) -> dict:
        assert_no_safety_keys(regime_map)
        version = "regimes-" + self._clock().strftime("%Y%m%dT%H%M%S")
        entry = {"version": version, "created_at": self._clock().isoformat(),
                 "map": regime_map, "dataset": dataset}
        self._write("regime_map_candidate", entry)
        return entry

    def candidate(self) -> Optional[dict]:
        return self._read("regime_map_candidate")

    def approved(self) -> Optional[dict]:
        return self._read("regime_map")

    def approve(self, *, by: str, reason: str) -> dict:
        cand = self.candidate()
        if cand is None:
            raise ValueError("no candidate regime map to approve")
        if not by.strip():
            raise ValueError("approval needs a named person")
        previous = self.approved()
        entry = {**cand, "approved_by": by, "approved_at": self._clock().isoformat(),
                 "reason": reason, "previous": previous}
        self._write("regime_map", entry)
        return entry

    def rollback(self) -> Optional[dict]:
        current = self.approved()
        previous = (current or {}).get("previous")
        if previous:
            self._write("regime_map", previous)
        else:
            try:
                (self.dir / "regime_map.json").unlink()
            except OSError:
                pass
        return previous

    def load(self) -> Optional[RegimeMap]:
        entry = self.approved()
        if not entry:
            return None
        return RegimeMap(regimes=(entry.get("map") or {}).get("regimes") or {},
                         version=entry.get("version", ""))
