# Configuration

All configuration is via environment variables with the `SKOPAQ_` prefix, loaded from `.env`.

## Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SKOPAQ_TRADING_MODE` | `paper` | `paper` or `live` |
| `SKOPAQ_ASSET_CLASS` | `equity` | `equity` or `crypto` |
| `SKOPAQ_INITIAL_PAPER_CAPITAL` | `1000000` | Paper trading capital (INR) |

## Adaptive AI

| Variable | Default | Description |
|----------|---------|-------------|
| `SKOPAQ_MODEL_DIR` | `~/.skopaq/models` | Model registries (candidate / shadow / champion); the swing model in `swing/` |
| `SKOPAQ_SIGNAL_JOURNAL_DIR` | `~/.skopaq/signals` | Every signal and its decision (learning data) |
| `SKOPAQ_SWING_SYMBOLS` | NIFTY 50 | Swing universe (comma-separated) |
| `SKOPAQ_SWING_TARGET_RR` | `2.0` | Swing target in R |
| `SKOPAQ_SWING_MODEL_KIND` | `lightgbm` | Model `skopaq swing-train` fits (`linear` needs no ML library) |
| `SKOPAQ_SWING_TRAIN_DAYS` | `1825` | Days of daily history per training run |
| `SKOPAQ_SWING_MIN_NET_RR` | `1.0` | Swing gate: minimum reward/risk after charges |
| `SKOPAQ_SWING_RULES_ONLY` | `false` | Paper only: trade swing setups without a model |
| `SKOPAQ_RL_ENABLED` | `false` | RL policies as the `rl_policy` scalp strategy |

There are no LLM or AI API keys.

## Broker

| Variable | Description |
|----------|-------------|
| `SKOPAQ_INDSTOCKS_TOKEN` | INDstocks API token (the only broker: market data, equity and F&O) |
| `SKOPAQ_INDSTOCKS_BASE_URL` | INDstocks API base URL (default `https://api.indstocks.com`) |

## Live Orders (INDstocks)

Live mode only. A live trade counts only when INDstocks confirms the fill; these settle how
long Skopaq waits, how hard it works a protective exit and how SELLs are checked against
open orders. The full list is in [Live Trading](../trading/live-trading.md#live-order-configuration).

| Variable | Default | Description |
|----------|---------|-------------|
| `SKOPAQ_ORDER_FILL_TIMEOUT_SECONDS` | `30` | Entries: cancel whatever has not filled after this long |
| `SKOPAQ_ORDER_EXIT_MAX_ATTEMPTS` | `3` | Protective exits: cancel and re-place a resting exit, this many attempts at most |
| `SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK` | `false` | **Danger:** SELL even when the order book cannot be read |

## Risk Management

| Variable | Default | Description |
|----------|---------|-------------|
| `SKOPAQ_POSITION_SIZING_ENABLED` | `true` | ATR-based position sizing |
| `SKOPAQ_RISK_PER_TRADE_PCT` | `0.01` | 1% of equity per trade |
| `SKOPAQ_ATR_MULTIPLIER` | `2.0` | Stop distance in ATR units |
| `SKOPAQ_MAX_SECTOR_CONCENTRATION_PCT` | `0.40` | Max 40% in one sector |
| `SKOPAQ_MIN_CONFIDENCE_PCT` | `0` | Minimum signal confidence to trade (a swing signal's confidence is the model's P(win) × 100) |

## Position Monitor

| Variable | Default | Description |
|----------|---------|-------------|
| `SKOPAQ_MONITOR_POLL_INTERVAL_SECONDS` | `10` | Check interval |
| `SKOPAQ_MONITOR_HARD_STOP_PCT` | `0.04` | 4% hard stop |
| `SKOPAQ_MONITOR_EOD_EXIT_MINUTES_BEFORE_CLOSE` | `10` | EOD exit at 15:20 |
| `SKOPAQ_MONITOR_TRAILING_STOP_ENABLED` | `false` | Enable trailing stop |
| `SKOPAQ_MONITOR_TRAILING_STOP_PCT` | `0.02` | 2% trail distance |
| `SKOPAQ_MONITOR_RESYNC_CYCLES` | `3` | Live: re-read the broker's order book and positions every N polls |

## Daemon (Autonomous)

| Variable | Default | Description |
|----------|---------|-------------|
| `SKOPAQ_DAEMON_MAX_TRADES_PER_SESSION` | `3` | Max BUY orders/day |
| `SKOPAQ_DAEMON_MAX_CANDIDATES_TO_ANALYZE` | `5` | Top N swing ideas tried per session |
| `SKOPAQ_DAEMON_SCAN_DELAY_AFTER_OPEN_SECONDS` | `60` | Wait after 9:15 |

## Telegram

| Variable | Description |
|----------|-------------|
| `SKOPAQ_TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `SKOPAQ_TELEGRAM_CHAT_ID` | Your chat ID: notifications, and allowed to use the bot |
| `SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS` | More chat IDs allowed to use the bot (comma-separated) |
