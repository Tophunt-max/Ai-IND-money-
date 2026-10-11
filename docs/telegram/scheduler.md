# Scheduled Jobs

The Telegram bot runs three scheduled jobs on NSE trading days. They are configured in `skopaq/telegram_bot.py` using `python-telegram-bot`'s built-in job queue: each runs Monday to Friday (`days=(1, 2, 3, 4, 5)`; in python-telegram-bot 20+, 0 is Sunday) and returns early on NSE holidays (`skopaq/risk/calendar.py`, plus `SKOPAQ_NSE_HOLIDAYS`).

!!! note "Not the trading daemon"
    These jobs only send messages. Autonomous trading sessions are started by the
    compose `scheduler` service ([Mac mini runbook](../deployment/mac-mini.md)).

## Job Schedule

| Time (IST) | Time (UTC) | Job | Description |
|------------|------------|-----|-------------|
| 09:00 | 03:30 | Pre-Market Token Check | INDstocks token status |
| 09:25 | 03:55 | Market Scan | Top movers among 10 blue chips |
| 15:35 | 10:05 | EOD Summary | End-of-day P&L report |

## Pre-Market Token Check (09:00 IST)

Checks the INDstocks access token before the market opens.

**What it does:**

1. Reads the token health from `TokenManager` (same check as `/token`)
2. If valid: sends "Good morning! INDstocks token valid (…left). Market opens at 9:15 IST. Auto-scan at 9:25."
3. If missing or expired: says so and explains how to set it (dashboard Broker page or `skopaq token set <TOKEN>`)

**Why 09:00?**

NSE pre-open session starts at 09:00. Checking early leaves time to set a fresh token before 09:15 when regular trading begins.

```python
# IST 9:00 = UTC 3:30, Monday to Friday
app.job_queue.run_daily(
    job_pre_market_token,
    time=dt_time(hour=3, minute=30, tzinfo=timezone.utc),
    days=weekdays,
    name="pre_market_token",
)
```

## Market Scan (09:25 IST)

A quote scan of 10 blue chips sent to all registered chats. It does not run the swing model; it points to `/ideas` and `/analyze` for that.

**What it does:**

1. Verifies the INDstocks token (sends the token message and stops if it is missing or expired)
2. Sends "Market open! Scanning NIFTY 50 for opportunities..."
3. Fetches live INDstocks quotes (one batched call) for:
   RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, LT, BHARTIARTL, WIPRO, NTPC
4. Sorts by absolute change% and sends the top 8

**Example output:**

```
📊 Market Scan (09:25 IST)

🟢 RELIANCE: Rs 2,485.50 (+1.23%) Vol: 1,234,567
🟢 HDFCBANK: Rs 1,567.80 (+0.89%) Vol: 845,230
🔴 SBIN: Rs 756.30 (-0.45%) Vol: 1,567,890
🟢 TCS: Rs 3,750.00 (+0.32%) Vol: 321,456
...

/ideas — the Adaptive AI's swing ideas
/analyze SYMBOL — one stock's setup and indicators
```

**Why 09:25?**

Prices settle about 10 minutes after market open (09:15). Scanning at 09:25 gives enough data for meaningful volume and direction signals.

!!! note "Customizing the watchlist"
    The scanned stocks are the `_SCAN_SYMBOLS` tuple in `skopaq/telegram_bot.py`; edit it to customize.

## EOD Summary (15:35 IST)

Sends end-of-day portfolio summary after market close.

**What it does:**

1. Checks the INDstocks token (skips if missing or expired)
2. Fetches INDstocks equity and F&O positions and funds
3. Calculates total P&L
4. Sends formatted summary

**Example output:**

```
Market Closed — EOD Summary

Cash: Rs 987,654.00

Positions (2):
  RELIANCE [CNC]: 10x @ 2400.00 P&L: Rs +855.00
  TCS [CNC]: 5x @ 3800.00 P&L: Rs -250.00

Total realised P&L: Rs +605.00
```

**Why 15:35?**

NSE regular trading ends at 15:30. The 5-minute buffer ensures all orders are settled and final prices are available.

## How Jobs Are Registered

All three jobs use `run_daily` from `python-telegram-bot`'s `JobQueue`:

```python
from datetime import time as dt_time, timezone

# Monday to Friday (PTB 20+: 0 = Sunday); each job also returns early on NSE holidays.
weekdays = (1, 2, 3, 4, 5)

# IST 9:00 = UTC 3:30
app.job_queue.run_daily(
    job_pre_market_token,
    time=dt_time(hour=3, minute=30, tzinfo=timezone.utc),
    days=weekdays,
    name="pre_market_token",
)

# IST 9:25 = UTC 3:55
app.job_queue.run_daily(
    job_market_scan,
    time=dt_time(hour=3, minute=55, tzinfo=timezone.utc),
    days=weekdays,
    name="market_scan",
)

# IST 15:35 = UTC 10:05
app.job_queue.run_daily(
    job_eod_summary,
    time=dt_time(hour=10, minute=5, tzinfo=timezone.utc),
    days=weekdays,
    name="eod_summary",
)
```

With `SKOPAQ_HEARTBEAT_FILE` set (docker compose sets it), a fourth job touches that file
every minute for the container health check.

!!! warning "UTC times"
    All times are specified in UTC. IST = UTC + 5:30. If you change these, remember to convert correctly.

## Notification Routing

Scheduled jobs send messages to all registered chat IDs in `alert_chat_ids`. A chat ID is registered when a user sends `/start` to the bot.

```
/start → chat_id added to alert_chat_ids
                          │
                          ▼
Scheduled jobs → send to all registered chat_ids
```

If no chat IDs are registered, scheduled jobs run silently (no errors, just no output).

## Adding Custom Jobs

To add a new scheduled job, follow this pattern in `skopaq/telegram_bot.py`:

```python
async def job_custom(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Your custom job description."""
    for chat_id in list(alert_chat_ids):
        await context.bot.send_message(
            chat_id=chat_id,
            text="Your scheduled message here",
        )

# Register in main():
app.job_queue.run_daily(
    job_custom,
    time=dt_time(hour=6, minute=0, tzinfo=timezone.utc),  # 11:30 IST
    days=(1, 2, 3, 4, 5),
    name="custom_job",
)
```

## Weekday-Only Execution

`days=(1, 2, 3, 4, 5)` keeps the jobs off weekends, and each job starts with
`if not _is_trading_day_ist(): return`, which skips NSE holidays. Add the same check to
custom market-hours jobs. When the current year has no NSE holiday list, the jobs stay
silent until it is added (see the [Mac mini runbook](../deployment/mac-mini.md), Holidays).
