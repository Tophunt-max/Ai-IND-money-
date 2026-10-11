"""Drift monitoring of the champion model against recent live/paper signals.

``drift_now`` compares every feature's training sample (stored with the model at
training) with the features of the last ``days`` of QUALIFIED signals in the signal
journal (PSI, ``skopaq.learning.evaluation.psi``). Features above the alert level raise a
deduplicated WARNING with the action to take (retrain, or roll back). The daemon runs it
at the end of every session (REPORTING); the dashboard reads it on demand.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from skopaq.learning.evaluation import psi

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))


def drift_now(config: Any, days: int = 5, *, alert_at: float = 0.25,
              notify: bool = False) -> dict:
    from skopaq.learning.dataset import from_journal
    from skopaq.learning.registry import ModelRegistry, model_dir
    from skopaq.strategy.journal import SignalJournal

    champ = ModelRegistry(model_dir(config)).champion()
    if not champ:
        return {"available": False, "reason": "no champion model"}
    sample = (champ.get("dataset") or {}).get("feature_sample") or {}
    if not sample:
        return {"available": False, "reason": "the champion has no stored feature sample"}
    today = datetime.now(_IST).date()
    journal = SignalJournal.from_config(config)
    rows = journal.signals(sorted(today - timedelta(days=i) for i in range(days)))
    recent = from_journal(rows).examples
    out: dict[str, float] = {}
    for name, train_vals in sample.items():
        v = psi(train_vals, [e.features.get(name, float("nan")) for e in recent])
        if v is not None:
            out[name] = round(v, 4)
    alerts = sorted(k for k, v in out.items() if v > alert_at)
    if alerts and notify:
        from skopaq.execution.order_alerts import get_alerter

        get_alerter().alert(
            "WARNING", f"model-drift:{champ['version']}",
            f"Model {champ['version']}: feature drift (PSI > {alert_at}) on "
            f"{', '.join(alerts)} over {len(recent)} recent signals. Action: retrain "
            "(skopaq learn-train) or roll back on the dashboard.", dedup_s=86400)
    return {"available": True, "version": champ["version"], "psi": out, "alerts": alerts,
            "recent_signals": len(recent), "days": days, "alert_at": alert_at}
