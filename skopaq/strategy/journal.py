"""Durable signal journal: every candidate and its decision, one JSON line each.

``~/.skopaq/signals/YYYY-MM-DD.jsonl`` (``SKOPAQ_SIGNAL_JOURNAL_DIR``) on the shared home
volume, like the order journal. Each line holds the point-in-time evidence (indicators,
patterns, regime, data quality) the decision saw, the decision with its reasons, and,
later, the outcome (``outcome`` lines naming the ``signal_id``). It is the learning
dataset's source: nothing on a decision line was computed after ``decided_at``.

Writes are best-effort (a full disk must never stop protective exits); a failed write is
logged once per process.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from skopaq.strategy.signal import Decision, SignalCandidate

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_DEFAULT_DIR = "~/.skopaq/signals"
_lock = threading.Lock()
_warned = False


def journal_dir(config: object = None) -> Path:
    env = os.environ.get("SKOPAQ_SIGNAL_JOURNAL_DIR", "").strip()
    value = getattr(config, "signal_journal_dir", None) if config is not None else None
    configured = value.strip() if isinstance(value, str) else ""
    return Path(env or configured or _DEFAULT_DIR).expanduser()


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(v) for v in value]
    if isinstance(value, float) and value != value:     # NaN
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class SignalJournal:
    def __init__(self, directory: Path | str, *, mode: str = "paper") -> None:
        self.directory = Path(directory).expanduser()
        self.mode = mode

    @classmethod
    def from_config(cls, config: object) -> "SignalJournal":
        return cls(journal_dir(config), mode=str(getattr(config, "trading_mode", "paper")))

    def path_for(self, day: date) -> Path:
        return self.directory / f"{day.isoformat()}.jsonl"

    def _write(self, line: dict, day: date) -> bool:
        global _warned
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            text = json.dumps(_json(line), separators=(",", ":"))
            with _lock, open(self.path_for(day), "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
            return True
        except Exception as exc:
            if not _warned:
                logger.warning("Signal journal not written (%s): %s", self.directory, exc)
                _warned = True
            return False

    def record_decision(self, c: SignalCandidate, d: Decision) -> bool:
        line = {
            "event": "decision", "ts": datetime.now(_IST).isoformat(), "mode": self.mode,
            "signal_id": c.signal_id, "strategy_id": c.strategy_id,
            "strategy_version": c.strategy_version, "model_version": c.model_version,
            "model_prob": c.model_prob, "symbol": c.symbol, "product": c.product,
            "direction": c.direction, "decided_at": c.decided_at,
            "timeframe_s": c.timeframe_s, "entry": c.entry, "stop": c.stop,
            "target": c.target, "qty": c.qty, "reason": c.reason,
            "invalidation": c.invalidation, "holding_minutes": c.holding_minutes,
            "expires_at": c.expires_at, "confidence": c.confidence,
            "evidence": c.evidence, **d.as_dict(),
        }
        return self._write(line, c.decided_at.astimezone(_IST).date())

    def record_execution(self, signal_id: str, *, status: str, filled: float = 0,
                         fill_price: Optional[float] = None, reason: str = "",
                         day: Optional[date] = None) -> bool:
        return self._write({"event": "execution", "ts": datetime.now(_IST).isoformat(),
                            "signal_id": signal_id, "status": status, "filled": filled,
                            "fill_price": fill_price, "reason": reason},
                           day or datetime.now(_IST).date())

    def record_outcome(self, signal_id: str, *, exit_price: float, net_pnl: float,
                       gross_pnl: float, charges: float, exit_reason: str,
                       closed_at: Optional[datetime] = None, mae: Optional[float] = None,
                       mfe: Optional[float] = None, day: Optional[date] = None) -> bool:
        closed_at = closed_at or datetime.now(_IST)
        return self._write({"event": "outcome", "ts": datetime.now(_IST).isoformat(),
                            "signal_id": signal_id, "exit_price": exit_price,
                            "net_pnl": net_pnl, "gross_pnl": gross_pnl, "charges": charges,
                            "exit_reason": exit_reason, "closed_at": closed_at,
                            "mae": mae, "mfe": mfe},
                           day or closed_at.astimezone(_IST).date())

    def lines(self, day: Optional[date] = None) -> list[dict]:
        path = self.path_for(day or datetime.now(_IST).date())
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for text in raw.splitlines():
            try:
                item = json.loads(text)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def days(self) -> list[date]:
        try:
            names = sorted(p.stem for p in self.directory.glob("*.jsonl"))
        except OSError:
            return []
        out = []
        for n in names:
            try:
                out.append(date.fromisoformat(n))
            except ValueError:
                continue
        return out

    def signals(self, days: Optional[Iterable[date]] = None) -> list[dict]:
        """Decision lines merged with their execution and outcome lines (by signal id)."""
        merged: dict[str, dict] = {}
        order: list[str] = []
        for day in (days if days is not None else self.days()):
            for line in self.lines(day):
                sid = line.get("signal_id")
                if not isinstance(sid, str):
                    continue
                ev = line.get("event")
                if ev == "decision":
                    if sid not in merged:
                        order.append(sid)
                    merged[sid] = {**line, "execution": None, "outcome": None}
                elif sid in merged and ev in ("execution", "outcome"):
                    merged[sid][ev] = line
        return [merged[s] for s in order]
