# Installation

## Prerequisites

- Python 3.11+
- INDstocks (INDmoney) account with an API token (market data and live trading; F&O segment activated for options)
- No LLM or AI API key: the Adaptive AI trains and runs locally

## Install from Source

```bash
git clone https://github.com/Tophunt-max/Ai-IND-money-.git
cd Ai-IND-money-
pip install -e .
```

The model libraries (LightGBM, XGBoost, CatBoost, PyTorch for RL) are optional; without them the linear models still work (`--model linear`).

## Install via Docker

```bash
docker pull samuelvinay91/skopaq:latest
```

## Configuration

Create a `.env` file from the template:

```bash
cp .env.example .env
```

### Minimum Setup (Paper Trading)

```bash
SKOPAQ_TRADING_MODE=paper
SKOPAQ_INDSTOCKS_TOKEN=...         # INDstocks API token (or: skopaq token set)
```

Without a token the paper engine still runs; quotes and daily history fall back to yfinance where they can.

### Full Setup (Live Trading)

```bash
# Broker
SKOPAQ_INDSTOCKS_TOKEN=...         # or TOTP auto-login: docs/deployment/go-live.md

# Telegram Bot
SKOPAQ_TELEGRAM_BOT_TOKEN=...      # From @BotFather
SKOPAQ_TELEGRAM_CHAT_ID=...        # Your chat ID

# Supabase (trade history, shared kill switch, P&L limits across processes)
SKOPAQ_SUPABASE_URL=...
SKOPAQ_SUPABASE_SERVICE_KEY=...

# Trading Mode
SKOPAQ_TRADING_MODE=paper          # Start with paper, switch to live later
```

## Verify Installation

```bash
skopaq status          # System health check
skopaq swing-status    # Swing model registry (empty until you train one)
skopaq --help          # All commands
```
