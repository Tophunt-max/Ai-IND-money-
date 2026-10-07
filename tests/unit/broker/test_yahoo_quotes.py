"""Yahoo Finance quotes/history (skopaq/broker/yahoo_quotes.py) and the paper-fill fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from skopaq.broker import yahoo_quotes as yq


@pytest.fixture(autouse=True)
def _clear_cache():
    yq._cache.clear()
    yield
    yq._cache.clear()


def _frame(closes):
    idx = pd.date_range("2026-10-01", periods=len(closes), freq="D", tz="Asia/Kolkata")
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes,
                         "Volume": [100] * len(closes)}, index=idx)


@pytest.mark.parametrize("raw,ticker", [
    ("reliance", "RELIANCE.NS"), (" M&M ", "M&M.NS"), ("BAJAJ-AUTO", "BAJAJ-AUTO.NS"),
    ("TCS.BO", "TCS.BO"), ("^NSEI", "^NSEI"),
])
def test_yahoo_ticker(raw, ticker):
    assert yq.yahoo_ticker(raw) == ticker


@pytest.mark.parametrize("bad", ["", "rm -rf;", "A" * 25, "TCS.XX", "../x"])
def test_rejects_bad_symbols(bad):
    with pytest.raises(ValueError):
        yq.normalize(bad)


def test_quote_from_daily_bars(monkeypatch):
    calls = []

    def fake(ticker, period, interval):
        calls.append((ticker, period, interval))
        return _frame([100.0, 110.0])

    monkeypatch.setattr(yq, "_history", fake)
    q = yq.get_quote("tcs")
    assert q["symbol"] == "TCS" and q["ticker"] == "TCS.NS"
    assert q["ltp"] == 110.0 and q["prev_close"] == 100.0 and q["change"] == 10.0
    assert abs(q["change_pct"] - 0.1) < 1e-9 and q["volume"] == 100
    yq.get_quote("TCS")
    assert calls == [("TCS.NS", "5d", "1d")]  # second call served from the cache


def test_quote_without_data_raises_and_get_quotes_collects_errors(monkeypatch):
    monkeypatch.setattr(yq, "_history", lambda *a: _frame([]))
    with pytest.raises(LookupError):
        yq.get_quote("TCS")
    quotes, errors = yq.get_quotes(["TCS", "bad;"])
    assert quotes == {} and set(errors) == {"TCS", "bad;"}


def test_history_candles(monkeypatch):
    monkeypatch.setattr(yq, "_history", lambda t, p, i: _frame([1.0, float("nan"), 3.0]))
    h = yq.get_history("INFY", "1mo")
    assert h["interval"] == "1d" and [c["c"] for c in h["candles"]] == [1.0, 3.0]
    with pytest.raises(ValueError):
        yq.get_history("INFY", "10y")


def test_paper_quote(monkeypatch):
    monkeypatch.setattr(yq, "_history", lambda *a: _frame([100.0, 102.0]))
    q = yq.paper_quote("SBIN")
    assert q.symbol == "SBIN" and q.ltp == q.bid == q.ask == 102.0 and q.close == 100.0


async def test_inject_paper_quote_falls_back_to_yahoo(monkeypatch):
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
    monkeypatch.setattr(yq, "_history", lambda *a: _frame([100.0, 101.0]))
    got = []
    paper = SimpleNamespace(update_quote=got.append)
    await cli._inject_paper_quote(SimpleNamespace(), paper, "SBIN")
    assert len(got) == 1 and got[0].ltp == 101.0 and got[0].symbol == "SBIN"
