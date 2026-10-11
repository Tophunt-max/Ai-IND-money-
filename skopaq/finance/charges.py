"""Versioned, product-specific trading charges for NSE orders at INDstocks (estimates).

One ``ChargeSchedule`` holds every rate with its version, effective date and source, so a
stored estimate says which rates produced it, and a rate change is a new schedule rather
than an edited constant. ``CURRENT`` is the schedule in force; ``skopaq.scalping.costs``
and ``skopaq.scalping.fno_rules`` read their rates from it.

Charges are per *order* (one brokerage per executed order, however many partial fills it
had), so ``leg_charges`` takes the order's total traded value. GST is levied on brokerage
+ exchange transaction charges + SEBI fees, never on STT or stamp duty.

Estimates are not confirmed amounts: the broker's contract note is authoritative, and
``skopaq.finance.ledger`` keeps both and reports the difference. Components a schedule
does not model are listed in ``excluded`` so nobody mistakes them for zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

EQUITY_INTRADAY = "equity_intraday"
EQUITY_DELIVERY = "equity_delivery"
OPTIONS = "options"
FUTURES = "futures"
PRODUCTS = (EQUITY_INTRADAY, EQUITY_DELIVERY, OPTIONS, FUTURES)


@dataclass(frozen=True)
class ProductRates:
    stt_buy: float = 0.0           # fraction of the buy value
    stt_sell: float = 0.0          # fraction of the sell value (options: premium)
    exchange_txn: float = 0.0      # NSE transaction charge, each side
    sebi: float = 0.000001         # ₹10 per crore, each side
    stamp_buy: float = 0.0         # stamp duty, buy side
    dp_per_sell_order: float = 0.0  # depository charge per delivery sell (₹, before GST)


@dataclass(frozen=True)
class ChargeSchedule:
    version: str
    effective_from: date
    source: str
    brokerage_per_order: float
    gst_rate: float
    products: dict[str, ProductRates]
    excluded: tuple[str, ...] = ()
    notes: str = ""

    def rates(self, product: str) -> ProductRates:
        try:
            return self.products[product]
        except KeyError:
            raise ValueError(f"Unknown product {product!r}; one of {PRODUCTS}") from None


@dataclass(frozen=True)
class Charges:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    dp: float = 0.0
    gst: float = 0.0
    schedule: str = ""
    estimated: bool = True
    other: float = 0.0     # broker totals not split into components (contract notes)

    @property
    def total(self) -> float:
        return (self.brokerage + self.stt + self.exchange + self.sebi + self.stamp
                + self.dp + self.gst + self.other)

    def __add__(self, other: "Charges") -> "Charges":
        return Charges(
            brokerage=self.brokerage + other.brokerage, stt=self.stt + other.stt,
            exchange=self.exchange + other.exchange, sebi=self.sebi + other.sebi,
            stamp=self.stamp + other.stamp, dp=self.dp + other.dp, gst=self.gst + other.gst,
            other=self.other + other.other,
            schedule=self.schedule or other.schedule,
            estimated=self.estimated or other.estimated)

    def as_dict(self) -> dict:
        out = {k: round(getattr(self, k), 4) for k in
               ("brokerage", "stt", "exchange", "sebi", "stamp", "dp", "gst", "other")}
        out.update(total=round(self.total, 2), schedule=self.schedule, estimated=self.estimated)
        return out


# NSE rates in force from 2024-10-01 (STT on F&O raised by the Finance Act 2024; the
# exchange revised transaction charges the same day). INDstocks API brokerage: ₹10 per
# executed order (https://api-docs.indstocks.com/api-overview/).
SCHEDULE_2024_10 = ChargeSchedule(
    version="nse-indstocks-2024-10",
    effective_from=date(2024, 10, 1),
    source="NSE circulars (STT: Finance (No. 2) Act 2024; txn charges from 2024-10-01); "
           "INDstocks API pricing",
    brokerage_per_order=10.0,
    gst_rate=0.18,
    products={
        EQUITY_INTRADAY: ProductRates(stt_sell=0.00025, exchange_txn=0.0000297,
                                      stamp_buy=0.00003),
        EQUITY_DELIVERY: ProductRates(stt_buy=0.001, stt_sell=0.001, exchange_txn=0.0000297,
                                      stamp_buy=0.00015),
        OPTIONS: ProductRates(stt_sell=0.001, exchange_txn=0.0003503, stamp_buy=0.00003),
        FUTURES: ProductRates(stt_sell=0.0002, exchange_txn=0.0000173, stamp_buy=0.00002),
    },
    excluded=("NSE IPFT", "clearing charges", "DP charges (INDstocks rate not verified)",
              "STT on exercised/ITM-expired options", "BSE rates"),
    notes="Verify against the current INDstocks charge sheet and contract notes before "
          "relying on it for live P&L.",
)

SCHEDULES = (SCHEDULE_2024_10,)
CURRENT = SCHEDULE_2024_10


def schedule_for(day: Optional[date] = None) -> ChargeSchedule:
    """The schedule in force on ``day`` (default today)."""
    day = day or date.today()
    found = [s for s in SCHEDULES if s.effective_from <= day]
    return max(found, key=lambda s: s.effective_from) if found else SCHEDULES[0]


def leg_charges(product: str, side: str, value: float, *, orders: int = 1,
                schedule: ChargeSchedule = CURRENT) -> Charges:
    """Charges of one side: ``value`` is the traded value (price × qty, options: premium)
    of ``orders`` executed orders (brokerage is per order, not per fill). ``orders=0``
    gives the value-based charges alone: a later part of an order already charged its
    brokerage."""
    if value <= 0 or orders < 0:
        return Charges(schedule=schedule.version)
    r = schedule.rates(product)
    buy = side.upper() == "BUY"
    brokerage = schedule.brokerage_per_order * orders
    exchange = value * r.exchange_txn
    sebi = value * r.sebi
    dp = 0.0 if buy else r.dp_per_sell_order * orders
    gst = schedule.gst_rate * (brokerage + exchange + sebi + dp)
    stt = value * (r.stt_buy if buy else r.stt_sell)
    stamp = value * r.stamp_buy if buy else 0.0
    return Charges(brokerage=brokerage, stt=stt, exchange=exchange, sebi=sebi, stamp=stamp,
                   dp=dp, gst=gst, schedule=schedule.version)


def round_trip(product: str, entry: float, exit_price: float, qty: float, *,
               schedule: ChargeSchedule = CURRENT) -> Charges:
    """Buy ``qty`` at ``entry`` and sell at ``exit_price``: one order each side."""
    if qty <= 0:
        return Charges(schedule=schedule.version)
    return (leg_charges(product, "BUY", entry * qty, schedule=schedule)
            + leg_charges(product, "SELL", exit_price * qty, schedule=schedule))


def breakeven_exit(product: str, entry: float, qty: float, *,
                   schedule: ChargeSchedule = CURRENT) -> float:
    """The sell price at which a long's net P&L is zero after the round trip's charges."""
    if qty <= 0 or entry <= 0:
        return entry
    price = entry
    for _ in range(50):                       # the cost grows slowly with the exit price
        nxt = entry + round_trip(product, entry, price, qty, schedule=schedule).total / qty
        if abs(nxt - price) < 1e-9:
            break
        price = nxt
    return price


def product_of(segment: str = "", product: str = "", symbol: str = "",
               instrument: str = "") -> str:
    """The charge product of an order: options / futures for derivatives, intraday or
    delivery for equity (``product`` INTRADAY → intraday, otherwise delivery)."""
    seg = (segment or "").upper()
    if seg == "DERIVATIVE" or instrument in (OPTIONS, FUTURES):
        if instrument == FUTURES:
            return FUTURES
        if instrument == OPTIONS:
            return OPTIONS
        return OPTIONS if symbol.upper().rstrip().endswith(("CE", "PE")) else FUTURES
    return EQUITY_INTRADAY if (product or "").upper() == "INTRADAY" else EQUITY_DELIVERY
