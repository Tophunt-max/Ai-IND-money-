"""Versioned charges (skopaq/finance/charges.py) and the execution ledger (ledger.py)."""

from __future__ import annotations

from datetime import date

import pytest

from skopaq.finance import charges as ch
from skopaq.finance.ledger import Fill, PositionLedger
from skopaq.scalping.costs import round_trip_cost
from skopaq.scalping.fno_rules import fno_round_trip_cost


def test_the_schedule_reproduces_the_existing_estimates():
    assert ch.round_trip(ch.EQUITY_INTRADAY, 1000, 1010, 50).total == pytest.approx(
        round_trip_cost(1000, 1010, 50), abs=0.01)
    assert ch.round_trip(ch.OPTIONS, 100, 120, 75).total == pytest.approx(
        fno_round_trip_cost(100, 120, 75), abs=0.01)
    assert ch.round_trip(ch.FUTURES, 24000, 24100, 75).total == pytest.approx(
        fno_round_trip_cost(24000, 24100, 75, future=True), abs=0.01)


def test_gst_is_on_brokerage_exchange_and_sebi_only():
    c = ch.leg_charges(ch.EQUITY_DELIVERY, "BUY", 100_000)
    assert c.gst == pytest.approx(0.18 * (c.brokerage + c.exchange + c.sebi))
    assert c.stt == pytest.approx(100.0) and c.stamp == pytest.approx(15.0)


def test_product_specific_stt_sides():
    # Delivery: both sides; intraday and F&O: sell side only (options on the premium)
    assert ch.leg_charges(ch.EQUITY_DELIVERY, "SELL", 100_000).stt == pytest.approx(100)
    assert ch.leg_charges(ch.EQUITY_INTRADAY, "BUY", 100_000).stt == 0
    assert ch.leg_charges(ch.OPTIONS, "BUY", 10_000).stt == 0
    assert ch.leg_charges(ch.OPTIONS, "SELL", 10_000).stt == pytest.approx(10)
    assert ch.leg_charges(ch.FUTURES, "SELL", 1_000_000).stt == pytest.approx(200)
    with pytest.raises(ValueError):
        ch.leg_charges("crypto", "BUY", 1)


def test_brokerage_is_per_order_and_zero_orders_charges_values_only():
    two = ch.leg_charges(ch.EQUITY_INTRADAY, "BUY", 10_000, orders=2)
    assert two.brokerage == 20
    rest = ch.leg_charges(ch.EQUITY_INTRADAY, "BUY", 10_000, orders=0)
    assert rest.brokerage == 0 and rest.exchange > 0


def test_breakeven_exit_nets_to_zero_and_schedule_versions():
    be = ch.breakeven_exit(ch.EQUITY_INTRADAY, 500.0, 100)
    assert be > 500
    net = (be - 500) * 100 - ch.round_trip(ch.EQUITY_INTRADAY, 500, be, 100).total
    assert net == pytest.approx(0, abs=1e-6)
    assert ch.schedule_for(date(2026, 1, 1)).version == "nse-indstocks-2024-10"
    assert ch.round_trip(ch.OPTIONS, 1, 1, 1).schedule == ch.CURRENT.version
    assert ch.product_of("DERIVATIVE", symbol="NIFTY26OCT24000CE") == ch.OPTIONS
    assert ch.product_of("DERIVATIVE", symbol="NIFTY26OCTFUT") == ch.FUTURES
    assert ch.product_of("EQUITY", "INTRADAY") == ch.EQUITY_INTRADAY
    assert ch.product_of("EQUITY", "CNC") == ch.EQUITY_DELIVERY


def test_ledger_partial_fills_charge_each_order_once():
    led = PositionLedger("TCS", ch.EQUITY_INTRADAY)
    led.request("B1", "BUY", 10)
    assert led.add_fill(Fill("B1", "BUY", 4, 100.0, expected_price=99.9, fill_id="t1"))
    assert led.add_fill(Fill("B1", "BUY", 6, 101.0, fill_id="t2"))
    assert not led.add_fill(Fill("B1", "BUY", 6, 101.0, fill_id="t2"))      # duplicate
    assert led.avg_entry() == pytest.approx(100.6)
    assert led.add_fill(Fill("S1", "SELL", 5, 103.0))
    assert led.add_fill(Fill("S2", "SELL", 5, 99.0))
    assert led.closed and led.qty == 0
    assert led.realized_gross == pytest.approx((103 - 100.6) * 5 + (99 - 100.6) * 5)
    est = led.estimated_charges()
    assert est.brokerage == 30                     # three orders, not four fills
    s = led.summary()
    assert s["net_status"] == "estimated" and s["charges_used"] == "estimated"
    assert s["net_total"] == pytest.approx(led.realized_gross - est.total, abs=0.01)
    assert s["slippage"] == pytest.approx(0.4)     # 4 × ₹0.10 worse than expected


def test_ledger_refuses_an_oversell_and_reports_charge_discrepancies():
    led = PositionLedger("TCS", ch.EQUITY_INTRADAY)
    led.add_fill(Fill("B1", "BUY", 5, 100.0))
    assert not led.add_fill(Fill("S1", "SELL", 6, 101.0))
    assert "exceeds the open" in led.discrepancies[0]
    assert led.unrealized(102.0) == pytest.approx(10.0)
    led.add_fill(Fill("S1", "SELL", 5, 101.0))
    est = led.estimated_charges()
    led.confirm_charges(ch.Charges(brokerage=est.brokerage, stt=est.stt + 5,
                                   exchange=est.exchange, sebi=est.sebi, stamp=est.stamp,
                                   gst=est.gst, schedule="contract-note"))
    s = led.summary()
    assert s["net_status"] == "reconciled" and s["charges_used"] == "confirmed"
    assert any("charges: broker" in d for d in s["discrepancies"])
    assert s["net_realized_confirmed"] == pytest.approx(5 - est.total - 5, abs=0.01)


def test_no_fill_means_no_pnl():
    led = PositionLedger("TCS", ch.EQUITY_INTRADAY)
    led.request("B1", "BUY", 10)
    assert led.summary()["gross_realized"] == 0 and led.estimated_charges().total == 0
    assert led.summary()["net_status"] == "open"
