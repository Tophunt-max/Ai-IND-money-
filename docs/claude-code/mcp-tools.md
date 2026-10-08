# MCP Tools Reference

SkopaqTrader exposes 29 MCP tools through `skopaq/mcp_server.py`. All tools are async and return JSON strings.

## Market Data

| Tool | Description | Key Args |
|------|-------------|----------|
| `get_quote` | Real-time LTP, OHLC, bid/ask, volume, change% | `symbol` |
| `get_historical` | OHLCV candles (last 20 returned) | `symbol`, `days=5`, `resolution=1` |

**Example — get a quote:**

> What is the current price of RELIANCE?

Claude calls `mcp__skopaq__get_quote(symbol="RELIANCE")` and returns:

```json
{
  "symbol": "RELIANCE",
  "ltp": 2485.50,
  "open": 2470.00,
  "high": 2498.00,
  "low": 2465.00,
  "change_pct": 0.63
}
```

**Example — historical data:**

> Show me 15-minute candles for TCS over the last 3 days.

```
mcp__skopaq__get_historical(symbol="TCS", days=3, resolution=15)
```

## Portfolio Management

| Tool | Description | Key Args |
|------|-------------|----------|
| `get_positions` | Open positions with P&L | none |
| `get_holdings` | Delivery (CNC) holdings | none |
| `get_funds` | Available cash, margin, collateral | none |
| `get_orders` | Today's orders with status | none |

These tools read the MCP server's order router (paper engine). Quotes and candles come from INDstocks.

## AI Analysis

| Tool | Description | Key Args |
|------|-------------|----------|
| `analyze_stock` | Full 15-agent pipeline (2-5 min) | `symbol`, `date=""` |
| `scan_market` | Multi-model market scan | `max_candidates=5` |
| `check_safety` | Pre-trade safety validation | `symbol`, `quantity`, `price`, `side` |

!!! note "Analysis duration"
    `analyze_stock` runs the complete multi-agent pipeline with 4 analysts, bull/bear debate, risk debate, and trader decision. It takes 2-5 minutes and calls multiple LLM providers.

## Data Gathering (Claude-Native)

These tools fetch raw data for Claude to reason over directly, bypassing the multi-LLM pipeline:

| Tool | Description | Key Args |
|------|-------------|----------|
| `gather_all_analysis_data` | One-shot: market + news + fundamentals + social + memories | `symbol`, `date=""` |
| `gather_market_data` | OHLCV + RSI, MACD, Bollinger, SMA, EMA, ATR, VWMA | `symbol`, `date=""` |
| `gather_news_data` | Company news, global macro, insider transactions | `symbol`, `date=""` |
| `gather_fundamentals_data` | Profile, balance sheet, cash flow, income statement | `symbol`, `date=""` |
| `gather_social_data` | Social media sentiment and company news | `symbol`, `date=""` |
| `recall_agent_memories` | BM25 search over past trade lessons | `situation_summary` |
| `quick_decision` | Calibrated answer to a question about a text via TypeSafe Jev (~0.1 s; needs `SKOPAQ_JEV_ENABLED`) | `text`, `question`, `options=None` |
| `save_trade_reflection` | Store post-trade lesson for future reference | `symbol`, `side`, `entry_price`, `exit_price`, `pnl`, `pnl_pct` |

## Order Execution

| Tool | Description | Key Args |
|------|-------------|----------|
| `place_order` | Execute order through safety checker (paper engine); the only order tool | `symbol`, `side="BUY"`, `quantity=1`, `price=0`, `order_type="MARKET"` |
| `system_status` | Version, mode, token health, active LLMs | none |
| `halt_trading` | Kill switch: reject every BUY everywhere (SELLs stay allowed) | `reason` |
| `resume_trading` | Lift the kill switch | none |

!!! warning "Safety First"
    Every order passes through the `SafetyChecker` before execution. Orders that violate position limits, daily loss caps, or other safety rules are rejected automatically.

## Options Trading

| Tool | Description | Key Args |
|------|-------------|----------|
| `get_option_chain` | INDstocks chain with calls/puts, OI, volume, distance% | `symbol="NIFTY"`, `expiry_index=0` |
| `suggest_option_trade` | AI strike selection with risk metrics | `symbol="NIFTY"`, `strategy="SHORT_PUT"`, `expiry_index=0` |

**Supported strategies:**

- `SHORT_PUT` -- Sell OTM put (bullish view)
- `SHORT_CALL` -- Sell OTM call (bearish view)
- `SHORT_STRANGLE` -- Sell OTM put + call (neutral view)

Option tools need a valid INDstocks token and are advisory: they place nothing.

## Learning & Backtesting

| Tool | Description | Key Args |
|------|-------------|----------|
| `performance_report` | AI calls vs NIFTY, closed trades, calibration | `days=90` |
| `backtest_strategy` | Backtest on OHLCV history (Sharpe, drawdown, win rate) | `symbol`, `days=365`, `stop_loss_pct=3.0`, `target_pct=6.0` |
| `run_monte_carlo_test` | Monte Carlo over the backtest's trades | `symbol`, `days=365`, `simulations=1000` |
| `get_learning_insights` | Insights from past trades (calibration, sectors, stops, timing) | none |
| `get_symbol_stats` | Past performance for one symbol | `symbol` |
| `evolve_strategy` | Backtest, validate, adapt and persist strategy parameters | `symbol`, `days=180` |

GTT, AMO, bracket, cover, basket, option/future order and mutual fund tools were removed with Kite Connect; INDstocks smart orders are planned (see [GTT Orders](../trading/gtt-orders.md)).

## Tool Count by Category

| Category | Count |
|----------|-------|
| Market Data | 2 |
| Portfolio | 4 |
| AI Analysis | 3 |
| Data Gathering | 8 |
| Execution | 4 |
| Options | 2 |
| Learning & Backtesting | 6 |
| **Total** | **29** |
