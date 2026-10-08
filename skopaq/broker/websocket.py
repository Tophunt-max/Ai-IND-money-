"""INDstocks WebSocket feeds: live prices and order updates.

Written from https://api-docs.indstocks.com/Websockets/ (see ``docs/indstocks_api.md``):

- **Price feed** (``wss://ws-prices.indstocks.com/api/v1/ws/prices``): after connecting,
  ``{"action": "subscribe", "mode": "ltp" | "quote", "instruments": ["NSE:2885", ...]}``.
  Instruments are ``SEGMENT:TOKEN`` (``NSE:``, ``BSE:``, ``NFO:``, ``BFO:``, ``NIDX:``,
  ``BIDX:``); REST scrip codes are the same with ``_`` (``NSE_2885``). A tick is a JSON
  string ``{"mode": "ltp", "instrument": "2885", "timestamp": <ms>, "data": {"ltp": ..}}``:
  the instrument comes back **without** its segment, so it is matched to the subscription
  with that token (a token subscribed on two segments is ambiguous and dropped).
- **Order updates** (``wss://ws-order-updates.indstocks.com/api/v1/ws/trades``):
  ``{"action": "subscribe", "mode": "order_update"}``; each frame is a JSON-encoded
  *string* holding the update (decode twice), with short status codes (R, P, S, F, C, RJ,
  PF, PFC). Updates are applied per order id, newest envelope ``timestamp`` first.

Both authenticate with ``Authorization: <token>`` (no ``Bearer``), reconnect with
exponential backoff, re-subscribe after a reconnect, and ignore heartbeats and anything
they do not understand. Nothing here places or confirms an order: the REST order book
stays the source of truth; a feed only makes Skopaq look sooner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

from skopaq.broker.token_manager import TokenExpiredError, TokenManager

logger = logging.getLogger(__name__)

INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0
BACKOFF_MULTIPLIER = 2.0
SUBSCRIBE_BATCH = 50          # instruments per subscribe message
PRICE_MODES = ("ltp", "quote")

# Short codes of the order-updates feed; the final ones end an order
ORDER_STATUS_CODES = {
    "R": "received", "P": "pending", "S": "success", "F": "failed", "C": "cancelled",
    "RJ": "rejected", "PF": "partially_filled", "PFC": "partially_filled_cancelled",
}
FINAL_ORDER_CODES = frozenset({"S", "F", "C", "RJ", "PFC"})

Connect = Callable[[str, dict[str, str]], Awaitable[Any]]


# ── Instruments ──────────────────────────────────────────────────────────────


def ws_instrument(code: str) -> str:
    """A REST scrip code (``NSE_2885``) or an instrument (``NSE:2885``) as the feed's
    ``SEGMENT:TOKEN``."""
    code = code.strip()
    if ":" in code:
        return code.upper()
    segment, sep, token = code.partition("_")
    if not sep or not segment or not token:
        raise ValueError(f"Not a scrip code: {code!r} (expected e.g. NSE_2885)")
    return f"{segment.upper()}:{token}"


def _decode(raw: Any) -> Optional[dict]:
    """A frame as a dict: JSON, or a JSON string holding JSON (decoded twice)."""
    value: Any = raw
    for _ in range(2):
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        if isinstance(value, dict):
            return value
    return None


def _ms_to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


# ── Price feed ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tick:
    """One price update."""

    instrument: str               # "NSE:2885"
    ltp: float
    timestamp: Optional[datetime]  # the broker's (UTC); None if not sent
    received_at: float            # this host's monotonic clock
    mode: str = "ltp"
    volume: Optional[int] = None  # quote mode, when sent
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def scrip_code(self) -> str:
        return self.instrument.replace(":", "_", 1)


def parse_price_message(raw: Any, subscribed: Iterable[str], received_at: float
                        ) -> Optional[Tick]:
    """A tick from one frame, or None (a heartbeat, an ack, an unknown or ambiguous
    instrument, no price)."""
    msg = _decode(raw)
    if msg is None or msg.get("mode") not in PRICE_MODES:
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    ltp = None
    for key in ("ltp", "last_price", "live_price", "lp"):
        ltp = _float(data.get(key))
        if ltp is not None:
            break
    if ltp is None or ltp <= 0:
        return None
    instrument = str(msg.get("instrument") or data.get("instrument") or "").strip()
    if not instrument:
        return None
    if ":" in instrument:
        instrument = instrument.upper()
    else:
        matches = [s for s in subscribed if s.split(":", 1)[-1] == instrument]
        if len(matches) != 1:
            return None   # not ours, or the same token on two segments
        instrument = matches[0]
    volume = data.get("volume")
    return Tick(
        instrument=instrument, ltp=ltp, timestamp=_ms_to_dt(msg.get("timestamp")),
        received_at=received_at, mode=str(msg["mode"]),
        volume=int(volume) if isinstance(volume, (int, float)) and not isinstance(
            volume, bool) else None,
        raw=msg,
    )


async def _default_connect(url: str, headers: dict[str, str]):
    import websockets

    return await websockets.connect(url, additional_headers=headers, ping_interval=20,
                                    ping_timeout=20, open_timeout=15, max_queue=1024)


class _Feed:
    """Connection loop shared by both feeds: connect, on_open, read, back off, repeat."""

    name = "feed"

    def __init__(self, url: str, token_manager: TokenManager, *,
                 connect: Optional[Connect] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._url = url
        self._tokens = token_manager
        self._connect = connect or _default_connect
        self._clock = clock
        self._sleep = sleep
        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.connects = 0                      # successful connections so far
        self.last_message_at: Optional[float] = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"indstocks-{self.name}")

    async def stop(self) -> None:
        self._running = False
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _send(self, message: dict) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(message))
            return True
        except Exception:
            logger.warning("INDstocks %s: send failed — it is resent on reconnect", self.name,
                           exc_info=True)
            return False

    async def _on_open(self) -> None:  # pragma: no cover - overridden
        return None

    def _on_message(self, raw: Any) -> None:  # pragma: no cover - overridden
        return None

    async def _loop(self) -> None:
        backoff = INITIAL_BACKOFF_S
        while self._running:
            ws = None
            try:
                token = self._tokens.get_token()
                ws = await self._connect(self._url, {"Authorization": token})
                self._ws = ws
                self.connects += 1
                backoff = INITIAL_BACKOFF_S
                logger.info("INDstocks %s connected", self.name)
                await self._on_open()
                async for raw in ws:
                    self.last_message_at = self._clock()
                    try:
                        self._on_message(raw)
                    except Exception:
                        logger.warning("INDstocks %s: bad message %r", self.name,
                                       str(raw)[:200], exc_info=True)
                if self._running:
                    logger.warning("INDstocks %s closed by the server", self.name)
            except asyncio.CancelledError:
                raise
            except TokenExpiredError:
                logger.error("INDstocks %s: no valid token — retrying in %.0fs", self.name,
                             MAX_BACKOFF_S)
                backoff = MAX_BACKOFF_S
            except Exception as exc:
                if self._running:
                    logger.warning("INDstocks %s disconnected (%s) — reconnecting in %.0fs",
                                   self.name, exc, backoff)
            finally:
                self._ws = None
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()
            if not self._running:
                break
            await self._sleep(backoff)
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_S)


class PriceFeed(_Feed):
    """Live prices for subscribed instruments; the newest tick of each is kept.

    ``ltp(code, max_age_s)`` is the last price if it arrived within ``max_age_s`` (by
    this host's clock) while connected, else None: the caller falls back to REST.
    ``on_tick`` listeners are called for every tick (their errors are logged).
    """

    name = "price feed"

    def __init__(self, url: str, token_manager: TokenManager, *, mode: str = "ltp",
                 **kwargs: Any) -> None:
        super().__init__(url, token_manager, **kwargs)
        if mode not in PRICE_MODES:
            raise ValueError(f"mode must be one of {PRICE_MODES}")
        self._mode = mode
        self._subscribed: set[str] = set()
        self._latest: dict[str, Tick] = {}
        self._listeners: list[Callable[[Tick], None]] = []
        self.ticks = 0

    @property
    def subscribed(self) -> frozenset[str]:
        return frozenset(self._subscribed)

    def add_listener(self, listener: Callable[[Tick], None]) -> None:
        self._listeners.append(listener)

    async def subscribe(self, codes: Iterable[str]) -> None:
        new = sorted({ws_instrument(c) for c in codes} - self._subscribed)
        if not new:
            return
        self._subscribed.update(new)
        await self._send_batches("subscribe", new)

    async def unsubscribe(self, codes: Iterable[str]) -> None:
        gone = sorted({ws_instrument(c) for c in codes} & self._subscribed)
        if not gone:
            return
        self._subscribed.difference_update(gone)
        for code in gone:
            self._latest.pop(code, None)
        await self._send_batches("unsubscribe", gone)

    def latest(self, code: str) -> Optional[Tick]:
        return self._latest.get(ws_instrument(code))

    def ltp(self, code: str, max_age_s: float) -> Optional[float]:
        if not self.connected:
            return None
        tick = self.latest(code)
        if tick is None or self._clock() - tick.received_at > max_age_s:
            return None
        return tick.ltp

    async def _send_batches(self, action: str, instruments: list[str]) -> None:
        for i in range(0, len(instruments), SUBSCRIBE_BATCH):
            await self._send({"action": action, "mode": self._mode,
                              "instruments": instruments[i:i + SUBSCRIBE_BATCH]})

    async def _on_open(self) -> None:
        if self._subscribed:
            await self._send_batches("subscribe", sorted(self._subscribed))

    def _on_message(self, raw: Any) -> None:
        tick = parse_price_message(raw, self._subscribed, self._clock())
        if tick is None:
            return
        self._latest[tick.instrument] = tick
        self.ticks += 1
        for listener in list(self._listeners):
            try:
                listener(tick)
            except Exception:
                logger.warning("Price listener failed", exc_info=True)


# ── Order updates ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrderUpdate:
    """One order update (fields the feed omits are None)."""

    order_id: str
    status_code: str              # R, P, S, F, C, RJ, PF, PFC (as sent, upper-cased)
    side: str                     # BUY / SELL
    published_at: Optional[datetime]
    sequence: int                 # the envelope timestamp (ms), to order updates
    entity_name: str = ""
    requested_qty: Optional[int] = None
    filled_lots: Optional[int] = None
    executed_price: Optional[float] = None
    error_message: str = ""

    @property
    def final(self) -> bool:
        return self.status_code in FINAL_ORDER_CODES

    @property
    def status(self) -> str:
        return ORDER_STATUS_CODES.get(self.status_code, "unknown")


def parse_order_update(raw: Any) -> Optional[OrderUpdate]:
    msg = _decode(raw)
    if msg is None or msg.get("mode") != "order_update":
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    order_id = str(data.get("order_id") or "").strip()
    code = str(data.get("order_status") or "").strip().upper()
    if not order_id or not code:
        return None
    seq = msg.get("timestamp")
    seq = int(seq) if isinstance(seq, (int, float)) and not isinstance(seq, bool) else 0

    def _int(key: str) -> Optional[int]:
        value = data.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(
            value, bool) else None

    return OrderUpdate(
        order_id=order_id, status_code=code,
        side=str(data.get("order_type") or "").upper(),
        published_at=_ms_to_dt(msg.get("timestamp")), sequence=seq,
        entity_name=str(data.get("entity_name") or ""),
        requested_qty=_int("req_quantity"), filled_lots=_int("lot"),
        executed_price=_float(data.get("executed_price")),
        error_message=str(data.get("error_message") or "").strip(),
    )


class OrderUpdateFeed(_Feed):
    """The account's order updates; the newest per order is kept.

    An update older (by envelope timestamp) than the one already held for an order is
    ignored. ``on_update`` listeners get every newer update.
    """

    name = "order feed"

    def __init__(self, url: str, token_manager: TokenManager, **kwargs: Any) -> None:
        super().__init__(url, token_manager, **kwargs)
        self._orders: dict[str, OrderUpdate] = {}
        self._listeners: list[Callable[[OrderUpdate], None]] = []

    def add_listener(self, listener: Callable[[OrderUpdate], None]) -> None:
        self._listeners.append(listener)

    def latest(self, order_id: str) -> Optional[OrderUpdate]:
        return self._orders.get(str(order_id))

    async def _on_open(self) -> None:
        await self._send({"action": "subscribe", "mode": "order_update"})

    def _on_message(self, raw: Any) -> None:
        update = parse_order_update(raw)
        if update is None:
            return
        held = self._orders.get(update.order_id)
        if held is not None and update.sequence < held.sequence:
            return
        self._orders[update.order_id] = update
        for listener in list(self._listeners):
            try:
                listener(update)
            except Exception:
                logger.warning("Order update listener failed", exc_info=True)


def feeds_from_config(config: Any, token_manager: Optional[TokenManager] = None
                      ) -> tuple[Optional[PriceFeed], Optional[OrderUpdateFeed]]:
    """The feeds ``config`` turns on (``ws_price_feed_enabled``, ``ws_order_feed_enabled``;
    only a real ``True`` counts), not started."""
    tokens = token_manager or TokenManager()
    price = order = None
    if getattr(config, "ws_price_feed_enabled", False) is True:
        price = PriceFeed(config.indstocks_ws_price_url, tokens)
    if getattr(config, "ws_order_feed_enabled", False) is True:
        order = OrderUpdateFeed(config.indstocks_ws_order_url, tokens)
    return price, order
