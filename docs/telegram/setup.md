# Telegram Bot Setup

SkopaqTrader includes a Telegram bot (`@Skopaq_bot`) that provides trade alerts, portfolio monitoring, the Adaptive AI's swing ideas, and scheduled market updates. The bot is defined in `skopaq/telegram_bot.py`.

## Create Your Bot

### Step 1: Register with BotFather

1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. Choose a name (e.g., "SkopaqTrader")
4. Choose a username (e.g., `my_skopaq_bot`)
5. BotFather gives you a token like `7123456789:AAH-abc123def456ghi789jkl`

### Step 2: Set the Token

Add the token to your `.env`:

```bash
SKOPAQ_TELEGRAM_BOT_TOKEN=7123456789:AAH-abc123def456ghi789jkl
```

Set your chat ID. The bot is private: it answers only this chat and any
listed in `SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS`, and sends notifications here.

```bash
SKOPAQ_TELEGRAM_CHAT_ID=123456789
# Optional: more chats allowed to use the bot (comma-separated)
SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS=
```

!!! tip "Finding your chat ID"
    Send `/start` to your bot: until your chat is allowed, it replies with the chat ID to add. Alternatively, send a message to `@userinfobot`.

!!! note "No orders from Telegram"
    The bot answers slash commands only; it places no orders. `/halt REASON` and `/resume` work the kill switch.

### Step 3: Configure Bot Commands (Optional)

Send these commands to `@BotFather` to set up the menu:

```
/setcommands
```

Then paste:

```
quote - Get real-time stock quote
portfolio - View positions and P&L
status - System health check
pnl - Current P&L summary
analyze - Indicators and the swing model's view
ideas - Today's swing ideas
token - INDstocks token status
halt - Kill switch: stop all new BUYs
resume - Lift the kill switch
help - List all commands
```

## Running Locally

```bash
python -m skopaq.telegram_bot
```

The bot starts polling for messages and registers 3 scheduled jobs:

```
Starting SkopaqTrader Telegram bot...
Scheduled jobs (Mon-Fri, NSE trading days):
  09:00 IST — Pre-market INDstocks token check
  09:25 IST — Auto market scan
  15:35 IST — EOD P&L summary
Bot ready. Polling for messages...
```

## Deploying to Fly.io

### Step 1: Create the App

```bash
fly launch --name skopaq-telegram --region bom --no-deploy
```

### Step 2: Set Secrets

```bash
fly secrets set \
  SKOPAQ_TELEGRAM_BOT_TOKEN="your-token" \
  SKOPAQ_INDSTOCKS_TOKEN="your-token" \
  SKOPAQ_TELEGRAM_CHAT_ID="123456789" \
  -a skopaq-telegram
```

### Step 3: Deploy

Use the Telegram-specific Fly config (`fly-telegram.toml`):

```bash
fly deploy --config fly-telegram.toml
```

The Telegram bot runs as a background worker (no HTTP port needed):

```toml
# fly-telegram.toml
app = 'skopaq-telegram'
primary_region = 'bom'

[processes]
  app = "telegram"

[mounts]
  source = "skopaq_data"
  destination = "/data"

[[vm]]
  memory = '512mb'
  cpu_kind = 'shared'
  cpus = 1
```

### Step 4: Create the Volume

```bash
fly volumes create skopaq_data --region bom --size 1 -a skopaq-telegram
```

`fly-telegram.toml` mounts `/data`; the volume is currently unused. The bot reads the INDstocks
token from `SKOPAQ_INDSTOCKS_TOKEN`; the 09:00 job warns when it is missing or expired (check any time with `/token`).

## Deploying with Docker

```bash
docker run -d \
  --name skopaq-telegram \
  --env-file .env \
  samuelvinay91/skopaq:latest \
  telegram
```

Or with Docker Compose:

```bash
docker compose up -d telegram
```

## Architecture

```
Telegram Cloud
      │
      │ (polling)
      ▼
skopaq/telegram_bot.py
      │
      ├── Command Handlers (/quote, /ideas, /analyze, etc.; allow-listed chats only)
      ├── Scheduled Jobs (token check, scan, EOD)
      │
      ├── skopaq/broker/client.py (INDstocks market data, positions, funds)
      ├── skopaq/notifications.py (centralized alerts)
      └── skopaq/mcp_server.py (quotes, portfolio, status, swing ideas, indicators)
```

## Notification System

The bot integrates with `skopaq/notifications.py`. Any part of the system can send alerts:

```python
from skopaq.notifications import notify

await notify("Order filled: BUY 10x RELIANCE @ Rs 2,485")
```

`notify()` sends to `SKOPAQ_TELEGRAM_CHAT_ID` plus any chat registered in the same process (in the bot, with `/start`).

!!! warning "Single bot per token"
    Telegram allows only one bot instance per token. If you run the bot locally and on Fly.io simultaneously, one will fail to receive messages.
