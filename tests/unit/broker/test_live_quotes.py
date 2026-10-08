"""Dashboard quotes (skopaq/broker/live_quotes.py): INDstocks when a token is valid, Yahoo
Finance for indices, unresolved symbols, no token and INDstocks failures."""

from __future__ import annotations

import asyncio

import pytest

from skopaq.broker import live_quotes, yahoo_quotes
from skopaq.broker.models import Quote


def _yahoo(symbol):
    return {"symbol": symbol, "ltp": 100.0, "source": "yahoo"}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    live_quotes._cache.clear()
    calls = {"yahoo": [], "live": []}

    def fake_yahoo(symbols):
        calls["yahoo"].append(list(symbols))
        errors = {"NOPE": "no data"} if "NOPE" in symbols else {}
        return {s: _yahoo(s) for s in symbols if s != "NOPE"}, errors

    monkeypatch.setattr(yahoo_quotes, "get_quotes", fake_yahoo)
    yield calls
    live_quotes._cache.clear()


def _run(symbols):
    return asyncio.run(live_quotes.get_quotes(symbols))


def test_no_token_uses_yahoo(monkeypatch, _fresh):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: False)
    quotes, errors = _run(["TCS"])
    assert quotes["TCS"]["source"] == "yahoo" and errors == {}
    assert live_quotes.source_label(quotes) == "Yahoo Finance (may be delayed)"


def test_token_uses_indstocks_and_yahoo_for_the_rest(monkeypatch, _fresh):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)

    async def fake_live(symbols):
        _fresh["live"].append(list(symbols))
        q = live_quotes._shape("TCS", "NSE_11536", Quote(
            symbol="TCS", ltp=2136.7, open=2104, high=2141.5, low=2100.1, close=2080.3,
            change=56.4, change_pct=2.71, volume=12345))
        return {"TCS": q}, {"WEIRD": "INDstocks: unknown symbol"}

    monkeypatch.setattr(live_quotes, "_indstocks", fake_live)
    quotes, errors = _run(["TCS", "WEIRD", "^NSEI"])
    assert _fresh["live"] == [["TCS", "WEIRD"]]  # indices never go to INDstocks
    tcs = quotes["TCS"]
    assert tcs["source"] == "indstocks" and tcs["ltp"] == 2136.7
    assert tcs["prev_close"] == 2080.3 and tcs["change_pct"] == pytest.approx(0.0271)
    assert quotes["WEIRD"]["source"] == "yahoo" and quotes["^NSEI"]["source"] == "yahoo"
    assert errors == {}
    assert live_quotes.source_label(quotes).startswith("INDstocks (live), Yahoo")


def test_indstocks_failure_falls_back(monkeypatch, _fresh):
    monkeypatch.setattr(live_quotes, "_token_ok", lambda: True)

    async def broken(symbols):
        raise RuntimeError("401 Unable to validate user token")

    monkeypatch.setattr(live_quotes, "_indstocks", broken)
    quotes, errors = _run(["TCS", "NOPE"])
    assert quotes["TCS"]["source"] == "yahoo"
    assert "401" in errors["NOPE"] and "no data" in errors["NOPE"]


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
