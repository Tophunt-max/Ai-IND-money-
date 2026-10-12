"""All NSE/BSE market data comes from the INDstocks API: regime, executor price, MCP
indicators, the router's default and the vendor's yearly paging."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from skopaq.broker import live_quotes

IST = timezone(timedelta(hours=5, minutes=30))


def _rows(n: int, start: float = 100.0, step: float = 1.0) -> list[dict]:
    day0 = datetime(2025, 1, 1, 9, 15, tzinfo=IST)
    return [{"t": int((day0 + timedelta(days=i)).timestamp()), "o": start + i * step,
             "h": start + i * step + 2, "l": start + i * step - 2, "c": start + i * step,
             "v": 1000} for i in range(n)]


def test_regime_data_comes_from_indstocks(monkeypatch):
    from skopaq.risk.regime import fetch_regime_data

    calls = []

    async def history(symbol, range_):
        calls.append((symbol, range_))
        if symbol == "^INDIAVIX":
            return {"candles": [{"t": 1, "c": 14.5}]}
        return {"candles": _rows(250)}

    monkeypatch.setattr(live_quotes, "get_history", history)
    vix, price, sma = fetch_regime_data()
    assert calls == [("^INDIAVIX", "1mo"), ("^NSEI", "1y")]
    assert vix == 14.5 and price == 349.0
    assert sma == pytest.approx(sum(100 + i for i in range(50, 250)) / 200)


def test_regime_data_without_indstocks_is_none(monkeypatch):
    from skopaq.risk.regime import fetch_regime_data

    async def fail(symbol, range_):
        raise LookupError(live_quotes.NO_TOKEN)

    monkeypatch.setattr(live_quotes, "get_history", fail)
    assert fetch_regime_data() == (None, None, None)


def test_executor_entry_price_is_the_indstocks_ltp(monkeypatch):
    from skopaq.execution.executor import Executor

    async def ltp(symbol):
        assert symbol == "TCS"
        return 2136.712

    monkeypatch.setattr(live_quotes, "get_ltp", ltp)
    assert Executor._fetch_current_price("TCS") == 2136.71

    async def none(symbol):
        return None

    monkeypatch.setattr(live_quotes, "get_ltp", none)
    assert Executor._fetch_current_price("TCS") is None


def test_mcp_market_data_uses_indstocks_and_local_indicators(monkeypatch):
    from skopaq import mcp_server

    async def history(symbol, range_):
        assert range_ == "1y"
        return {"symbol": symbol, "candles": _rows(220), "source": "indstocks"}

    monkeypatch.setattr(live_quotes, "get_history", history)
    out = json.loads(asyncio.run(mcp_server.gather_market_data("TCS")))
    assert out["source"] == "indstocks"
    ind = out["indicators"]
    assert ind["close_50_sma"] == pytest.approx(sum(100 + i for i in range(170, 220)) / 50)
    assert ind["atr"] == pytest.approx(4.0, rel=0.01)  # constant 4-point range, +1 drift
    assert ind["rsi"] == 100.0 and ind["macd"] is not None and ind["vwma"] is not None
    assert out["ohlcv"].startswith("Date,Open,High,Low,Close,Volume")


def test_mcp_news_and_fundamentals_say_indstocks_has_none():
    from skopaq import mcp_server

    news = json.loads(asyncio.run(mcp_server.gather_news_data("TCS")))
    fund = json.loads(asyncio.run(mcp_server.gather_fundamentals_data("TCS")))
    assert "INDstocks API" in news["company_news"]
    assert "INDstocks API" in fund["balance_sheet"]


def test_router_default_for_stock_data_is_indstocks():
    from tradingagents.default_config import build_default_config

    assert build_default_config()["data_vendors"]["core_stock_apis"] == "indstocks"


def test_indstocks_vendor_pages_daily_history_one_year_at_a_time(monkeypatch):
    from tradingagents.dataflows.vendors import indstocks as vendor

    calls = []
    first = datetime(2023, 6, 1, tzinfo=IST)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_historical(self, scrip_code, interval, start_time, end_time):
            calls.append((start_time, end_time))
            out = []
            d = datetime.fromtimestamp(start_time / 1000, IST)
            while d.timestamp() * 1000 < end_time:
                if d >= first:
                    out.append(SimpleNamespace(timestamp=d, open=1.0, high=1.0, low=1.0,
                                               close=1.0, volume=1))
                d += timedelta(days=1)
            return out

    async def resolve(symbol, exchange, client):
        return "NSE_1"

    monkeypatch.setattr(vendor, "_get_client", lambda: Client())
    monkeypatch.setattr(vendor, "_resolve_scrip_code", resolve)
    # Eight years asked; the history starts in 2023-06
    text = asyncio.run(vendor._fetch_historical("TCS", "2018-10-01", "2026-10-01"))
    year_ms = 365 * 24 * 60 * 60 * 1000
    assert all(e - s <= year_ms for s, e in calls)
    # Four windows with candles, then the first empty one (before 2023-06): stop, not 8
    assert len(calls) == 5
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert lines[1].startswith("2023-06-01")       # oldest first, from the start of history
    assert len(lines) - 1 == len({ln.split(",")[0] for ln in lines[1:]})  # no duplicates
