"""Track record (skopaq/learning/report.py) from the signal journal and the trades."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401

from skopaq.db.models import TradeRecord
from skopaq.learning import report as report_mod
from skopaq.learning.report import calibration, summarize_signals, summarize_trades


def _row(sid, status, *, filled=0, net=None, reasons=()):
    return {"strategy_id": sid, "status": status, "reasons": list(reasons),
            "execution": {"status": "FILLED", "filled": filled} if filled else None,
            "outcome": {"net_pnl": net} if net is not None else None}


def _trade(pnl, closed, confidence=None, price="100", qty="10"):
    return TradeRecord(
        symbol="TCS", side="BUY", quantity=Decimal(qty), fill_price=Decimal(price),
        pnl=None if pnl is None else Decimal(str(pnl)),
        closed_at=datetime.fromisoformat(closed),
        agent_decision={} if confidence is None else {"confidence": confidence},
    )


def test_summarize_signals_by_strategy():
    stats = summarize_signals([
        _row("swing.breakout", "QUALIFIED", filled=10, net=500.0),
        _row("swing.breakout", "QUALIFIED", filled=5, net=-200.0),
        _row("swing.trend_pullback", "REJECTED", reasons=["net reward/risk 0.8 < 1.0"]),
        _row("scalp.orb", "REJECTED", reasons=["net reward/risk 0.5 < 1.0", "spread 0.4%"]),
        _row("scalp.orb", "EXPIRED"),
        _row("scalp.orb", "QUALIFIED"),                       # not filled
    ])
    assert (stats.total, stats.qualified, stats.rejected, stats.expired) == (6, 3, 2, 1)
    assert (stats.executed, stats.closed, stats.wins, stats.net) == (2, 2, 1, 300.0)
    assert stats.win_rate == 0.5 and not stats.enough_data
    b = stats.by_strategy["swing.breakout"]
    assert (b.decisions, b.qualified, b.executed, b.closed, b.net) == (2, 2, 2, 2, 300.0)
    assert stats.top_rejections == [("net reward/risk # < #", 2), ("spread #", 1)]


def test_summarize_trades():
    trades = [
        _trade(500, "2026-09-01T10:00:00+00:00"),
        _trade(-300, "2026-09-02T10:00:00+00:00"),
        _trade(-400, "2026-09-03T10:00:00+00:00"),
        _trade(900, "2026-09-04T10:00:00+00:00"),
        _trade(None, "2026-09-05T10:00:00+00:00"),  # no P&L recorded: ignored
    ]
    stats = summarize_trades(trades)

    assert (stats.closed, stats.wins, stats.total_pnl) == (4, 2, 700.0)
    assert stats.win_rate == 0.5
    assert (stats.avg_win, stats.avg_loss) == (700.0, -350.0)
    assert stats.profit_factor == pytest.approx(1400 / 700)
    assert stats.max_drawdown == 700.0  # +500 peak, then -300 and -400
    assert stats.avg_return == pytest.approx(700 / 4 / 1000)  # on ₹1,000 per trade


def test_calibration_buckets_by_entry_confidence():
    buckets = calibration([
        _trade(100, "2026-09-01T10:00:00+00:00", confidence=85),
        _trade(-50, "2026-09-01T11:00:00+00:00", confidence=82),
        _trade(20, "2026-09-01T12:00:00+00:00", confidence=55),
        _trade(20, "2026-09-01T13:00:00+00:00"),  # no confidence stored
    ])
    by_label = {b.label: b for b in buckets}
    assert (by_label["80–100"].trades, by_label["80–100"].win_rate) == (2, 0.5)
    assert by_label["50–60"].win_rate == 1.0
    assert by_label["0–50"].win_rate is None


def test_build_report_reads_the_journal_and_supabase(tmp_path):
    config = MagicMock(supabase_url="https://x", trading_mode="paper")
    config.supabase_service_key.get_secret_value.return_value = "key"
    trades = MagicMock()
    trades.get_closed_since.return_value = [_trade(250, "2026-09-12T10:00:00+00:00", 70)]
    journal = MagicMock()
    journal.signals.return_value = [_row("swing.breakout", "QUALIFIED", filled=3, net=250.0)]

    with patch("supabase.create_client"), \
         patch("skopaq.strategy.journal.SignalJournal.from_config", return_value=journal), \
         patch("skopaq.db.repositories.TradeRepository", return_value=trades):
        result = report_mod.build_report(
            config, days=30, now=datetime(2026, 9, 24, tzinfo=timezone.utc))

    assert result.signals.total == 1 and result.signals.net == 250.0
    days = journal.signals.call_args.args[0]
    assert len(days) == 31 and days[-1].isoformat() == "2026-09-24"
    assert result.trades.closed == 1 and result.trades.total_pnl == 250.0
    assert trades.get_closed_since.call_args.kwargs == {"is_paper": True}
    assert result.sources == ["signal journal", "supabase:trades"]


def test_cli_and_mcp_render_the_report(monkeypatch):
    from typer.testing import CliRunner

    from skopaq import mcp_server
    from skopaq.cli.main import app
    from skopaq.learning.report import Report, SignalStats, TradeStats

    fake = Report(days=90, mode="paper", signals=summarize_signals([
                      _row("swing.breakout", "QUALIFIED", filled=1, net=1200.0),
                      _row("scalp.orb", "REJECTED")]),
                  trades=TradeStats(closed=1, wins=1, total_pnl=1200.0),
                  calibration=calibration([]), sources=["supabase:trades"])
    monkeypatch.setattr(report_mod, "build_report", lambda config, days=90: fake)

    result = CliRunner().invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    assert "Track record" in result.output and "₹1,200" in result.output
    assert "mostly noise" in result.output  # 1 closed signal is not a sample
    assert "swing." in result.output

    import asyncio

    monkeypatch.setattr(mcp_server, "_get_config", lambda: MagicMock())
    data = json.loads(asyncio.run(mcp_server.performance_report()))
    assert data["signals"]["win_rate"] == 1.0 and data["signals"]["enough_data"] is False
    assert data["signals"]["by_strategy"]["swing.breakout"]["net"] == 1200.0
    assert data["trades"]["win_rate"] == 1.0
