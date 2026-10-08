"""Round-trip charges of an NSE equity intraday trade at INDstocks (estimate).

Brokerage is the INDstocks API fee (₹10 per executed order, GST on top). The statutory
charges are NSE's published intraday rates; ``INDstocksClient.get_margin`` gives the exact
charges of one order when it matters. A scalp is only worth taking when its target pays
these several times over (``scalp_min_reward_to_cost``).
"""

from __future__ import annotations

from skopaq.constants import GST_RATE, INDSTOCKS_BROKERAGE_PER_ORDER_INR

STT_INTRADAY_SELL = 0.00025       # 0.025 % on the sell side
EXCHANGE_TXN = 0.0000297          # NSE transaction charge, each side
SEBI_FEE = 0.000001               # ₹10 per crore, each side
STAMP_DUTY_BUY = 0.00003          # 0.003 % on the buy side


def round_trip_cost(entry: float, exit_price: float, qty: int) -> float:
    """₹ charges of buying ``qty`` at ``entry`` and selling at ``exit_price`` intraday."""
    if qty <= 0:
        return 0.0
    buy, sell = entry * qty, exit_price * qty
    brokerage = 2 * INDSTOCKS_BROKERAGE_PER_ORDER_INR
    exchange = (buy + sell) * EXCHANGE_TXN
    sebi = (buy + sell) * SEBI_FEE
    gst = GST_RATE * (brokerage + exchange + sebi)
    return round(brokerage + exchange + sebi + gst + sell * STT_INTRADAY_SELL
                 + buy * STAMP_DUTY_BUY, 2)
