"""Control channel between the dashboard API and the trading processes.

The API, the scheduler and the daemon (or ``skopaq monitor``) are separate processes
(compose services) that share the home volume, so they talk through files in
``control_dir`` (``~/.skopaq/control``):

- ``session.json`` / ``monitor.json``: status the daemon and the monitor write every few
  seconds (phase, positions with LTP, P&L, stop and target). The API streams them.
- ``stop.request``: the running daemon / monitor sets its stop event (the daemon then
  goes to CLOSING and sells what it holds, as on a SIGTERM). Only a request made after
  the process started, and less than ``REQUEST_TTL_S`` old, is honoured.
- ``start.request``: the scheduler launches today's session now (when it is idle).
- ``commands/<id>.json``: work for the running monitor (close a position, close all,
  change a stop or target, place an order). The monitor claims one by renaming it into
  ``claimed/`` (one process only), does it, and writes ``results/<id>.json``.

Every write is atomic (a temp file renamed over the old one); reads never raise.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

REQUEST_TTL_S = 300.0      # a stop/start request older than this is ignored
COMMAND_TTL_S = 120.0      # a command nobody claimed within this is expired
RESULT_KEEP_S = 3600.0
COMMAND_KINDS = ("close", "close_all", "set_plan", "order")
STATUS_NAMES = ("session", "monitor", "scalper")
COMMAND_TARGETS = ("monitor", "scalper")


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, default=str)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class ControlChannel:
    """The files of ``directory``; ``clock`` is wall time (``time.time``)."""

    def __init__(self, directory: str | os.PathLike, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(os.path.expanduser(str(directory)))
        self._clock = clock

    @classmethod
    def from_config(cls, config: Any) -> Optional["ControlChannel"]:
        directory = getattr(config, "control_dir", None)
        if not isinstance(directory, str) or not directory.strip():
            return None
        return cls(directory)

    # ── Status ───────────────────────────────────────────────────────────

    def write_status(self, name: str, data: dict) -> None:
        """Publish ``name`` (session / monitor) status; failures are logged only."""
        if name not in STATUS_NAMES:
            raise ValueError(f"unknown status {name!r}")
        try:
            _atomic_write(self.dir / f"{name}.json",
                          {**data, "pid": os.getpid(), "updated_at": self._clock()})
        except OSError:
            logger.warning("Control status %s not written (%s)", name, self.dir, exc_info=True)

    def read_status(self, name: str) -> Optional[dict]:
        """The status with ``age_s`` (seconds since written), or None."""
        data = _read(self.dir / f"{name}.json")
        if data is None:
            return None
        updated = data.get("updated_at")
        if isinstance(updated, (int, float)):
            data["age_s"] = max(0.0, self._clock() - float(updated))
        return data

    def fresh_status(self, name: str, max_age_s: float) -> Optional[dict]:
        data = self.read_status(name)
        if data is None or data.get("age_s", float("inf")) > max_age_s or data.get("ended"):
            return None
        return data

    # ── Stop / start requests ────────────────────────────────────────────

    def _request(self, name: str, by: str, reason: str, **extra: Any) -> dict:
        req = {"by": by, "reason": reason, "at": self._clock(), "id": uuid.uuid4().hex[:12],
               **extra}
        _atomic_write(self.dir / f"{name}.request", req)
        return req

    def _pending(self, name: str, since: float) -> Optional[dict]:
        req = _read(self.dir / f"{name}.request")
        if req is None or not isinstance(req.get("at"), (int, float)):
            return None
        at = float(req["at"])
        if at < since or self._clock() - at > REQUEST_TTL_S:
            return None
        return req

    def _clear(self, name: str) -> None:
        with contextlib.suppress(OSError):
            (self.dir / f"{name}.request").unlink()

    def request_stop(self, by: str, reason: str = "stopped from the dashboard") -> dict:
        return self._request("stop", by, reason)

    def stop_requested(self, since: float) -> Optional[dict]:
        """A stop request made at or after ``since`` (and still valid), or None."""
        return self._pending("stop", since)

    def clear_stop(self) -> None:
        self._clear("stop")

    def request_start(self, by: str, reason: str = "started from the dashboard", *,
                      job: str = "daemon") -> dict:
        """``job``: ``daemon`` (a session) or ``monitor`` (live: guard what is held)."""
        return self._request("start", by, reason, job=job)

    def start_requested(self) -> Optional[dict]:
        return self._pending("start", 0.0)

    def clear_start(self) -> None:
        self._clear("start")

    # ── Commands ─────────────────────────────────────────────────────────

    def submit(self, kind: str, payload: dict, by: str, *, target: str = "monitor") -> str:
        """Queue a command for the running ``target`` (``monitor`` or ``scalper``)."""
        if kind not in COMMAND_KINDS:
            raise ValueError(f"unknown command {kind!r}")
        if target not in COMMAND_TARGETS:
            raise ValueError(f"unknown target {target!r}")
        cmd_id = uuid.uuid4().hex[:12]
        _atomic_write(self.dir / "commands" / f"{target}-{cmd_id}.json",
                      {"id": cmd_id, "kind": kind, "payload": payload, "by": by,
                       "target": target, "at": self._clock()})
        return cmd_id

    def claim(self, target: str = "monitor") -> list[dict]:
        """``target``'s commands, oldest first; expired ones get an expired result."""
        inbox = self.dir / "commands"
        try:
            names = sorted(p for p in inbox.glob(f"{target}-*.json"))
        except OSError:
            return []
        claimed: list[dict] = []
        for path in names:
            target = self.dir / "claimed" / path.name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)          # atomic: only one process gets it
            except OSError:
                continue
            cmd = _read(target)
            if cmd is None:
                with contextlib.suppress(OSError):
                    target.unlink()
                continue
            at = cmd.get("at")
            cmd_id = str(cmd.get("id") or path.stem)
            if not isinstance(at, (int, float)) or self._clock() - float(at) > COMMAND_TTL_S:
                self.complete(cmd_id, ok=False,
                              message="expired: no running session picked it up in time")
                with contextlib.suppress(OSError):
                    target.unlink()
                continue
            claimed.append(cmd)
        claimed.sort(key=lambda c: c.get("at", 0))
        return claimed

    def complete(self, cmd_id: str, *, ok: bool, message: str, **extra: Any) -> None:
        try:
            _atomic_write(self.dir / "results" / f"{cmd_id}.json",
                          {"id": cmd_id, "ok": ok, "message": message, "at": self._clock(),
                           **extra})
        except OSError:
            logger.warning("Command result %s not written", cmd_id, exc_info=True)
        for name in (f"{cmd_id}.json", *(f"{t}-{cmd_id}.json" for t in COMMAND_TARGETS)):
            with contextlib.suppress(OSError):
                (self.dir / "claimed" / name).unlink()
        self._prune()

    def result(self, cmd_id: str) -> Optional[dict]:
        return _read(self.dir / "results" / f"{cmd_id}.json")

    def pending(self) -> int:
        try:
            return sum(1 for _ in (self.dir / "commands").glob("*.json"))
        except OSError:
            return 0

    def _prune(self) -> None:
        now = self._clock()
        for folder in ("results",):
            with contextlib.suppress(OSError):
                for path in (self.dir / folder).glob("*.json"):
                    with contextlib.suppress(OSError):
                        if now - path.stat().st_mtime > RESULT_KEEP_S:
                            path.unlink()


async def watch_stop(channel: Optional[ControlChannel], stop_event, *, started_at: float,
                     interval_s: float = 2.0, sleep=None) -> None:
    """Set ``stop_event`` when a stop request newer than ``started_at`` appears (then
    clear it). Runs until cancelled or the event is set."""
    import asyncio

    if channel is None:
        return
    pause = sleep or asyncio.sleep
    while not stop_event.is_set():
        req = channel.stop_requested(started_at)
        if req is not None:
            logger.warning("Stop requested from the dashboard by %s: %s — stopping",
                           req.get("by"), req.get("reason"))
            channel.clear_stop()
            stop_event.set()
            return
        await pause(interval_s)
