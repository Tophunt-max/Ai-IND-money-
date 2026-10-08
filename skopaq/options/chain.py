"""Option chain via INDstocks (``GET /market/option-chain``).

Fetches the chain for an index (NIFTY, BANKNIFTY, FINNIFTY, ...) or an F&O stock for one
expiry, with live price, OI, IV and Greeks per leg, and adds the metrics the strategy
selectors use (distance from spot, days to expiry, daily theta).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from skopaq.broker.models import OptionLeg

logger = logging.getLogger(__name__)


@dataclass
class OptionContract:
    """A single option contract with computed metrics."""

    tradingsymbol: str
    security_id: str     # the contract's id: pass it to OrderRequest.security_id
    exchange: str        # NFO / BFO (derivatives segment of the exchange)
    strike: float
    option_type: str     # "CE" or "PE"
    expiry: date
    lot_size: int

    # Live data
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    oi: int = 0
    oi_change: int = 0
    iv: float = 0.0      # implied volatility, percent
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0   # per day, as reported (negative for a long option)
    vega: float = 0.0

    # Computed metrics
    spot_price: float = 0.0
    distance_pct: float = 0.0       # % out of the money; negative when in the money
    premium_yield_pct: float = 0.0  # premium / spot, percent
    days_to_expiry: int = 0
    theta_estimate: float = 0.0     # daily time decay (≥ 0)

    @property
    def is_otm(self) -> bool:
        return self.distance_pct > 0


@dataclass
class OptionChainData:
    """Complete option chain for a symbol and expiry, strikes ascending."""

    symbol: str
    spot_price: float
    expiry: date
    calls: list[OptionContract] = field(default_factory=list)
    puts: list[OptionContract] = field(default_factory=list)
    lot_size: int = 1
    expiries: list[str] = field(default_factory=list)  # every upcoming expiry
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def atm_strike(self) -> Optional[float]:
        strikes = sorted({c.strike for c in self.calls} | {p.strike for p in self.puts})
        if not strikes:
            return None
        return min(strikes, key=lambda k: abs(k - self.spot_price))


def _contract(
    leg: OptionLeg, *, strike: float, option_type: str, expiry: date, lot: int, spot: float,
    exchange: str, today: date,
) -> OptionContract:
    if spot > 0:
        if option_type == "CE":
            distance = (strike - spot) / spot * 100
        else:
            distance = (spot - strike) / spot * 100
    else:
        distance = 0.0
    dte = max((expiry - today).days, 1)
    theta = float(leg.greeks.theta or 0.0)
    return OptionContract(
        tradingsymbol=leg.trading_symbol,
        security_id=leg.security_id,
        exchange=exchange,
        strike=strike,
        option_type=option_type,
        expiry=expiry,
        lot_size=lot,
        ltp=leg.last_price,
        bid=leg.top_bid_price,
        ask=leg.top_ask_price,
        volume=leg.volume,
        oi=leg.oi,
        oi_change=leg.oi_change,
        iv=leg.iv,
        delta=leg.greeks.delta,
        gamma=leg.greeks.gamma,
        theta=theta,
        vega=leg.greeks.vega,
        spot_price=spot,
        distance_pct=distance,
        premium_yield_pct=(leg.last_price / spot * 100) if spot > 0 else 0.0,
        days_to_expiry=dte,
        theta_estimate=abs(theta) if theta else leg.last_price / dte,
    )


async def fetch_option_chain(
    client,
    symbol: str = "NIFTY",
    expiry_index: int = 0,  # 0 = nearest expiry, 1 = next, etc.
    strike_count: int = 15,
) -> OptionChainData:
    """Fetch the option chain for a symbol via INDstocks.

    Args:
        client: An open ``INDstocksClient``.
        symbol: Underlying (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, RELIANCE, ...).
        expiry_index: 0 = nearest expiry, 1 = next, etc. (the last one if out of range).
        strike_count: strikes on each side of the money (the chain has 2n + 1).

    Returns:
        OptionChainData with every call and put of the selected expiry, ITM and OTM.
    """
    from skopaq.broker import fno

    underlying = await fno.resolve_underlying(client, symbol)
    expiries = await client.get_expiries(underlying.symbol)
    if not expiries:
        raise ValueError(f"No upcoming F&O expiries for {underlying.symbol}")
    if expiry_index >= len(expiries) or expiry_index < 0:
        expiry_index = 0
    expiry = expiries[expiry_index]
    logger.info("Option chain %s expiry %s (index %d of %d)",
                underlying.symbol, expiry, expiry_index, len(expiries))

    chain = await client.get_option_chain(
        underlying.security_id, expiry, segment=underlying.segment,
        exchange=underlying.exchange, strike_count=strike_count,
    )
    lot = await fno.lot_size(client, underlying.symbol, expiry)

    expiry_date = date.fromisoformat(chain.expiry or expiry)
    spot = chain.underlying_ltp
    exchange = "BFO" if underlying.exchange == "BSE" else "NFO"
    today = date.today()

    calls: list[OptionContract] = []
    puts: list[OptionContract] = []
    for row in chain.strikes:
        common = dict(strike=row.strike, expiry=expiry_date, lot=lot, spot=spot,
                      exchange=exchange, today=today)
        if row.ce and row.ce.security_id:
            calls.append(_contract(row.ce, option_type="CE", **common))
        if row.pe and row.pe.security_id:
            puts.append(_contract(row.pe, option_type="PE", **common))

    calls.sort(key=lambda c: c.strike)
    puts.sort(key=lambda p: p.strike)
    return OptionChainData(
        symbol=underlying.symbol,
        spot_price=spot,
        expiry=expiry_date,
        calls=calls,
        puts=puts,
        lot_size=lot,
        expiries=list(expiries),
    )


async def load_option_chain(
    symbol: str = "NIFTY",
    expiry_index: int = 0,
    *,
    config=None,
    strike_count: int = 15,
) -> OptionChainData:
    """:func:`fetch_option_chain` with its own INDstocks client (read only: market data
    needs no whitelisted IP)."""
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager
    from skopaq.config import SkopaqConfig

    async with INDstocksClient(config or SkopaqConfig(), TokenManager()) as client:
        return await fetch_option_chain(client, symbol, expiry_index, strike_count)
