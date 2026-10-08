"""INDstocks WebSocket feeds (skopaq/broker/websocket.py) against a fake connection: the
documented messages, subscriptions, reconnects and fresh/stale prices."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from skopaq.broker.websocket import (
    OrderUpdateFeed,
    PriceFeed,
    feeds_from_config,
    parse_order_update,
    parse_price_message,
    ws_instrument,
)


class FakeSocket:
    """Yields ``frames`` (then waits until closed, or ends when ``hold`` is False)."""

    def __init__(self, frames, hold=True):
        self.frames = list(frames)
        self.sent: list[dict] = []
        self.closed = asyncio.Event()
        self.hold = hold

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def close(self):
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        if self.frames:
            return self.frames.pop(0)
        if self.hold:
            await self.closed.wait()
        raise StopAsyncIteration


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _tokens(token="TOKEN-1"):
    tokens = MagicMock()
    tokens.get_token.return_value = token
    return tokens


def _price_feed(sockets, clock=None, **kw):
    connects = []

    async def connect(url, headers):
        connects.append((url, headers))
        sock = sockets.pop(0)
        if isinstance(sock, Exception):
            raise sock
        return sock

    async def no_sleep(_s):
        await asyncio.sleep(0)

    feed = PriceFeed("wss://prices", _tokens(), connect=connect, clock=clock or Clock(),
                     sleep=no_sleep, **kw)
    return feed, connects


def _ltp(token, price, ts=1750138351089):
    return json.dumps({"mode": "ltp", "instrument": token, "timestamp": ts,
                       "data": {"ltp": price}})


async def _until(cond, n=200):
    for _ in range(n):
        if cond():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


def test_instrument_formats():
    assert ws_instrument("NSE_2885") == "NSE:2885"
    assert ws_instrument("nfo:51011") == "NFO:51011"
    with pytest.raises(ValueError):
        ws_instrument("2885")


def test_parse_the_documented_ltp_tick():
    tick = parse_price_message(_ltp("2885", 1426), {"NSE:2885"}, 5.0)
    assert (tick.instrument, tick.ltp, tick.scrip_code, tick.received_at) == (
        "NSE:2885", 1426.0, "NSE_2885", 5.0)
    assert tick.timestamp.year == 2025


@pytest.mark.parametrize("frame", [
    "not json", json.dumps({"type": "heartbeat"}), json.dumps({"mode": "ltp", "data": {}}),
    _ltp("9999", 10),                                    # not subscribed
    _ltp("2885", 0),
])
def test_heartbeats_and_unknown_frames_are_ignored(frame):
    assert parse_price_message(frame, {"NSE:2885"}, 0.0) is None


def test_a_token_subscribed_on_two_segments_is_ambiguous():
    assert parse_price_message(_ltp("2885", 10), {"NSE:2885", "BSE:2885"}, 0.0) is None
    # A frame that names its segment is fine
    frame = json.dumps({"mode": "ltp", "instrument": "BSE:2885", "data": {"ltp": 10}})
    assert parse_price_message(frame, {"NSE:2885", "BSE:2885"}, 0.0).instrument == "BSE:2885"


async def test_subscribes_after_connecting_and_keeps_fresh_prices_only():
    clock = Clock()
    sock = FakeSocket([_ltp("2885", 1426)])
    feed, connects = _price_feed([sock], clock)
    await feed.subscribe(["NSE_2885"])          # before connecting: sent on open
    seen = []
    feed.add_listener(seen.append)
    await feed.start()
    await _until(lambda: feed.ticks == 1)

    assert connects == [("wss://prices", {"Authorization": "TOKEN-1"})]   # no "Bearer"
    assert sock.sent == [{"action": "subscribe", "mode": "ltp", "instruments": ["NSE:2885"]}]
    assert [t.ltp for t in seen] == [1426.0]
    assert feed.ltp("NSE_2885", max_age_s=5) == 1426.0
    clock.t += 6
    assert feed.ltp("NSE_2885", max_age_s=5) is None                     # stale
    await feed.subscribe(["NSE_11536", "NSE_2885"])                      # only the new one
    assert sock.sent[-1]["instruments"] == ["NSE:11536"]
    await feed.stop()
    assert feed.ltp("NSE_2885", max_age_s=100) is None                   # disconnected


async def test_reconnects_after_a_failure_and_resubscribes():
    first = FakeSocket([_ltp("2885", 10)], hold=False)                   # server closes
    second = FakeSocket([_ltp("2885", 11)])
    feed, connects = _price_feed([OSError("refused"), first, second])
    await feed.subscribe([f"NSE_{i}" for i in range(60)] + ["NSE_2885"])
    await feed.start()
    await _until(lambda: feed.ticks == 2)

    assert len(connects) == 3 and feed.connects == 2
    batches = [m["instruments"] for m in second.sent]
    assert [len(b) for b in batches] == [50, 11]                         # batched
    assert feed.latest("NSE_2885").ltp == 11.0
    await feed.stop()


def test_parse_the_documented_double_encoded_order_update():
    inner = {"mode": "order_update", "timestamp": 1789628366550, "data": {
        "order_id": "97603202", "entity_name": "SENSEX 17 SEP 74400 CE", "lot": 1,
        "order_type": "BUY", "order_status": "S", "executed_price": 306.45,
        "elapsed_time": 8, "error_message": " ", "timestamp": 1789628366544,
        "req_quantity": 20, "requested_lot": 1}}
    update = parse_order_update(json.dumps(json.dumps(inner)))
    assert (update.order_id, update.status, update.final, update.executed_price,
            update.requested_qty, update.filled_lots, update.error_message) == (
        "97603202", "success", True, 306.45, 20, 1, "")
    assert parse_order_update(json.dumps({"mode": "ltp"})) is None


async def test_order_feed_subscribes_and_keeps_the_newest_update_per_order():
    def frame(status, ts):
        return json.dumps(json.dumps({"mode": "order_update", "timestamp": ts, "data": {
            "order_id": "1", "order_type": "SELL", "order_status": status}}))

    sock = FakeSocket([frame("P", 2), frame("R", 1), frame("S", 3)])

    async def connect(url, headers):
        return sock

    feed = OrderUpdateFeed("wss://orders", _tokens(), connect=connect)
    seen = []
    feed.add_listener(lambda u: seen.append(u.status_code))
    await feed.start()
    await _until(lambda: len(seen) == 2)
    await feed.stop()

    assert sock.sent == [{"action": "subscribe", "mode": "order_update"}]
    assert seen == ["P", "S"]                     # the late "R" is older: ignored
    assert feed.latest("1").final


def test_feeds_from_config_needs_a_real_true():
    price, order = feeds_from_config(MagicMock(), _tokens())
    assert (price, order) == (None, None)
    cfg = MagicMock(ws_price_feed_enabled=True, ws_order_feed_enabled=False,
                    indstocks_ws_price_url="wss://p", indstocks_ws_order_url="wss://o")
    price, order = feeds_from_config(cfg, _tokens())
    assert isinstance(price, PriceFeed) and order is None
