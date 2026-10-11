# Telegram Commands

The SkopaqTrader Telegram bot answers slash commands only, and only in allow-listed chats. Its trading views come from the Adaptive AI (the swing model); the bot places no orders.

## Slash Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Welcome message, register for alerts | `/start` |
| `/help` | List all commands | `/help` |
| `/quote SYMBOL` | Real-time stock quote | `/quote RELIANCE` |
| `/portfolio` | Funds and open positions | `/portfolio` |
| `/status` | System health check | `/status` |
| `/pnl` | P&L on open positions | `/pnl` |
| `/analyze SYMBOL` | Indicators + the swing model's view | `/analyze TCS` |
| `/ideas` | Today's swing ideas | `/ideas` |
| `/token` | INDstocks token status | `/token` |
| `/halt REASON` | Kill switch: stop all new BUYs everywhere | `/halt news event` |
| `/resume` | Lift the kill switch | `/resume` |

## Command Details

### /start

Registers your chat for notifications and displays a welcome message with available commands and scheduled job times.

```
Welcome to SkopaqTrader! Alerts and commands for the Adaptive AI.

Commands:
/quote SYMBOL - Live stock quote
/portfolio - Positions & P&L
/status - System health
/pnl - Open position P&L
/ideas - Today's swing ideas (Adaptive AI)
/analyze SYMBOL - Indicators + the swing model's view
/token - INDstocks token status
/help - All commands

Scheduled (auto):
  09:00 IST - Token check
  09:25 IST - Market scan
  15:35 IST - EOD summary
```

### /quote SYMBOL

Fetches a real-time quote from INDstocks.

```
/quote HDFCBANK
```

Response:

```
HDFCBANK (NSE)

LTP: Rs 1,567.80 (+1.23%)
Open: 1,550.00 | High: 1,575.00
Low: 1,545.00 | Close: 1,548.75
Volume: 845,230
```

### /portfolio

Shows funds and open positions (from the MCP server's order router).

```
/portfolio
```

Response:

```
Cash: Rs 987,654.00
Margin Used: Rs 112,346.00

Positions (2):
  RELIANCE | Qty: 10 | Avg: 2,400.00 | PnL: +855.00
  TCS | Qty: 5 | Avg: 3,800.00 | PnL: -250.00
```

### /pnl

Shows P&L for open INDstocks positions at live quotes, per position and in total.

### /status

Displays system health information:

```
SkopaqTrader v0.4.0
Mode: PAPER
Asset: equity
Token: Valid
Swing model: <champion version, else shadow, else none>
Capital: Rs 1,000,000
```

### /analyze SYMBOL

RSI, MACD and ATR from `gather_market_data` with a simple indicator reading (oversold, overbought, bullish, bearish or neutral), then the swing model's view from `swing_idea`: the setup on the last daily bar with its entry, stop and target, and P(win) against the threshold — or "none on the last daily bar".

```
/analyze INFY
```

### /ideas

Today's top 5 swing ideas from `swing_ideas`, with the model version and state: symbol, setup, entry, stop (SL), target (TG) and P(win). With no ideas it says so and names the model (or "none" when no swing model is trained). No orders are placed.

```
/ideas
```

### /halt REASON and /resume

`/halt` sets the kill switch: every new BUY is rejected everywhere (daemon, CLI, MCP) until `/resume`; SELLs stay allowed. The reply says whether it was recorded in Supabase (every process halted) or only on the bot's machine.

### /token

Shows whether the INDstocks access token is valid and how long it has left.

```
/token
```

Response (valid):

```
INDstocks token valid (5h 12m left).
```

Response (missing or expired):

```
INDstocks token missing or expired.
Set today's INDstocks token on the dashboard Broker page, or run `skopaq token set <TOKEN>` on the server.
```

## Notification Messages

Beyond commands, alerts are sent through `skopaq/notifications.py` to `SKOPAQ_TELEGRAM_CHAT_ID` (and chats registered in the sending process), for example:

| Event | Sent by |
|-------|---------|
| Trade filled, partial, unconfirmed, failed or rejected | Executor |
| Position alerts (stop, target, trailing stop, EOD exit) | Position monitor |
| Live order alerts (CRITICAL / WARNING) | Order alerter |
| Token check, market scan, EOD summary | The bot's [scheduled jobs](scheduler.md) |

## Message Formatting

Replies are plain text (not Markdown): symbols in UPPERCASE, prices prefixed with "Rs", P&L with a `+` or `-` sign.

!!! tip "Scheduled alerts"
    The scheduled jobs message every allow-listed chat (added at bot start) and any chat that sent `/start` or `/token` since.
