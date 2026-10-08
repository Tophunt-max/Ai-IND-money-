# Options Selling

SkopaqTrader includes an AI-powered options selling module that analyzes option chains and selects optimal OTM strikes for theta decay strategies.

## Supported Strategies

| Strategy | Market View | Action | Risk Profile |
|----------|-------------|--------|-------------|
| `SHORT_PUT` | Bullish | Sell OTM put below support | Profit if stock stays above strike |
| `SHORT_CALL` | Bearish | Sell OTM call above resistance | Profit if stock stays below strike |
| `SHORT_STRANGLE` | Neutral | Sell both OTM put + call | Profit if stock stays in range |

## How It Works

The options module (`skopaq/options/`) has two components:

1. **Chain Fetcher** (`chain.py`) -- Fetches the option chain from INDstocks (`GET /market/option-chain`, expiries from `GET /market/instruments/expiries`) with calls, puts, strikes, premiums, OI, volume, IV and Greeks. Underlyings, expiries, lot sizes and contracts are resolved by `skopaq/broker/fno.py`
2. **Strategy Selector** (`strategy.py`) -- Picks the optimal strike based on distance from spot, premium yield, liquidity, and risk. It is advisory and places no orders

There is no GTT integration: stop-losses on option positions are not placed automatically (INDstocks smart orders are planned, see [GTT Orders](gtt-orders.md)).

Supported underlyings: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX and F&O stocks.

### Strike Selection Criteria

For `SHORT_PUT`, the selector filters puts that are:

- 3-8% OTM (below spot price)
- Premium at least Rs 5
- Reasonable liquidity (volume > 0 or OI > 100)
- Highest premium yield among qualifying strikes

Similar logic applies to `SHORT_CALL` (3-8% above spot) and `SHORT_STRANGLE` (both sides).

## Using /options

The simplest way to get an options recommendation:

```
/options                          # NIFTY SHORT_PUT (default)
/options BANKNIFTY SHORT_CALL     # BANKNIFTY bearish
/options RELIANCE SHORT_STRANGLE  # Stock strangle
```

### Example Output

```
SELL NIFTY 24000 PE at Rs 85
Strike: 24000 (PE) — 4.2% OTM
Expiry: 2026-04-10 (4 days)
Lot Size: 25

Max Profit: Rs 2,125 (85 x 25)
Stop Loss: Rs 255 (exit if premium triples)
Max Loss: Rs 6,375 (255 x 25)
Margin: ~Rs 1,20,000
Win Probability: ~78%

Risk Management:
- Exit if premium doubles (100% SL)
- Exit 2 days before expiry if not profitable
- Never hold through major events without hedging
```

## MCP Tools for Options

| Tool | Purpose |
|------|---------|
| `get_option_chain` | Fetch raw chain data from INDstocks (calls + puts with OI, volume) |
| `suggest_option_trade` | Get AI recommendation with risk metrics |

**Example -- fetch chain manually:**

> Show me the NIFTY option chain for the nearest expiry.

Claude calls `mcp__skopaq__get_option_chain(symbol="NIFTY", expiry_index=0)`.

## Risk Management

!!! warning "Options selling carries significant risk"
    Selling naked options exposes you to theoretically unlimited loss. Always use stop-losses and position sizing.

### Built-in Safeguards

1. **Distance filter** -- Only selects strikes 3-8% OTM (avoids near-the-money danger)
2. **Premium floor** -- Ignores strikes with premium below Rs 5 (not worth the risk)
3. **Stop-loss rule** -- Recommends exiting at 2-3x premium collected
4. **Position sizing** -- Never risk more than 2% of capital per trade
5. **Safety checker** -- `no_naked_option_selling` rule in `SafetyRules` (can be configured)

### Events to Watch

Before selling options, check for:

- Earnings announcements (high IV crush risk)
- RBI policy decisions (macro moves)
- F&O expiry day (Thursday volatility)
- Union budget or major government policy

## Architecture

```
/options NIFTY SHORT_PUT
        │
        ▼
  get_quote(NIFTY)          ← spot price
        │
        ▼
  get_option_chain(NIFTY)   ← chain from INDstocks
        │
        ▼
  suggest_option_trade()    ← AI selects strike
        │
        ▼
  Claude presents risk analysis
        │
        ▼
  User decides (the selector places nothing)
```

## Prerequisites

Options trading requires:

1. **INDstocks token** -- A valid INDstocks access token (`skopaq token set`; options data comes from INDstocks)
2. **F&O enabled** -- Your INDstocks account must have the F&O segment activated
3. **Sufficient margin** -- Options selling requires margin (shown in recommendation)

!!! tip "Start with NIFTY"
    NIFTY options have the best liquidity and tightest spreads. Start there before moving to stock options.
