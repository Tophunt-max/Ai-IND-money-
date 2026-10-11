"""Tests for the MCP server tool registration."""

from __future__ import annotations


def test_mcp_server_imports():
    from skopaq.mcp_server import mcp

    assert mcp is not None
    assert mcp._tool_manager is not None


def test_mcp_server_has_all_tools():
    from skopaq.mcp_server import mcp

    tool_names = {t.name for t in mcp._tool_manager._tools.values()}
    assert len(tool_names) == 26  # Total tool count

    # Verify key tools exist by category
    assert "get_quote" in tool_names  # Market data
    assert "get_positions" in tool_names  # Portfolio
    assert {"swing_ideas", "swing_idea"} <= tool_names  # Adaptive AI swing book
    assert "place_order" in tool_names  # Execution (paper engine, SafetyChecker)
    assert "get_option_chain" in tool_names  # Options (INDstocks)
    assert "suggest_option_trade" in tool_names  # Options AI
    # The Kite order tools are gone: INDstocks is the only broker
    assert not tool_names & {"place_gtt_order", "list_gtt_orders", "setup_swing_trade",
                             "place_amo_order", "place_bracket", "place_cover", "place_basket",
                             "buy_option_contract", "trade_future", "invest_mutual_fund",
                             "list_mutual_funds"}
    assert "gather_all_analysis_data" in tool_names  # Data pipeline
    # The LLM tools are gone: Adaptive AI is the only decision-maker
    assert not tool_names & {"analyze_stock", "scan_market", "quick_decision",
                             "recall_agent_memories", "save_trade_reflection"}
    assert {"halt_trading", "resume_trading"} <= tool_names  # Kill switch
    assert "performance_report" in tool_names  # Track record
    assert "system_status" in tool_names  # System


def test_mcp_server_name():
    from skopaq.mcp_server import mcp

    assert mcp.name == "SkopaqTrader"


def test_mcp_server_has_instructions():
    from skopaq.mcp_server import mcp

    assert "trading" in mcp.instructions.lower()


# ── check_safety / place_order: the SELL's open-order context (GAP 2) ────────


def _sell_inputs(positions, *, error=""):
    from datetime import datetime, timezone

    from skopaq.execution.order_router import SellInputs
    from skopaq.execution.sellable import SellContext

    return SellInputs(positions=positions, holdings=[], context=SellContext(
        orders=(), read_at=datetime(2026, 9, 25, 5, 30, tzinfo=timezone.utc), error=error))


def _tcs(qty):
    from decimal import Decimal

    from skopaq.broker.models import Position

    return Position(symbol="TCS", security_id="11536", quantity=Decimal(qty), product="CNC",
                    average_price=100.0)


def _mock_live_router(inputs, execute=None):
    from unittest.mock import AsyncMock, MagicMock

    from skopaq.broker.models import Funds

    router = MagicMock(mode="live")
    router.sell_inputs = AsyncMock(return_value=inputs)
    router.sell_lock = MagicMock(return_value=None)
    router.get_positions = AsyncMock(return_value=[])
    router.get_settled_holdings = AsyncMock(return_value=[])
    router.get_funds = AsyncMock(return_value=Funds(available_cash=500_000,
                                                    available_margin=500_000))
    router.execute = AsyncMock(return_value=execute)
    return router


def _run(tool, *, router, config, **kwargs):
    import asyncio
    import json
    from unittest.mock import patch

    from skopaq import mcp_server
    from skopaq.execution.safety_checker import SafetyChecker

    with patch.dict(mcp_server._infra_cache, {"config": config, "router": router}), \
         patch("skopaq.execution.pnl_history.seed_safety_checker"), \
         patch.object(SafetyChecker, "_check_market_hours"):
        return json.loads(asyncio.run(getattr(mcp_server, tool)(**kwargs)))


def _live_config():
    from unittest.mock import MagicMock

    return MagicMock(trading_mode="live", max_sector_concentration_pct=0.4)


def test_check_safety_checks_a_sell_against_the_routers_open_order_context():
    router = _mock_live_router(_sell_inputs([_tcs(10)], error="HTTP 503"))

    result = _run("check_safety", router=router, config=_live_config(), symbol="TCS",
                  quantity=5, side="SELL")

    assert not result["passed"]
    assert result["rejections"][0].startswith("Cannot read the broker's order book (HTTP 503)")
    router.get_positions.assert_not_called()      # the book-first read replaces them


def test_check_safety_buy_does_not_read_sell_inputs():
    router = _mock_live_router(None)
    result = _run("check_safety", router=router, config=_live_config(), symbol="TCS",
                  quantity=1, price=100, side="BUY")
    assert result["passed"], result
    router.sell_inputs.assert_not_called()


def test_place_order_refuses_a_live_sell_when_the_book_cannot_be_read(monkeypatch):
    from skopaq.execution import order_alerts
    from tests.unit.execution._fakes import AlertSpy

    spy = AlertSpy()
    monkeypatch.setattr(order_alerts, "_alerter", spy)
    router = _mock_live_router(_sell_inputs([_tcs(10)], error="HTTP 503"))

    result = _run("place_order", router=router, config=_live_config(), symbol="TCS",
                  side="SELL", quantity=5)

    assert result["success"] is False
    assert "Cannot read the broker's order book" in result["reason"]
    router.execute.assert_not_called()
    router.sell_lock.assert_called_once()
    assert spy.keys("CRITICAL") == ["sell-refused:TCS:book-unreadable"]


def test_place_order_holds_the_sell_lock_until_the_broker_answers():
    from decimal import Decimal

    from skopaq.broker.models import ExecutionResult

    events = []

    class Lock:
        async def __aenter__(self):
            events.append("lock")

        async def __aexit__(self, *exc):
            events.append("unlock")

    filled = ExecutionResult(success=True, mode="live", fill_price=95.0,
                             filled_quantity=Decimal(3), requested_quantity=Decimal(5),
                             outcome="partial", order_ids=["EQ-1"])
    router = _mock_live_router(_sell_inputs([_tcs(10)]), execute=filled)
    router.sell_lock.return_value = Lock()

    async def execute(order, signal):
        events.append("execute")
        return filled

    router.execute.side_effect = execute

    result = _run("place_order", router=router, config=_live_config(), symbol="TCS",
                  side="SELL", quantity=5)

    assert events == ["lock", "execute", "unlock"]
    assert result["success"] is True
    assert (result["filled_quantity"], result["outcome"], result["order_ids"]) == (
        3, "partial", ["EQ-1"])
    assert (result["remaining_open"], result["fill_unconfirmed"]) == (False, False)


def test_place_order_in_paper_reports_the_fill_as_before():
    from unittest.mock import MagicMock

    from skopaq.broker.paper_engine import PaperEngine
    from skopaq.execution.order_router import OrderRouter

    config = MagicMock(trading_mode="paper", max_sector_concentration_pct=0.4)
    paper = PaperEngine(initial_capital=1_000_000)
    router = OrderRouter(config, paper)
    import asyncio
    import json
    from unittest.mock import patch

    from skopaq import mcp_server

    with patch.dict(mcp_server._infra_cache, {"config": config, "router": router,
                                              "paper": paper}), \
         patch("skopaq.execution.pnl_history.seed_safety_checker"):
        bought = json.loads(asyncio.run(mcp_server.place_order(
            symbol="TCS", side="BUY", quantity=2, price=100, order_type="LIMIT")))
        refused = json.loads(asyncio.run(mcp_server.place_order(
            symbol="INFY", side="SELL", quantity=1, price=100, order_type="LIMIT")))

    assert bought["success"] is True and bought["mode"] == "paper"
    assert (bought["filled_quantity"], bought["outcome"], bought["order_ids"]) == (2, "", [])
    assert refused == {"success": False,
                       "reason": "Safety check failed: No short sales: SELL 1 INFY but only "
                                 "0 held"}
