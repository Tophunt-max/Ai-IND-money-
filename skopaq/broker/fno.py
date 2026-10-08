"""F&O instrument lookups on INDstocks: underlyings, expiries, lot sizes, contracts.

INDstocks addresses an option chain by the *underlying's* SECURITY_ID (an index id from
``/market/instruments?source=index``, or a stock's cash-market id) and orders by the
*contract's* SECURITY_ID (``/market/instruments/search``). This module turns names such
as ``NIFTY`` or ``RELIANCE`` into both.

Usage::

    async with INDstocksClient(config, token_mgr) as client:
        und = await resolve_underlying(client, "NIFTY")        # NSE INDEX 40000001
        expiry = (await client.get_expiries("NIFTY"))[0]
        contract = await find_option(client, "NIFTY", expiry, 24500, "CE")
        # contract.security_id, contract.lot_size → OrderRequest(segment=DERIVATIVE, ...)
"""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass

from skopaq.broker.client import INDstocksClient
from skopaq.broker.models import DerivativeContract, InstrumentType, OptionType
from skopaq.broker.scrip_resolver import resolve_security_id

logger = logging.getLogger(__name__)

# Trading name → (exchange, names it may carry in the index instruments file)
INDEX_UNDERLYINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "NIFTY": ("NSE", ("NIFTY 50", "NIFTY")),
    "BANKNIFTY": ("NSE", ("NIFTY BANK", "BANKNIFTY")),
    "FINNIFTY": ("NSE", ("NIFTY FIN SERVICE", "NIFTY FINANCIAL SERVICES", "FINNIFTY")),
    "MIDCPNIFTY": ("NSE", ("NIFTY MID SELECT", "NIFTY MIDCAP SELECT", "MIDCPNIFTY")),
    "NIFTYNXT50": ("NSE", ("NIFTY NEXT 50", "NIFTYNXT50")),
    "SENSEX": ("BSE", ("SENSEX", "BSE SENSEX", "S&P BSE SENSEX")),
    "BANKEX": ("BSE", ("BANKEX", "BSE BANKEX", "S&P BSE BANKEX")),
}
# Names people type for the same index
_ALIASES = {"NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "NIFTY BANK": "BANKNIFTY",
            "NIFTY FIN SERVICE": "FINNIFTY", "NIFTY NEXT 50": "NIFTYNXT50"}

_CACHE_TTL = 3600.0
_index_ids: dict[tuple[str, str], str] = {}   # ("NSE", "NIFTY 50") → "40000001"
_index_ts = 0.0
_lot_cache: dict[tuple[str, str], tuple[float, int]] = {}  # (underlying, expiry) → (ts, lot)


@dataclass(frozen=True)
class Underlying:
    """What the option-chain endpoint needs for one underlying."""

    symbol: str          # trading name: NIFTY, BANKNIFTY, RELIANCE
    exchange: str        # NSE / BSE
    segment: str         # INDEX / EQUITY (the option-chain ``segment``)
    security_id: str     # the underlying's id (``underlying-scrip``)

    @property
    def is_index(self) -> bool:
        return self.segment == "INDEX"


def canonical_underlying(symbol: str) -> str:
    """``nifty 50`` → ``NIFTY``; stock names are upper-cased."""
    name = " ".join(symbol.strip().upper().split())
    return _ALIASES.get(name, name)


def parse_index_csv(text: str) -> dict[tuple[str, str], str]:
    """The index instruments file: ``EXCH,SEGMENT,SECURITY_ID`` where the second column
    holds the index *name*. Read by position, never by header."""
    out: dict[tuple[str, str], str] = {}
    reader = csv.reader(io.StringIO(text))
    for i, row in enumerate(reader):
        if len(row) < 3:
            continue
        exch, name, sid = (c.strip() for c in row[:3])
        if i == 0 and sid.upper() == "SECURITY_ID":
            continue
        if exch and name and sid:
            out[(exch.upper(), " ".join(name.upper().split()))] = sid
    return out


async def index_id(client: INDstocksClient, exchange: str, names: tuple[str, ...]) -> str:
    """The SECURITY_ID of an index (any of ``names``) in the index instruments file."""
    global _index_ids, _index_ts
    if not _index_ids or time.time() - _index_ts > _CACHE_TTL:
        _index_ids = parse_index_csv(await client.get_instruments(source="index"))
        _index_ts = time.time()
        logger.info("Index instruments loaded: %d indices", len(_index_ids))
    for name in names:
        sid = _index_ids.get((exchange.upper(), " ".join(name.upper().split())))
        if sid:
            return sid
    raise ValueError(f"Index {names[0]} not found in the INDstocks index instruments file")


async def _index_security_id(client: INDstocksClient, symbol: str) -> tuple[str, str]:
    exchange, names = INDEX_UNDERLYINGS[symbol]
    return exchange, await index_id(client, exchange, names)


# Market-data codes of an index, in the order they are tried. The docs give the
# WebSocket prefix (NIDX:/BIDX:) but no REST example for an index, so the first code that
# answers a price is remembered (per exchange) and used from then on.
_index_prefix: dict[str, str] = {}


def index_code_candidates(exchange: str, security_id: str) -> list[str]:
    bse = exchange.upper() == "BSE"
    found = _index_prefix.get(exchange.upper())
    order = [found] if found else []
    order += [p for p in (("BIDX", "BSE") if bse else ("NIDX", "NSE")) if p != found]
    return [f"{p}_{security_id}" for p in order]


async def index_scrip_code(client: INDstocksClient, exchange: str, security_id: str) -> str:
    """The REST scrip code that prices this index (``NIDX_<id>`` first). Falls back to
    the first candidate when none answers (e.g. outside a session)."""
    candidates = index_code_candidates(exchange, security_id)
    # One code per request: a code the broker does not know may fail the whole request
    for code in candidates:
        try:
            quotes = await client.get_quotes([code], [code])
        except Exception:
            logger.debug("Index code %s did not answer", code, exc_info=True)
            continue
        if any(q.symbol == code and float(q.ltp or 0) > 0 for q in quotes):
            _index_prefix[exchange.upper()] = code.split("_", 1)[0]
            return code
    return candidates[0]


async def resolve_underlying(client: INDstocksClient, symbol: str) -> Underlying:
    """An index (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX) or an
    NSE stock with F&O. Raises ValueError for an unknown name."""
    name = canonical_underlying(symbol)
    if name in INDEX_UNDERLYINGS:
        exchange, sid = await _index_security_id(client, name)
        return Underlying(name, exchange, "INDEX", sid)
    sid = await resolve_security_id(client, name, "NSE")
    return Underlying(name, "NSE", "EQUITY", sid)


async def expiry_at(client: INDstocksClient, symbol: str, index: int = 0) -> str:
    """The ``index``-th upcoming expiry (0 = nearest); the last one if fewer exist."""
    expiries = await client.get_expiries(canonical_underlying(symbol))
    if not expiries:
        raise ValueError(f"No upcoming F&O expiries for {symbol}")
    return expiries[min(max(index, 0), len(expiries) - 1)]


async def lot_size(client: INDstocksClient, symbol: str, expiry: str) -> int:
    """Contract lot size of an underlying for one expiry (cached for an hour)."""
    name = canonical_underlying(symbol)
    key = (name, expiry)
    hit = _lot_cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    _, rows = await client.search_derivatives(name, expiry=expiry, page_size=1)
    if not rows:
        raise ValueError(f"No {name} contracts for expiry {expiry}")
    lot = max(1, int(rows[0].lot_size or 1))
    _lot_cache[key] = (time.time(), lot)
    return lot


async def find_option(
    client: INDstocksClient,
    symbol: str,
    expiry: str,
    strike: float,
    option_type: str,
) -> DerivativeContract:
    """The option contract at exactly ``strike`` (CE/PE) for ``expiry``."""
    name = canonical_underlying(symbol)
    side = OptionType(option_type.upper())
    _, rows = await client.search_derivatives(
        name, expiry=expiry, option_type=side.value, strike_from=strike, strike_to=strike,
        page_size=10,
    )
    for row in rows:
        if row.strike_price is not None and abs(float(row.strike_price) - strike) < 1e-6 \
                and (row.option_type or "").upper() == side.value:
            return row
    raise ValueError(f"No {name} {expiry} {strike:g} {side.value} contract")


async def nearest_future(
    client: INDstocksClient, symbol: str, index: int = 0,
) -> DerivativeContract:
    """The ``index``-th future (0 = current month) of an index or stock."""
    name = canonical_underlying(symbol)
    kind = InstrumentType.FUTIDX if name in INDEX_UNDERLYINGS else InstrumentType.FUTSTK
    _, rows = await client.search_derivatives(name, instrument_type=kind.value, page_size=10)
    futures = sorted((r for r in rows if r.is_future and r.expiry), key=lambda r: r.expiry)
    if not futures:
        raise ValueError(f"No {name} futures")
    return futures[min(max(index, 0), len(futures) - 1)]


def lots_to_quantity(lots: int, lot: int) -> int:
    """Order quantity (units) for ``lots`` lots; INDstocks takes F&O ``qty`` in units."""
    if lots < 1:
        raise ValueError("lots must be at least 1")
    return int(lots) * int(lot)


def clear_caches() -> None:
    """Forget the cached index ids and lot sizes (tests)."""
    global _index_ids, _index_ts
    _index_ids = {}
    _index_ts = 0.0
    _lot_cache.clear()
    _index_prefix.clear()


__all__ = [
    "INDEX_UNDERLYINGS", "Underlying", "canonical_underlying", "clear_caches", "expiry_at",
    "find_option", "index_code_candidates", "index_id", "index_scrip_code", "lot_size",
    "lots_to_quantity", "nearest_future", "parse_index_csv", "resolve_underlying",
]
