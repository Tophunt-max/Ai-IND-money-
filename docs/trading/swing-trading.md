# Swing Trading (Adaptive AI)

The swing book buys NSE stocks on daily setups that the **swing model** — part of the Adaptive AI — has learned to trust. How it works inside: [Trading Pipeline](../architecture/pipeline.md).

!!! note "Out the same day"
    The position monitor and the daemon's CLOSING sell every position from 15:20 IST, so the swing book does not hold overnight yet. The model is trained on exactly that: entry at the open, stop or target during the day, else out at the close.

## 1. Train a model (no orders)

```bash
skopaq swing-train                       # NIFTY 50 (or SKOPAQ_SWING_SYMBOLS), 5 years of daily history
skopaq swing-train --symbols RELIANCE,TCS,INFY --days 1095 --model linear
skopaq swing-status                      # champion, shadow, test results per version
```

Or **Dashboard → AI learning → Swing model → Train swing model**. A model that passes the gates becomes the *shadow*; one that does not stays a *candidate* (the reasons are listed).

`--model linear` needs no ML library; `lightgbm` (the default), `xgboost`, `catboost` and `random_forest` need their packages.

## 2. See today's ideas

```bash
skopaq scan --limit 10                   # setup, entry / stop / target, P(win)
```

Also: dashboard **Swing scanner**, Telegram `/ideas`, MCP `swing_ideas` / `swing_idea`, Claude Code `/scan`.

## 3. Paper sessions

```bash
skopaq daemon --once --paper             # scan → gate → executor → monitor → close
```

On paper the shadow model trades when there is no champion. With no model at all, `SKOPAQ_SWING_RULES_ONLY=true` trades the raw setups on paper to build a record (never live).

## 4. Promote, then live

After 7 days in shadow:

```bash
skopaq swing-promote swing-lightgbm-20261012T... --by "your name" --reason "paper record ok"
skopaq daemon --once --live --confirm-live
```

Live sessions use the champion only. `skopaq swing-rollback --by NAME --reason TEXT` goes back to the previous one.

## Settings

| Variable | Default | What |
|----------|---------|------|
| `SKOPAQ_SWING_SYMBOLS` | NIFTY 50 | The universe (comma-separated) |
| `SKOPAQ_SWING_TARGET_RR` | 2.0 | Target in R (risk multiples) |
| `SKOPAQ_ATR_MULTIPLIER` | 2.0 | Stop distance in ATRs |
| `SKOPAQ_SWING_MODEL_KIND` | lightgbm | Model trained by `swing-train` |
| `SKOPAQ_SWING_TRAIN_DAYS` | 1825 | Calendar days of daily history |
| `SKOPAQ_SWING_MIN_NET_RR` | 1.0 | The gate's minimum reward/risk after charges |
| `SKOPAQ_SWING_RULES_ONLY` | false | Paper only: trade setups without a model |
| `SKOPAQ_DAEMON_MAX_CANDIDATES_TO_ANALYZE` | 5 | Ideas tried per session |

## Position sizing and limits

The executor sizes every entry from its ATR stop (risk per trade), scaled down in a weak market regime and around events (F&O expiry, RBI policy). The `SafetyChecker` then caps it by the position-size and order-value limits, the open-position count, the loss limits and the kill switch. None of these can be changed by a model.
