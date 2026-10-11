# MCP Tools Reference

SkopaqTrader exposes 26 MCP tools through `skopaq/mcp_server.py`. All tools are async and return strings (JSON, or a text report for the backtest and learning tools).

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

## Swing Ideas (Adaptive AI)

| Tool | Description | Key Args |
|------|-------------|----------|
| `swing_ideas` | Today's daily setups ranked by the swing model (no orders) | `limit=10`, `symbols=""` |
| `swing_idea` | One symbol's setup on its last closed daily bar and the model's probability | `symbol` |

`symbols` is a comma-separated list; empty means `SKOPAQ_SWING_SYMBOLS`, else NIFTY 50. Each idea has `setup` (`trend_pullback`, `breakout` or `oversold_bounce`), `entry`, the ATR `stop`, the R-multiple `target`, `prob` and `score`. The response also names the `model` version and its `threshold`; `model: null` means no swing model is trained yet. `swing_idea` returns `idea: null` when no setup fired. See [Swing Trading](../trading/swing-trading.md).

## Safety

| Tool | Description | Key Args |
|------|-------------|----------|
| `check_safety` | Validate a hypothetical order against the paper safety rules | `symbol`, `quantity=1`, `price=0`, `side="BUY"` |

## Data Gathering

Raw data for Claude to read and summarise:

| Tool | Description | Key Args |
|------|-------------|----------|
| `gather_all_analysis_data` | One-shot: market + news + fundamentals + social, in parallel | `symbol`, `date=""` |
| `gather_market_data` | OHLCV + RSI, MACD, Bollinger, SMA, EMA, ATR, VWMA | `symbol`, `date=""` |
| `gather_news_data` | Company news, global macro, insider transactions | `symbol`, `date=""` |
| `gather_fundamentals_data` | Profile, balance sheet, cash flow, income statement | `symbol`, `date=""` |
| `gather_social_data` | Social media sentiment and company news | `symbol`, `date=""` |

## Order Execution and System

| Tool | Description | Key Args |
|------|-------------|----------|
| `place_order` | Execute order through safety checker (paper engine); the only order tool | `symbol`, `side="BUY"`, `quantity=1`, `price=0`, `order_type="MARKET"` |
| `system_status` | Version, mode, asset class, token health, swing model (`champion`, `shadow`), paper capital, kill switch | none |
| `halt_trading` | Kill switch: reject every BUY everywhere (SELLs stay allowed) | `reason` |
| `resume_trading` | Lift the kill switch | none |

!!! warning "Safety First"
    Every order passes through the `SafetyChecker` before execution. Orders that violate position limits, daily loss caps, or other safety rules are rejected automatically.

## Options Trading

| Tool | Description | Key Args |
|------|-------------|----------|
| `get_option_chain` | INDstocks chain with calls/puts, OI, volume, distance% | `symbol="NIFTY"`, `expiry_index=0` |
| `suggest_option_trade` | Rule-based strike selection with risk metrics | `symbol="NIFTY"`, `strategy="SHORT_PUT"`, `expiry_index=0` |

**Supported strategies:**

- `SHORT_PUT` -- Sell OTM put (bullish view)
- `SHORT_CALL` -- Sell OTM call (bearish view)
- `SHORT_STRANGLE` -- Sell OTM put + call (neutral view)

Option tools need a valid INDstocks token and are advisory: they place nothing.

## Learning & Backtesting

| Tool | Description | Key Args |
|------|-------------|----------|
| `performance_report` | Journalled signals (qualified, executed, closed net), closed trades, confidence calibration | `days=90` |
| `backtest_strategy` | RSI backtest on OHLCV history (Sharpe, drawdown, win rate) | `symbol`, `days=365`, `stop_loss_pct=3.0`, `target_pct=6.0` |
| `run_monte_carlo_test` | Monte Carlo over the backtest's trades | `symbol`, `days=365`, `simulations=1000` |
| `get_learning_insights` | Insights from past trades (calibration, sectors, stops, timing) | none |
| `get_symbol_stats` | Past performance for one symbol | `symbol` |
| `evolve_strategy` | Backtest, validate, adapt and persist strategy parameters | `symbol`, `days=180` |

`performance_report` works in the current trading mode; below 30 closed signals `enough_data` is false (the win rate is mostly noise).

GTT, AMO, bracket, cover, basket, option/future order and mutual fund tools were removed with Kite Connect; INDstocks smart orders are planned (see [GTT Orders](../trading/gtt-orders.md)).

## Tool Count by Category

| Category | Count |
|----------|-------|
| Market Data | 2 |
| Portfolio | 4 |
| Swing Ideas | 2 |
| Safety | 1 |
| Data Gathering | 5 |
| Execution & System | 4 |
| Options | 2 |
| Learning & Backtesting | 6 |
| **Total** | **26** |
