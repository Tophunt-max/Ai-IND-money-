"""Best-effort mirror of the journals to Supabase (``supabase/migrations/005``).

The local journals decide; the mirror is the durable, queryable audit trail across hosts.
Rows go through a bounded queue to one background thread, so a slow or failed database
never delays an order or an exit (a full queue drops the oldest row with a WARNING).
Enabled when ``SKOPAQ_SUPABASE_URL`` and the service key are set and
``SKOPAQ_AUDIT_MIRROR_ENABLED`` is not false.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX = 5000
_mirrors: dict[str, "Mirror"] = {}
_lock = threading.Lock()


class Mirror:
    def __init__(self, url: str, key: str) -> None:
        self._url, self._key = url, key
        self._q: "queue.Queue[tuple[str, dict, Optional[str]]]" = queue.Queue(maxsize=_MAX)
        self._client = None
        self._thread = threading.Thread(target=self._run, name="audit-mirror", daemon=True)
        self._thread.start()
        self.dropped = 0

    def put(self, table: str, row: dict, on_conflict: Optional[str] = None) -> None:
        try:
            self._q.put_nowait((table, row, on_conflict))
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            if self.dropped in (1, 100, 1000):
                logger.warning("Audit mirror queue full: %d row(s) dropped", self.dropped)
            self._q.put_nowait((table, row, on_conflict))

    def _run(self) -> None:
        while True:
            table, row, conflict = self._q.get()
            try:
                if self._client is None:
                    from supabase import create_client

                    self._client = create_client(self._url, self._key)
                q = self._client.table(table)
                q = (q.upsert(row, on_conflict=conflict, ignore_duplicates=True)
                     if conflict else q.insert(row))
                q.execute()
            except Exception:
                logger.debug("Audit mirror write to %s failed", table, exc_info=True)


def mirror_from_config(config: Any) -> Optional[Mirror]:
    if getattr(config, "audit_mirror_enabled", True) is False:
        return None
    url = getattr(config, "supabase_url", "")
    key = getattr(config, "supabase_service_key", None)
    key = key.get_secret_value() if hasattr(key, "get_secret_value") else ""
    if not isinstance(url, str) or not url or not key:
        return None
    with _lock:
        if url not in _mirrors:
            _mirrors[url] = Mirror(url, key)
        return _mirrors[url]


def signal_row(line: dict) -> dict:
    return {"signal_id": line.get("signal_id"), "event": line.get("event"),
            "mode": line.get("mode"), "strategy_id": line.get("strategy_id"),
            "symbol": line.get("symbol"), "status": line.get("status"), "payload": line}


def order_row(line: dict) -> dict:
    from skopaq.execution.order_lifecycle import state_of_line

    state = state_of_line(line)

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {"internal_id": line.get("internal_id") or None,
            "order_id": line.get("order_id") or None, "event": line.get("event"),
            "lifecycle_state": state.value if state else None, "symbol": line.get("symbol"),
            "side": line.get("side"), "qty": num(line.get("qty")),
            "filled": num(line.get("filled")), "payload": line}
