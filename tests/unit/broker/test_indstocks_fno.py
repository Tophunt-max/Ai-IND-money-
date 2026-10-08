"""INDstocks F&O support: the derivatives endpoints of INDstocksClient, the name → id
lookups of skopaq.broker.fno, the option chain, and the derivative order rules of
OrderRequest. Mocked transport only (httpx.MockTransport)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest

from skopaq.broker import client as client_mod
from skopaq.broker import fno
from skopaq.broker.client import INDstocksClient
from skopaq.broker.models import OrderRequest, Product, Segment, Side
from skopaq.broker.rate_limiter import RateLimiter
from skopaq.execution.safety_checker import SafetyChecker

INDEX_CSV = "EXCH,SEGMENT,SECURITY_ID\nNSE,NIFTY 50,40000001\nNSE,NIFTY BANK,40000002\n" \
            "BSE,SENSEX,40000100\n"

CHAIN = {
    "underlying_ltp": 24471.7,
    "expiry": "2026-10-13",
    "strikes": {
        "24500": {
            "ce": {"security_id": 45110, "trading_symbol": "NIFTY-Oct2026-24500-CE",
                   "last_price": 150.0, "oi": 1000, "previous_oi": 900, "volume": 50,
                   "top_bid_price": 149.5, "top_ask_price": 150.5, "iv": 11.2,
                   "greeks": {"delta": 0.48, "gamma": 0.001, "theta": -9.5, "vega": 13.0}},
            "pe": {"security_id": "45111", "trading_symbol": "NIFTY-Oct2026-24500-PE",
                   "last_price": 170.0, "iv": 11.0,
                   "greeks": {"delta": -0.52, "theta": -9.0}},
        },
        "24450": {
            "ce": {"security_id": "45108", "trading_symbol": "NIFTY-Oct2026-24450-CE",
                   "last_price": 180.0, "greeks": None},
        },
    },
}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(client_mod, "_api_limiter", RateLimiter(max_calls=10_000))
    monkeypatch.setattr(client_mod, "_order_limiter", RateLimiter(max_calls=10_000))
    fno.clear_caches()
    yield
    fno.clear_caches()


def _client(handler) -> INDstocksClient:
    cfg = MagicMock()
    cfg.indstocks_base_url = "https://api.indstocks.test"
    token = MagicMock()
    token.get_token.return_value = "TOKEN"
    return INDstocksClient(cfg, token, transport=httpx.MockTransport(handler))


def ok(data) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": data})


def _broker(requests: list[httpx.Request]):
    """A fake INDstocks answering the derivatives endpoints."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path, q = request.url.path, request.url.params
        if path == "/market/instruments" and q["source"] == "index":
            return httpx.Response(200, text=INDEX_CSV)
        if path == "/market/instruments/expiries":
            return ok(["2026-10-20", "2026-10-13"])
        if path == "/market/instruments/search":
            return ok({"count": 1, "page": 1, "page_size": 1, "instruments": [
                {"security_id": 45110, "trading_symbol": "NIFTY26OCT1324500CE",
                 "expiry": "2026-10-13", "strike_price": 24500, "option_type": "CE",
                 "lot_size": 75}]})
        if path == "/market/option-chain":
            return ok(CHAIN)
        return httpx.Response(404, json={"message": f"unexpected {path}"})

    return handler


async def test_option_chain_request_and_parsing():
    requests: list[httpx.Request] = []
    async with _client(_broker(requests)) as client:
        chain = await client.get_option_chain("40000001", "2026-10-13", strike_count=5)

    params = requests[0].url.params
    assert (params["exchange"], params["segment"], params["underlying-scrip"],
            params["expiry"], params["strike_count"]) == (
        "NSE", "INDEX", "40000001", "2026-10-13", "5")
    assert chain.underlying_ltp == 24471.7
    assert [s.strike for s in chain.strikes] == [24450.0, 24500.0]   # sorted numerically
    leg = chain.strikes[1].ce
    assert leg.security_id == "45110" and leg.greeks.delta == 0.48 and leg.oi_change == 100
    assert chain.strikes[0].pe is None and chain.strikes[0].ce.greeks.theta == 0.0


async def test_bad_dates_and_segments_never_reach_the_broker():
    requests: list[httpx.Request] = []
    async with _client(_broker(requests)) as client:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            await client.get_option_chain("40000001", "20261013")
        with pytest.raises(ValueError, match="INDEX or EQUITY"):
            await client.get_option_chain("40000001", "2026-10-13", segment="DERIVATIVE")
    assert requests == []


async def test_expiries_are_sorted_and_search_passes_filters():
    requests: list[httpx.Request] = []
    async with _client(_broker(requests)) as client:
        assert await client.get_expiries("nifty") == ["2026-10-13", "2026-10-20"]
        count, rows = await client.search_derivatives(
            "NIFTY", instrument_type="OPTIDX", expiry="2026-10-13", option_type="CE",
            strike_from=24500, strike_to=24500, page_size=500)
    assert requests[0].url.params["underlying"] == "NIFTY"
    q = requests[1].url.params
    assert (q["segment"], q["instrument_type"], q["option_type"], q["page_size"]) == (
        "DERIVATIVE", "OPTIDX", "CE", "100")
    assert count == 1 and rows[0].security_id == "45110" and rows[0].lot_size == 75


async def test_margin_sends_the_documented_body():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return ok({"total_margin": 750, "insufficient_balance": 0,
                   "charges": {"brokerage": 10, "gst": 1.8, "IPFTCharges": 0.01,
                               "total_charges": 11.81}})

    async with _client(handler) as client:
        m = await client.get_margin(security_id="40131", side="BUY", quantity=75, price=10,
                                    product="MARGIN")
    assert seen[0].method == "GET" and seen[0].url.path == "/margin"
    assert json.loads(seen[0].content) == {
        "segment": "DERIVATIVE", "exchange": "NSE", "securityID": "40131", "txnType": "BUY",
        "quantity": "75", "price": "10", "product": "MARGIN"}
    assert m.total_margin == 750 and m.sufficient and m.charges.ipft_charges == 0.01


async def test_derivative_positions_query_margin_and_intraday():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        product = request.url.params["product"]
        return ok([{"symbol": f"NIFTY FUT {product}", "net_qty": 75, "avg_price": 24500,
                    "security_id": 58072}])

    async with _client(handler) as client:
        rows = await client.get_derivative_positions()
    assert [(r.url.params["segment"], r.url.params["product"]) for r in seen] == [
        ("derivative", "margin"), ("derivative", "intraday")]
    assert [p.product for p in rows] == ["MARGIN", "INTRADAY"]


async def test_funds_reads_the_per_segment_balances():
    def handler(request):
        return ok({"pledge_received": 0, "detailed_avl_balance": {
            "eq_cnc": 2980.4, "eq_mis": 2980.4, "option_buy": 4449.65, "option_sell": 2980.4,
            "future": 2980.4}})

    async with _client(handler) as client:
        funds = await client.get_funds()
    assert funds.available_cash == 2980.4
    assert (funds.option_buy_available, funds.futures_available) == (4449.65, 2980.4)


async def test_fno_lookups_and_the_chain():
    from skopaq.options.chain import fetch_option_chain

    requests: list[httpx.Request] = []
    async with _client(_broker(requests)) as client:
        und = await fno.resolve_underlying(client, "nifty 50")
        assert (und.symbol, und.exchange, und.segment, und.security_id) == (
            "NIFTY", "NSE", "INDEX", "40000001")
        sensex = await fno.resolve_underlying(client, "SENSEX")
        assert (sensex.exchange, sensex.security_id) == ("BSE", "40000100")
        assert await fno.expiry_at(client, "NIFTY", 5) == "2026-10-20"     # clamped
        contract = await fno.find_option(client, "NIFTY", "2026-10-13", 24500, "ce")
        assert contract.security_id == "45110"

        chain = await fetch_option_chain(client, "NIFTY", 0)

    assert chain.lot_size == 75 and chain.expiry == date(2026, 10, 13)
    assert chain.expiries == ["2026-10-13", "2026-10-20"]
    ce = next(c for c in chain.calls if c.strike == 24500)
    assert ce.security_id == "45110" and ce.exchange == "NFO" and ce.is_otm
    assert ce.theta_estimate == 9.5 and ce.delta == 0.48
    itm_call = next(c for c in chain.calls if c.strike == 24450)
    assert itm_call.distance_pct < 0                                    # ITM kept, signed
    # the index file was downloaded once despite two lookups
    assert sum(r.url.path == "/market/instruments" for r in requests) == 1


def test_parse_index_csv_reads_by_position():
    ids = fno.parse_index_csv(INDEX_CSV)
    assert ids[("NSE", "NIFTY 50")] == "40000001" and ids[("BSE", "SENSEX")] == "40000100"
    assert fno.lots_to_quantity(2, 75) == 150
    with pytest.raises(ValueError):
        fno.lots_to_quantity(0, 75)


def _fno_order(**kw) -> OrderRequest:
    base = dict(symbol="NIFTY-Oct2026-24500-CE", side=Side.BUY, quantity=Decimal(150),
                price=150.0, segment=Segment.DERIVATIVE, product=Product.MARGIN,
                lot_size=75, security_id="45110")
    base.update(kw)
    return OrderRequest(**base)


def test_derivative_order_rules():
    order = _fno_order()
    assert order.lots == 2 and order.product.value == "MARGIN"
    assert Product.NRML is Product.MARGIN and Product.MIS is Product.INTRADAY
    with pytest.raises(ValueError, match="multiple of the lot size"):
        _fno_order(quantity=Decimal(100))
    with pytest.raises(ValueError, match="not CNC"):
        _fno_order(product=Product.CNC)
    with pytest.raises(ValueError, match="for derivatives"):
        OrderRequest(symbol="TCS", side=Side.BUY, quantity=1, product=Product.MARGIN)


def test_max_lots_counts_lots_for_derivatives():
    checker = SafetyChecker()
    checker.set_order_limits(lots=5)                          # the dashboard's default
    rejections: list[str] = []
    checker._check_max_lots(_fno_order(quantity=Decimal(75 * 5)), rejections)
    assert rejections == []                                  # 5 lots = the limit
    checker._check_max_lots(_fno_order(quantity=Decimal(75 * 6)), rejections)
    assert rejections and "6 lots" in rejections[0]


def test_indstocks_chain_symbols_count_as_options_for_the_naked_sell_rule():
    """An option symbol SELL outside the DERIVATIVE segment is refused outright; an F&O
    SELL is left to the no-short-sale check (it may only close a long position)."""
    checker = SafetyChecker()
    rejections: list[str] = []
    checker._check_naked_options(
        OrderRequest(symbol="NIFTY-Oct2026-24500-CE", side=Side.SELL, quantity=Decimal(75),
                     price=150.0, product=Product.INTRADAY), rejections)
    assert rejections and "Naked option" in rejections[0]
    rejections = []
    checker._check_naked_options(_fno_order(side=Side.SELL), rejections)
    assert rejections == []
    short = checker._check_no_short_sale(_fno_order(side=Side.SELL), [], [], rejections)
    assert short == "no-short-sale" and "No short sales" in rejections[0]
