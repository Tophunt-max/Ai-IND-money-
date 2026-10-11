# Custom Slash Commands

SkopaqTrader ships 6 custom skills for Claude Code, defined in `.claude/skills/`. These are slash commands you type directly in the Claude Code prompt.

The Adaptive AI is the only decision-maker: `/analyze`, `/scan` and `/trade` report its setups and probabilities and never invent a verdict, confidence or price levels.

## Available Commands

| Command | Description | Skill File |
|---------|-------------|------------|
| `/quote SYMBOL` | Real-time stock quote | `.claude/skills/quote/SKILL.md` |
| `/analyze SYMBOL` | The Adaptive AI's swing setup, model probability and indicators | `.claude/skills/analyze/SKILL.md` |
| `/scan [N]` | Today's swing ideas | `.claude/skills/scan/SKILL.md` |
| `/portfolio` | Positions, holdings, funds, P&L | `.claude/skills/portfolio/SKILL.md` |
| `/trade SYMBOL` | Paper-trade the Adaptive AI's setup after confirmation | `.claude/skills/trade/SKILL.md` |
| `/options [SYMBOL] [STRATEGY]` | Options selling recommendation | `.claude/skills/options/SKILL.md` |

## /quote SYMBOL

Get a live market quote with LTP, day range, bid/ask, and volume.

```
/quote RELIANCE
```

Claude fetches the quote via `get_quote` and presents:

```
RELIANCE (NSE)
  LTP:    Rs 2,485.50 (+0.63%)
  Open:   Rs 2,470.00
  High:   Rs 2,498.00
  Low:    Rs 2,465.00
  Volume: 12,34,567
```

## /analyze SYMBOL

Shows what the Adaptive AI sees in a stock. This skill only reports.

```
/analyze TCS
```

1. `swing_idea` — the setup (`trend_pullback` / `breakout` / `oversold_bounce`) on the last closed daily bar, its entry, ATR stop and R-multiple target, and the swing model's P(win) against its threshold
2. `get_quote` — the live price and its distance from the entry
3. `gather_market_data` — indicators for context (RSI, moving averages, ATR)

The report is a table (setup, model and whether it qualifies, entry / stop / target, live price) plus 3–5 factual bullet points. No setup today, or no trained swing model (it suggests `skopaq swing-train`), is said plainly. If you ask whether it is safe to trade, it calls `check_safety` with the setup's levels.

## /scan

Today's swing ideas from `swing_ideas`: daily setups across the configured universe (NIFTY 50 by default) kept when the swing model's probability clears its threshold.

```
/scan        # Default: 10 ideas
/scan 5      # Top 5
```

Output is a table of symbol, setup, P(win), entry, stop and target, optionally with live quotes for the top 3. Nothing is bought; an empty list is a valid answer.

## /portfolio

Display the full portfolio state: cash balance, open positions with P&L, and delivery holdings.

```
/portfolio
```

Returns a formatted summary:

```
Funds:
  Available Cash:  Rs 9,87,654.00
  Used Margin:     Rs 1,12,346.00
  Collateral:      Rs 0.00

Positions (2):
  RELIANCE  10x @ Rs 2,400.00  LTP Rs 2,485.50  P&L +Rs 855.00
  TCS        5x @ Rs 3,800.00  LTP Rs 3,750.00  P&L -Rs 250.00
```

## /trade SYMBOL

Paper-trades the Adaptive AI's setup for one stock.

```
/trade INFY
```

**Workflow:**

1. `swing_idea` — stops if no setup fired or the model's P(win) is below its threshold; with no trained model it continues only if you explicitly ask for a paper test
2. `get_funds`, then quantity = floor(1% of capital / (entry − stop)), at least 1
3. `check_safety` at the setup's entry (BUY); stops if it fails
4. Presents the trade plan (setup, P(win), entry / stop / target, quantity, risk) and asks for confirmation
5. Executes via `place_order` only after you say yes

!!! warning "Paper only"
    The MCP server's orders always go to the paper engine. Claude never executes without your confirmation.

## /options

Options selling analysis. Fetches the option chain, gets the rule-based strike suggestion (`suggest_option_trade`), and presents it with risk metrics.

```
/options                         # Default: NIFTY SHORT_PUT
/options BANKNIFTY SHORT_CALL    # Specific underlying + strategy
/options RELIANCE SHORT_STRANGLE # Stock options
```

**Strategies:**

| Strategy | Market View | What It Does |
|----------|-------------|--------------|
| `SHORT_PUT` | Bullish | Sell OTM put below support |
| `SHORT_CALL` | Bearish | Sell OTM call above resistance |
| `SHORT_STRANGLE` | Neutral | Sell both OTM put + call |

The output includes strike selection, premium, max profit/loss, margin, stop-loss, and win probability estimate. It asks for confirmation before any execution.

!!! tip "Requires the INDstocks token"
    Options data comes from the INDstocks option chain API (with IV and Greeks). Check the token with `/token` in Telegram or `skopaq token status`; the account needs F&O activated at INDstocks.

## How Skills Work

Each skill is a `SKILL.md` file in `.claude/skills/<name>/`. It contains:

- **YAML frontmatter**: name, description, allowed MCP tools
- **Markdown body**: Detailed instructions for Claude on how to execute the skill

Claude Code reads the skill file and follows its instructions, calling the listed MCP tools as needed. `/quote` and `/portfolio` call the MCP server's Python functions through Bash instead. See [Adding Skills](../contributing/skills.md) for how to create new ones.
