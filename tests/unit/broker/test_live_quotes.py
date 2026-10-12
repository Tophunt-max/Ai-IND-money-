"""Market data (skopaq/broker/live_quotes.py): the INDstocks API only — stocks, indices,
charts and multi-year daily history. No token or a failure gives errors, never another
vendor."""

from __future__ import annotations

import asyncio

import pytest

from skopaq.broker import live_quotes
from skopaq.broker.models import Quote


@pytest.fixture(autouse=True)
def _fresh():
    live_quotes._cache.clear()
    calls = {"live": []}
    yield calls
    live_quotes._cache.clear()


def _run(symbols):
    return asyncio.run(live_quotes.get_quotes(symbols))


def test_no_token_gives_errors_not_another_vendor(monkeypatch):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: False)
    quotes, errors = _run(["TCS"])
    assert quotes == {}
    assert "INDstocks token" in errors["TCS"]
    assert live_quotes.source_label(quotes) == "INDstocks unavailable"


def test_token_uses_indstocks(monkeypatch, _fresh):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)

    async def fake_live(symbols):
        _fresh["live"].append(list(symbols))
        q = live_quotes._shape("TCS", "NSE_11536", Quote(
            symbol="TCS", ltp=2136.7, open=2104, high=2141.5, low=2100.1, close=2080.3,
            change=56.4, change_pct=2.71, volume=12345))
        return {"TCS": q}, {"WEIRD": "INDstocks: unknown symbol"}

    monkeypatch.setattr(live_quotes, "_indstocks", fake_live)
    quotes, errors = _run(["TCS", "WEIRD", "^NSEI"])
    assert _fresh["live"] == [["TCS", "WEIRD", "^NSEI"]]  # indices go to INDstocks too
    tcs = quotes["TCS"]
    assert tcs["source"] == "indstocks" and tcs["ltp"] == 2136.7
    assert tcs["prev_close"] == 2080.3 and tcs["change_pct"] == pytest.approx(0.0271)
    assert set(quotes) == {"TCS"} and errors == {"WEIRD": "INDstocks: unknown symbol"}
    assert live_quotes.source_label(quotes) == "INDstocks (live)"


def test_indstocks_failure_is_reported(monkeypatch):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)

    async def broken(symbols):
        raise RuntimeError("401 Unable to validate user token")

    monkeypatch.setattr(live_quotes, "_indstocks", broken)
    quotes, errors = _run(["TCS", "NOPE"])
    assert quotes == {} and "401" in errors["TCS"] and "401" in errors["NOPE"]


@pytest.mark.parametrize("bad", ["", "rm -rf;", "A" * 25, "TCS.XX", "../x"])
def test_rejects_bad_symbols(bad):
    with pytest.raises(ValueError):
        live_quotes.normalize(bad)


def test_run_sync_works_inside_and_outside_an_event_loop():
    async def answer():
        return 42

    assert live_quotes.run_sync(answer()) == 42

    async def inside():
        return live_quotes.run_sync(answer())

    assert asyncio.run(inside()) == 42


def test_live_quotes_are_cached_briefly(monkeypatch, _fresh):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)

    async def fake_live(symbols):
        _fresh["live"].append(list(symbols))
        return {s: {"symbol": s, "ltp": 1.0, "source": "indstocks"} for s in symbols}, {}

    monkeypatch.setattr(live_quotes, "_indstocks", fake_live)
    _run(["TCS"])
    _run(["TCS"])
    assert _fresh["live"] == [["TCS"]]


def test_zero_price_is_not_a_quote():
    assert live_quotes._shape("TCS", "NSE_1", Quote(symbol="TCS", ltp=0)) is None


# ── Indices and charts from INDstocks ────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from skopaq.broker import fno  # noqa: E402

INDEX_CSV = "EXCH,SEGMENT,SECURITY_ID\nNSE,NIFTY 50,40000001\nNSE,INDIA VIX,40000099\n" \
            "BSE,SENSEX,40000100\n"


class FakeClient:
    """Index file, quotes (only NIDX_ codes answer) and paged history."""

    def __init__(self, candles=()):
        self.quote_calls = []
        self.history_calls = []
        self.candles = list(candles)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_instruments(self, source="equity"):
        assert source == "index"
        return INDEX_CSV

    async def get_quotes(self, codes, symbols=None):
        self.quote_calls.append(list(codes))
        return [Quote(symbol=(symbols or codes)[i], ltp=25_000.5 if c.startswith(("NIDX", "BIDX"))
                      else 0, close=24_900, open=24_950, high=25_050, low=24_900)
                for i, c in enumerate(codes)]

    async def get_historical(self, code, interval, start_time, end_time):
        self.history_calls.append((code, interval, start_time, end_time))
        return [c for c in self.candles
                if start_time <= c.timestamp.timestamp() * 1000 < end_time]


@pytest.fixture
def fake_client(monkeypatch):
    fno.clear_caches()
    live_quotes._codes.clear()
    live_quotes._history_cache.clear()
    holder = {}

    def make(candles=()):
        holder["client"] = FakeClient(candles)
        monkeypatch.setattr(live_quotes, "_client", lambda: holder["client"])
        return holder["client"]

    yield make
    fno.clear_caches()
    live_quotes._codes.clear()
    live_quotes._history_cache.clear()


def test_indices_are_priced_live_from_their_instrument_ids(monkeypatch, fake_client):
    client = fake_client()
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)
    quotes, errors = _run(["^NSEI", "^INDIAVIX", "^BSESN"])
    assert ["NIDX_40000001"] in client.quote_calls             # probed one code at a time
    assert errors == {}
    nifty = quotes["^NSEI"]
    assert nifty["source"] == "indstocks" and nifty["ltp"] == 25_000.5
    assert nifty["ticker"] == "NIDX_40000001"
    assert quotes["^INDIAVIX"]["ticker"] == "NIDX_40000099"
    assert quotes["^BSESN"]["ticker"] == "BIDX_40000100"
    # The NIDX prefix answered: it is remembered and tried first
    assert fno.index_code_candidates("NSE", "7")[0] == "NIDX_7"
    assert live_quotes.source_label(quotes) == "INDstocks (live)"


def test_an_unknown_index_is_an_error(monkeypatch, fake_client):
    fake_client()
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)
    quotes, errors = _run(["^XYZ"])
    assert quotes == {} and "no INDstocks code" in errors["^XYZ"]


def _candle(ts, price):
    return SimpleNamespace(timestamp=ts, open=price, high=price + 1, low=price - 1,
                           close=price, volume=10)


def test_charts_come_from_indstocks_paged_and_trimmed(monkeypatch, fake_client):
    ist = timezone(timedelta(hours=5, minutes=30))
    day1 = datetime(2026, 10, 7, 9, 15, tzinfo=ist)
    day2 = datetime(2026, 10, 8, 9, 15, tzinfo=ist)
    candles = [_candle(day1 + timedelta(minutes=5 * i), 100 + i) for i in range(3)]
    candles += [_candle(day2 + timedelta(minutes=5 * i), 200 + i) for i in range(3)]
    client = fake_client(candles)
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)
    now = datetime(2026, 10, 8, 10, 0, tzinfo=ist)

    data = asyncio.run(live_quotes._indstocks_history("^NSEI", "1d", now=now))
    assert data["source"] == "indstocks" and data["interval"] == "5minute"
    assert [c["c"] for c in data["candles"]] == [200, 201, 202]      # the last day only
    assert client.history_calls[0][0] == "NIDX_40000001"
    # 5Y of weekly candles: five one-year windows
    client.history_calls.clear()
    try:
        asyncio.run(live_quotes._indstocks_history("^NSEI", "5y", now=now))
    except LookupError:
        pass                                          # no weekly candles in the fake
    assert len(client.history_calls) == 5 and client.history_calls[0][1] == "1week"


def test_chart_without_a_token_raises(monkeypatch, fake_client):
    fake_client()
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: False)
    with pytest.raises(LookupError, match="INDstocks token"):
        asyncio.run(live_quotes.get_history("TCS", "1d"))
    with pytest.raises(ValueError):
        asyncio.run(live_quotes.get_history("TCS", "9y"))


def test_daily_history_pages_one_year_at_a_time_back_to_the_start(monkeypatch, fake_client):
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime(2026, 10, 9, 16, 0, tzinfo=ist)
    first = datetime(2024, 3, 1, 9, 15, tzinfo=ist)      # history starts here
    candles = [_candle(first + timedelta(days=i), 100 + i)
               for i in range((now - first).days)]
    client = fake_client(candles)
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)

    rows = asyncio.run(live_quotes.daily_history("^NSEI", 5 * 365, now=now))
    assert len(rows) == len(candles) and rows[0]["c"] == 100      # oldest first, no gaps
    assert all(call[1] == "1day" for call in client.history_calls)
    spans = [(e - s) / 86_400_000 for _, _, s, e in client.history_calls]
    assert max(spans) <= 365                                        # the broker's window
    # 2026-10 → 2025-10 → 2024-10 → 2023-10 (empty: before the start) and stop
    assert len(client.history_calls) == 4


def test_daily_history_without_a_token_raises(monkeypatch, fake_client):
    fake_client()
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: False)
    with pytest.raises(LookupError):
        asyncio.run(live_quotes.daily_history("TCS", 365))


async def test_paper_fill_has_no_price_without_indstocks(monkeypatch):
    from skopaq.broker import client as broker_client
    from skopaq.cli import main as cli

    class NoToken:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("no token")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(broker_client, "INDstocksClient", NoToken)
    got = []
    paper = SimpleNamespace(update_quote=got.append)
    await cli._inject_paper_quote(SimpleNamespace(), paper, "SBIN")
    assert got == []  # no other vendor's price is used
