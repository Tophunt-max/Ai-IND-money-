"""Pydantic models for INDstocks API requests and responses.

Internal field names use Pythonic conventions (``side``, ``quantity``,
``symbol``).  Translation to INDstocks API field names (``txn_type``,
``qty``, ``security_id``) happens in ``client.py`` only.

API docs: https://api-docs.indstocks.com/
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from skopaq.constants import INDSTOCKS_BROKERAGE_PER_ORDER_INR


# ── Enums ───────────────────────────────────────────────────────────────────


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"
    BINANCE = "BINANCE"


class Segment(StrEnum):
    """INDstocks segment parameter (required for orders)."""
    EQUITY = "EQUITY"
    DERIVATIVE = "DERIVATIVE"


class Side(StrEnum):
    """BUY or SELL — maps to INDstocks ``txn_type`` in the API layer."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SLM = "SL-M"


class Product(StrEnum):
    """INDstocks product types (the values sent to the API).

    Equity: ``CNC`` (delivery) or ``INTRADAY``. Derivatives: ``MARGIN`` (carry forward,
    "NRML") or ``INTRADAY``. ``MIS`` / ``NRML`` are aliases of the same members, so either
    name can be used in code; the API always receives ``INTRADAY`` / ``MARGIN``.
    """
    CNC = "CNC"            # Cash and Carry (delivery), equity only
    INTRADAY = "INTRADAY"  # Intraday, equity or derivatives
    MARGIN = "MARGIN"      # Carry-forward derivatives
    MIS = "INTRADAY"       # alias of INTRADAY
    NRML = "MARGIN"        # alias of MARGIN


class InstrumentType(StrEnum):
    """Derivative contract types (INDstocks ``instrument_type``)."""
    OPTIDX = "OPTIDX"  # index option
    OPTSTK = "OPTSTK"  # stock option
    FUTIDX = "FUTIDX"  # index future
    FUTSTK = "FUTSTK"  # stock future


class OptionType(StrEnum):
    CE = "CE"
    PE = "PE"


class Validity(StrEnum):
    DAY = "DAY"
    IOC = "IOC"


class OrderStatus(StrEnum):
    """Not INDstocks' vocabulary; used only by the unused websocket feed.

    Parse broker statuses with ``skopaq.broker.order_status`` (``normalise_status``,
    ``parse_order_row``), never by comparing against these values.
    """

    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER PENDING"


# ── Request Models ──────────────────────────────────────────────────────────


class OrderRequest(BaseModel):
    """Parameters for placing an order.

    Internal field names are Pythonic (``symbol``, ``side``, ``quantity``).
    The ``client.py`` translates these to INDstocks API names:
        side       → txn_type
        quantity   → qty
        symbol     → (used for logging; ``security_id`` goes to API)
    """

    symbol: str                         # Human-readable (e.g. "RELIANCE")
    exchange: Exchange = Exchange.NSE
    segment: Segment = Segment.EQUITY
    side: Side
    quantity: Decimal = Field(gt=0)
    order_type: OrderType = OrderType.LIMIT
    price: Optional[float] = None       # Limit price
    trigger_price: Optional[float] = None
    product: Product = Product.CNC
    validity: Validity = Validity.DAY
    disclosed_quantity: int = 0
    is_amo: bool = False                # After Market Order

    # INDstocks-specific fields
    security_id: str = ""               # e.g. "3045" from instruments CSV
    # The exchange algo id; "" sends the configured one (NSE 99999, BSE sixteen 9s)
    algo_id: str = ""

    # Derivatives: ``quantity`` is in units (shares), and must be a whole number of
    # lots. Not sent to the API; checked here so a wrong size never reaches the broker.
    lot_size: int = Field(1, ge=1)

    # Internal tracking (not sent to API)
    internal_id: UUID = Field(default_factory=uuid4)
    tag: str = ""                       # User-defined tag

    @model_validator(mode="after")
    def _check_segment_rules(self) -> "OrderRequest":
        if self.segment == Segment.DERIVATIVE:
            if self.product == Product.CNC:
                raise ValueError("Derivative orders take product MARGIN (NRML) or INTRADAY, "
                                 "not CNC")
            if self.quantity % self.lot_size != 0:
                raise ValueError(
                    f"Derivative quantity {self.quantity} is not a multiple of the lot size "
                    f"{self.lot_size}"
                )
        elif self.product == Product.MARGIN:
            raise ValueError("Product MARGIN is for derivatives; equity takes CNC or INTRADAY")
        return self

    @property
    def lots(self) -> int:
        """Number of lots (derivatives); the quantity itself for equity."""
        return int(self.quantity) // self.lot_size


class ModifyOrderRequest(BaseModel):
    """Parameters for modifying a pending order (``POST /order/modify``)."""

    order_id: str
    segment: Segment = Segment.EQUITY
    quantity: Optional[int] = None
    price: Optional[float] = None
    order_type: Optional[OrderType] = None


class CancelOrderRequest(BaseModel):
    """Parameters for cancelling an order (``POST /order/cancel``)."""

    order_id: str
    segment: Segment = Segment.EQUITY


# ── Response Models ─────────────────────────────────────────────────────────


def _blank_to_zero(value: object) -> object:
    """None, ``""`` and ``"null"`` become 0: INDstocks sends null for numbers that
    do not apply (e.g. ``day_buy_val`` on carried-forward rows)."""
    if value is None:
        return 0
    if isinstance(value, str) and value.strip().lower() in ("", "null"):
        return 0
    return value


def _id_to_str(value: object) -> object:
    """Accept a numeric ``security_id`` (``2885``) as the string ``"2885"``."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return "" if value is None else value


class OrderResponse(BaseModel):
    """Response from INDstocks after placing/modifying/cancelling an order."""

    order_id: str = ""
    status: str = ""
    message: str = ""
    exchange_order_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class Position(BaseModel):
    """Open position from ``GET /portfolio/positions``.

    INDstocks uses shortened field names (``net_qty``, ``avg_price``,
    ``buy_qty``, etc.).  Pydantic ``validation_alias`` lets the API
    response hydrate our canonical field names automatically, while
    ``extra = "allow"`` keeps any additional fields the API may add.
    """

    symbol: str = Field("", validation_alias=AliasChoices("symbol", "trading_symbol"))
    exchange: str = ""
    # The client overwrites this with the product it queried (row values are unreliable)
    product: str = ""
    quantity: Decimal = Field(
        Decimal("0"), validation_alias=AliasChoices("net_qty", "net_quantity"),
    )
    average_price: float = Field(0.0, validation_alias="avg_price")
    last_price: float = 0.0
    pnl: float = Field(0.0, validation_alias="realized_profit")
    day_pnl: float = 0.0
    buy_quantity: Decimal = Field(Decimal("0"), validation_alias="buy_qty")
    sell_quantity: Decimal = Field(Decimal("0"), validation_alias="sell_qty")
    buy_value: float = Field(0.0, validation_alias="day_buy_val")
    sell_value: float = Field(0.0, validation_alias="day_sell_val")
    day_sell_quantity: Decimal = Field(
        Decimal("0"), validation_alias=AliasChoices("day_sell_qty", "day_sell_quantity"),
    )
    security_id: str = ""
    # The same shares have a different security id on each exchange; the ISIN is shared
    isin: str = ""

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator(
        "quantity", "average_price", "last_price", "pnl", "day_pnl", "buy_quantity",
        "sell_quantity", "buy_value", "sell_value", "day_sell_quantity", mode="before",
    )
    @classmethod
    def _blank_numbers(cls, value: object) -> object:
        return _blank_to_zero(value)

    @field_validator("security_id", "exchange", "isin", mode="before")
    @classmethod
    def _security_id_text(cls, value: object) -> object:
        return _id_to_str(value)


class Holding(BaseModel):
    """Delivery holding from ``GET /portfolio/holdings``.

    INDstocks rows carry ``symbol``, ``security_id``, ``total_qty`` (T1 + DP),
    ``used_qty`` ("pledged, sold, or otherwise blocked") and ``avg_price``; there is no
    product, LTP or P&L. ``used_quantity`` is kept but not subtracted: it may include
    shares sold today, which the negative net position already subtracts.
    """

    symbol: str = Field("", validation_alias=AliasChoices("symbol", "trading_symbol"))
    security_id: str = ""
    exchange: str = ""
    isin: str = ""
    quantity: Decimal = Field(Decimal("0"), validation_alias=AliasChoices("quantity", "total_qty"))
    average_price: float = Field(
        0.0, validation_alias=AliasChoices("average_price", "avg_price"),
    )
    used_quantity: Decimal = Field(
        Decimal("0"), validation_alias=AliasChoices("used_quantity", "used_qty"),
    )
    last_price: float = 0.0
    pnl: float = 0.0
    day_change: float = 0.0
    day_change_pct: float = 0.0

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator(
        "quantity", "average_price", "used_quantity", "last_price", "pnl", "day_change",
        "day_change_pct", mode="before",
    )
    @classmethod
    def _blank_numbers(cls, value: object) -> object:
        return _blank_to_zero(value)

    @field_validator("security_id", "exchange", "isin", mode="before")
    @classmethod
    def _security_id_text(cls, value: object) -> object:
        return _id_to_str(value)


class Quote(BaseModel):
    """Market quote from ``GET /market/quotes/full``."""

    symbol: str = ""
    exchange: str = ""
    ltp: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    change: float = 0.0
    change_pct: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    timestamp: Optional[datetime] = None

    model_config = {"extra": "allow"}


class Greeks(BaseModel):
    """Option Greeks, as returned on each option-chain leg (no rho)."""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0

    model_config = {"extra": "allow"}

    @field_validator("delta", "gamma", "theta", "vega", mode="before")
    @classmethod
    def _blank_numbers(cls, value: object) -> object:
        return _blank_to_zero(value)


class OptionLeg(BaseModel):
    """One call or put of an option-chain strike (``GET /market/option-chain``)."""

    security_id: str = ""
    trading_symbol: str = ""
    last_price: float = 0.0
    previous_close_price: float = 0.0
    oi: int = 0
    previous_oi: int = 0
    volume: int = 0
    top_bid_price: float = 0.0
    top_bid_quantity: int = 0
    top_ask_price: float = 0.0
    top_ask_quantity: int = 0
    iv: float = 0.0                      # percent: 10.5 means 10.5 %
    greeks: Greeks = Field(default_factory=Greeks)

    model_config = {"extra": "allow"}

    @field_validator(
        "last_price", "previous_close_price", "oi", "previous_oi", "volume", "top_bid_price",
        "top_bid_quantity", "top_ask_price", "top_ask_quantity", "iv", mode="before",
    )
    @classmethod
    def _blank_numbers(cls, value: object) -> object:
        return _blank_to_zero(value)

    @field_validator("security_id", mode="before")
    @classmethod
    def _security_id_text(cls, value: object) -> object:
        return _id_to_str(value)

    @field_validator("greeks", mode="before")
    @classmethod
    def _greeks_or_empty(cls, value: object) -> object:
        return value if isinstance(value, dict) else {}

    @property
    def oi_change(self) -> int:
        return self.oi - self.previous_oi


class OptionStrike(BaseModel):
    strike: float
    ce: Optional[OptionLeg] = None
    pe: Optional[OptionLeg] = None


class OptionChain(BaseModel):
    """Option chain for one underlying and one expiry, strikes in ascending order.

    The broker response carries no lot size, tick size or other expiries: take those
    from the contracts search / instruments file.
    """

    underlying_ltp: float = 0.0
    expiry: str = ""                     # YYYY-MM-DD
    strikes: list[OptionStrike] = Field(default_factory=list)


class DerivativeContract(BaseModel):
    """A derivative contract (``GET /market/instruments/search``)."""

    security_id: str = ""
    trading_symbol: str = ""
    expiry: str = ""                     # YYYY-MM-DD
    strike_price: Optional[float] = None  # None for futures
    option_type: Optional[str] = None     # CE / PE; None for futures
    lot_size: int = 1
    instrument_type: Optional[str] = None

    model_config = {"extra": "allow"}

    @field_validator("security_id", mode="before")
    @classmethod
    def _security_id_text(cls, value: object) -> object:
        return _id_to_str(value)

    @property
    def is_future(self) -> bool:
        return self.option_type is None and self.strike_price is None


class MarginCharges(BaseModel):
    stt: float = 0.0
    exchange_charges: float = 0.0
    stamp_duty: float = 0.0
    sebi_turn_over_charges: float = 0.0
    brokerage: float = 0.0
    gst: float = 0.0
    ipft_charges: float = Field(0.0, validation_alias=AliasChoices("IPFTCharges", "ipft_charges"))
    total_charges: float = 0.0

    model_config = {"extra": "allow", "populate_by_name": True}


class MarginEstimate(BaseModel):
    """Margin and charges for one order (``GET /margin``)."""

    total_margin: float = 0.0
    span_margin: float = 0.0
    exposure_margin: float = 0.0
    var_margin: float = 0.0
    delivery_margin: float = 0.0
    hedge_benefit: float = 0.0
    available_balance: float = 0.0
    insufficient_balance: float = 0.0
    brokerage: float = 0.0
    charges: MarginCharges = Field(default_factory=MarginCharges)

    model_config = {"extra": "allow"}

    @property
    def sufficient(self) -> bool:
        return self.insufficient_balance <= 0


class Funds(BaseModel):
    """Available funds from ``GET /funds``.

    ``available_cash`` is equity CNC buying power (``detailed_avl_balance.eq_cnc``); the
    other ``*_available`` fields are the per-segment balances of the same block.
    """

    available_cash: float = 0.0
    used_margin: float = 0.0
    available_margin: float = 0.0
    total_collateral: float = 0.0
    intraday_available: float = 0.0       # eq_mis
    option_buy_available: float = 0.0     # option_buy
    option_sell_available: float = 0.0    # option_sell
    futures_available: float = 0.0        # future

    model_config = {"extra": "allow"}


class UserProfile(BaseModel):
    """User profile from ``GET /user/profile``."""

    user_id: str = ""
    name: str = ""
    email: str = ""
    broker: str = "INDstocks"

    model_config = {"extra": "allow"}


class HistoricalCandle(BaseModel):
    """Single OHLCV candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


# ── Composite Models ────────────────────────────────────────────────────────


class PortfolioSnapshot(BaseModel):
    """Complete portfolio state at a point in time."""

    timestamp: datetime = Field(default_factory=datetime.now)
    total_value: Decimal = Decimal("0")
    cash: Decimal = Decimal("0")
    positions_value: Decimal = Decimal("0")
    day_pnl: Decimal = Decimal("0")
    positions: list[Position] = Field(default_factory=list)
    open_orders: int = 0


class TradingSignal(BaseModel):
    """Parsed trading signal from the agent graph."""

    symbol: str
    exchange: Exchange = Exchange.NSE
    action: str  # BUY, SELL, HOLD
    confidence: int = Field(ge=0, le=100, default=50)
    # The order's limit price; with order_type=MARKET, the reference price
    # (e.g. the LTP an exit was decided at), used as the fill estimate.
    entry_price: Optional[float] = None
    # None: LIMIT at entry_price when it is set, else MARKET. Protective exits
    # set MARKET so a stop-loss below the entry price still fills.
    order_type: Optional[OrderType] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    quantity: Optional[Decimal] = None
    reasoning: str = ""
    agent_state: dict = Field(default_factory=dict)
    # Live: a protective exit of the day's position (the monitor, CLOSING). The Executor
    # re-checks it under the SELL lock against what that position still holds after
    # Skopaq's own open, unconfirmed and not-yet-shown SELLs, so it never sells older
    # delivery holdings. An analysis SELL leaves it False (it may sell holdings). Paper
    # ignores it.
    position_only: bool = False
    # None: CNC (delivery). The scalper trades INTRADAY; its positions are its own (the
    # swing monitor and CLOSING manage CNC rows only)
    product: Optional[Product] = None
    # F&O (the options engine): segment DERIVATIVE, the contract's security id and lot
    # size; ``symbol`` is the contract's trading symbol and ``quantity`` is in units (a
    # whole number of lots). None: equity
    segment: Optional[Segment] = None
    security_id: str = ""
    lot_size: int = Field(1, ge=1)

    @property
    def is_derivative(self) -> bool:
        return self.segment == Segment.DERIVATIVE


class ExecutionResult(BaseModel):
    """Result of order execution (paper or live)."""

    success: bool
    order: Optional[OrderResponse] = None
    signal: Optional[TradingSignal] = None
    mode: str = "paper"
    safety_passed: bool = True
    rejection_reason: str = ""
    fill_price: Optional[float] = None
    slippage: float = 0.0
    brokerage: float = INDSTOCKS_BROKERAGE_PER_ORDER_INR  # INR flat per order
    timestamp: datetime = Field(default_factory=datetime.now)

    # Live only (paper keeps the defaults; consumers then use the ordered quantity).
    # Read them through the helpers below, which tolerate mock results.
    filled_quantity: Optional[Decimal] = None   # broker-confirmed total across the result's orders
    requested_quantity: Optional[Decimal] = None
    # filled | partial | rejected | cancelled | open | unknown | not_placed | late_fill
    outcome: str = ""
    order_ids: list[str] = Field(default_factory=list)   # every broker order placed, in order
    remaining_open: bool = False    # an order may still be working at the broker
    fill_unconfirmed: bool = False  # a final order whose filled quantity the broker did not report
    fill_price_source: str = ""     # trades | order | trade_book | estimate
    broker_message: str = ""        # last extra_info / broker or cancel message


# Readers for ExecutionResult's live fields. Tests pass MagicMock results whose
# attributes are truthy mocks, so every consumer reads through these.


def derivative_scrip_code(exchange: str, security_id: str) -> str:
    """The market-data scrip code of an F&O contract: ``NFO_<id>`` (NSE), ``BFO_<id>``
    (BSE). Orders send the cash exchange (NSE/BSE) with segment DERIVATIVE instead."""
    prefix = "BFO" if str(exchange).upper() in ("BSE", "BFO") else "NFO"
    return f"{prefix}_{security_id}"


def is_derivative_segment(value: object) -> bool:
    """A segment as INDstocks or Skopaq writes it (``DERIVATIVE``, ``FNO``, ``F&O``,
    ``NFO``, ``BFO``) is the derivatives one; ``""`` (unknown) is not."""
    text = str(getattr(value, "value", value) or "").upper()
    return text.startswith("DERIV") or text in ("FNO", "F&O", "FO", "NFO", "BFO")


def filled_quantity_of(result: object, default: Decimal | int) -> Decimal:
    """The broker-confirmed filled quantity, else ``default`` (paper, mocks, unknown)."""
    value = getattr(result, "filled_quantity", None)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return default if isinstance(default, Decimal) else Decimal(default)


def is_remaining_open(result: object) -> bool:
    """True only when the result says an order may still be working at the broker."""
    return getattr(result, "remaining_open", False) is True


def is_unconfirmed(result: object) -> bool:
    """An order may still be working, or a final order's fill was not reported."""
    return is_remaining_open(result) or getattr(result, "fill_unconfirmed", False) is True


def outcome_of(result: object) -> str:
    """The live outcome string, or ``""``."""
    value = getattr(result, "outcome", "")
    return value if isinstance(value, str) else ""


def order_ids_of(result: object) -> list[str]:
    """The broker order ids, or ``[]``."""
    value = getattr(result, "order_ids", None)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return []


def fill_status_of(result: object) -> str:
    """FILLED, PARTIAL (a live order filled in part), UNCONFIRMED (a live order may still
    be working, or its fill was not reported) or FAILED. Paper: FILLED or FAILED."""
    if getattr(result, "success", False):
        return "FILLED" if outcome_of(result) in ("", "filled") else "PARTIAL"
    return "UNCONFIRMED" if is_unconfirmed(result) else "FAILED"
