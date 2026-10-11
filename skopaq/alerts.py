"""Price alerts and the dashboard's notification feed.

An alert fires once when a symbol's price crosses its level (``above`` / ``below``) or its
day change crosses a percentage (``pct_up`` / ``pct_down``). Whoever sees the quote first
fires it — the dashboard's live price stream (``skopaq/api/dashboard_market.py``) or the
Telegram bot's minute job — and the file lock makes sure it fires only once. A fired alert
becomes inactive, lands in the notification feed and goes to Telegram (best effort).

Alerts never place orders. State lives in one JSON file (``SKOPAQ_ALERTS_FILE``, on the
home volume every compose container shares), written atomically under a lock file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping, Optional

logger = logging.getLogger(__name__)

Op = Literal["above", "below", "pct_up", "pct_down"]
OPS: tuple[str, ...] = ("above", "below", "pct_up", "pct_down")
MAX_ALERTS = 200
MAX_FEED = 100
_SYMBOL = re.compile(r"^\^?[A-Z0-9&\-.]{1,24}$")


@dataclass
class Alert:
    id: str
    symbol: str
    op: str
    value: float
    note: str = ""
    created_at: str = ""
    created_by: str = ""
    active: bool = True
    triggered_at: Optional[str] = None
    triggered_price: Optional[float] = None

    def describe(self) -> str:
        if self.op == "above":
            return f"{self.symbol} at or above {self.value:,.2f}"
        if self.op == "below":
            return f"{self.symbol} at or below {self.value:,.2f}"
        if self.op == "pct_up":
            return f"{self.symbol} up {self.value:.2f}% or more today"
        return f"{self.symbol} down {self.value:.2f}% or more today"

    def hit(self, quote: Mapping[str, Any]) -> bool:
        ltp = quote.get("ltp")
        if not isinstance(ltp, (int, float)) or ltp <= 0:
            return False
        if self.op == "above":
            return ltp >= self.value
        if self.op == "below":
            return ltp <= self.value
        pct = quote.get("change_pct")
        if not isinstance(pct, (int, float)):
            return False
        pct *= 100  # quotes carry fractions (0.0123 = 1.23 %)
        return pct >= self.value if self.op == "pct_up" else pct <= -abs(self.value)


@dataclass
class Notification:
    id: str
    kind: str
    title: str
    body: str
    at: str
    symbol: str = ""
    read: bool = False


@dataclass
class _State:
    alerts: list[Alert] = field(default_factory=list)
    feed: list[Notification] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate(symbol: str, op: str, value: float) -> tuple[str, str, float]:
    sym = (symbol or "").strip().upper()
    if not _SYMBOL.match(sym):
        raise ValueError("Give an NSE symbol, e.g. RELIANCE")
    if op not in OPS:
        raise ValueError(f"op must be one of {', '.join(OPS)}")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError("value must be a number") from None
    if not v > 0 or v != v:
        raise ValueError("value must be above 0")
    if op.startswith("pct") and v > 50:
        raise ValueError("a day-change alert must be 50% or less")
    return sym, op, round(v, 4)


class AlertStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    @classmethod
    def from_config(cls, config: Any = None) -> "AlertStore":
        value = getattr(config, "alerts_file", None) if config is not None else None
        env = os.environ.get("SKOPAQ_ALERTS_FILE", "").strip()
        return cls(env or (value if isinstance(value, str) and value else "~/.skopaq/alerts.json"))

    # ── storage ─────────────────────────────────────────────────────────
    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        with open(lock, "a+") as fh:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - not on Linux / macOS
                pass
            try:
                yield
            finally:
                with contextlib.suppress(Exception):
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read(self) -> _State:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return _State()
        except (OSError, ValueError):
            logger.warning("Alerts file %s unreadable: starting empty", self.path, exc_info=True)
            return _State()
        alerts = []
        for a in raw.get("alerts") or []:
            with contextlib.suppress(TypeError):
                alerts.append(Alert(**a))
        feed = []
        for n in raw.get("feed") or []:
            with contextlib.suppress(TypeError):
                feed.append(Notification(**n))
        return _State(alerts, feed)

    def _write(self, state: _State) -> None:
        tmp = self.path.with_suffix(".tmp")
        data = {"alerts": [asdict(a) for a in state.alerts],
                "feed": [asdict(n) for n in state.feed[:MAX_FEED]]}
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    # ── alerts ──────────────────────────────────────────────────────────
    def alerts(self) -> list[Alert]:
        with self._locked():
            return self._read().alerts

    def active_symbols(self) -> set[str]:
        return {a.symbol for a in self.alerts() if a.active}

    def add(self, symbol: str, op: str, value: float, *, note: str = "",
            by: str = "") -> Alert:
        sym, op, v = validate(symbol, op, value)
        with self._locked():
            st = self._read()
            if len(st.alerts) >= MAX_ALERTS:
                raise ValueError(f"at most {MAX_ALERTS} alerts: delete old ones first")
            a = Alert(uuid.uuid4().hex[:10], sym, op, v, note.strip()[:200], _now(), by)
            st.alerts.insert(0, a)
            self._write(st)
            return a

    def delete(self, alert_id: str) -> bool:
        with self._locked():
            st = self._read()
            keep = [a for a in st.alerts if a.id != alert_id]
            if len(keep) == len(st.alerts):
                return False
            st.alerts = keep
            self._write(st)
            return True

    def rearm(self, alert_id: str) -> Optional[Alert]:
        with self._locked():
            st = self._read()
            for a in st.alerts:
                if a.id == alert_id:
                    a.active, a.triggered_at, a.triggered_price = True, None, None
                    self._write(st)
                    return a
        return None

    def check(self, quotes: Mapping[str, Mapping[str, Any]]) -> list[Alert]:
        """Fire every active alert *quotes* meet (once); returns the alerts fired now."""
        if not quotes:
            return []
        fired: list[Alert] = []
        with self._locked():
            st = self._read()
            for a in st.alerts:
                q = quotes.get(a.symbol)
                if not a.active or q is None or not a.hit(q):
                    continue
                a.active = False
                a.triggered_at = _now()
                a.triggered_price = float(q["ltp"])
                fired.append(a)
                st.feed.insert(0, Notification(
                    uuid.uuid4().hex[:10], "alert", f"Alert: {a.symbol}",
                    f"{a.describe()} — now {a.triggered_price:,.2f}"
                    + (f" · {a.note}" if a.note else ""), a.triggered_at, a.symbol))
            if fired:
                self._write(st)
        return fired

    # ── notification feed ───────────────────────────────────────────────
    def feed(self, limit: int = 50) -> list[Notification]:
        with self._locked():
            return self._read().feed[: max(1, min(limit, MAX_FEED))]

    def push(self, kind: str, title: str, body: str, symbol: str = "") -> Notification:
        n = Notification(uuid.uuid4().hex[:10], kind, title[:120], body[:500], _now(), symbol)
        with self._locked():
            st = self._read()
            st.feed.insert(0, n)
            self._write(st)
        return n

    def mark_read(self, ids: Optional[list[str]] = None) -> int:
        with self._locked():
            st = self._read()
            n = 0
            for item in st.feed:
                if not item.read and (ids is None or item.id in ids):
                    item.read = True
                    n += 1
            if n:
                self._write(st)
            return n


async def notify_fired(fired: list[Alert]) -> None:
    """Telegram (and the log) for alerts that just fired; never raises."""
    if not fired:
        return
    try:
        from skopaq.notifications import notify

        for a in fired:
            await notify(f"🔔 Price alert: {a.describe()} — now Rs {a.triggered_price:,.2f}"
                         + (f"\n{a.note}" if a.note else ""))
    except Exception:
        logger.warning("Alert notification failed", exc_info=True)


async def check_now(store: Optional[AlertStore] = None) -> list[Alert]:
    """Quote every symbol with an active alert and fire what is due (the bot's job)."""
    store = store or AlertStore.from_config(_config())
    symbols = sorted(store.active_symbols())
    if not symbols:
        return []
    from skopaq.broker import live_quotes

    quotes, _ = await live_quotes.get_quotes(symbols[:50])
    fired = store.check(quotes)
    await notify_fired(fired)
    return fired


def _config():
    with contextlib.suppress(Exception):
        from skopaq.config import SkopaqConfig

        return SkopaqConfig()
    return None

