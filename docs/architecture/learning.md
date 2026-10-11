# Quantitative Learning Engine

The signal tracker (`skopaq/learning/tracker.py`) records every closed trade and its outcome, so you can see what works and what doesn't. It stores to `DATABASE_URL` (Postgres) and does nothing without it.

## What It Tracks

| Metric | Question It Answers |
|--------|-------------------|
| **Symbol Accuracy** | Which stocks is the AI best/worst at? |
| **Confidence Calibration** | When AI says 75%, does it win 75%? |
| **Sector Performance** | Banking vs IT vs Energy — where's the edge? |
| **Regime Performance** | High VIX vs low VIX — different strategies? |
| **Timing Patterns** | Morning entries vs afternoon — which works? |
| **Stop-Loss Analysis** | Are stops too tight (hit before recovery)? |
| **Holding Period** | Optimal days to hold per stock? |

## How It Works

```
Position closed (trade booking) → record: symbol, confidence, entry/exit, P&L, won/lost,
                                   sector, IST entry hour, holding days, exit reason
        ↓
Tracker queries → per-symbol, calibration, sector, regime, timing, stop-loss stats
        ↓
Insights report → "OVERCONFIDENT on IT stocks", "Stops too tight on RELIANCE"
```

The report is shown on the dashboard's **AI learning** page (`/api/dashboard/learning[/symbol]`).

## MCP Tools

- `get_learning_insights` — Full learning report with all patterns
- `get_symbol_stats RELIANCE` — Per-symbol performance stats

## Database Tables

- `signal_records` — Every signal with outcome (Fly.io Postgres)

## What it does not do

The insights are for review: nothing reads them back into trading, and they change no
stop, size or ranking. The models that learn are the Adaptive AI's (the swing model,
`skopaq swing-train`; the scalper's `learn-train` / `adaptive-train`; RL policies), each
through a registry with gates, a shadow period and an admin's promotion — see
[Pipeline](pipeline.md) and [Execution safety](execution-safety.md#learning-lifecycle).
