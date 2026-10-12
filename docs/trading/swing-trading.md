# Swing Trading (Adaptive AI)

The swing book buys NSE stocks on daily setups that the **swing model** — part of the Adaptive AI — has learned to trust. How it works inside: [Trading Pipeline](../architecture/pipeline.md).

!!! warning "Out the same day: daily setups, intraday holding"
    The position monitor and the daemon's CLOSING sell every position from 15:20 IST, so the swing book does not hold overnight yet. The model is trained on exactly that: entry at the open, stop or target during the day, else out at the close. Same-day exits are charged at intraday rates (a CNC squared off the day it was bought), both in training and in the SignalGate.

    With a 2 ATR stop and a 2R target, the target is 4 ATR away: on the same day it is almost never reached, so nearly every trade ends at the close (see the results below).

## Results on real data (October 2026)

All three setups were tested on NIFTY 50 daily candles, 2019-08 to 2026-10 (taken from Yahoo Finance for this one-off study, made without an INDstocks token; the app itself now reads only INDstocks — re-run `skopaq swing-train` with a token to confirm). 50 stocks, about 8,600 setups. Default stop and target, ₹1 lakh per trade, 0.1 % slippage, net of charges. The **test** column is the unseen period (Feb 2025 onwards) that the gates judge.

| Hold (days) | Win | Avg net / trade | Profit factor 2019–26 | Profit factor, test | Gates |
|---|---|---|---|---|---|
| 1 (today's book) | 37 % | ₹-341 | 0.55 | 0.44 | rejected (no trade passes the threshold) |
| 3 | 41 % | ₹-378 | 0.70 | 0.52 | rejected |
| 5 | 45 % | ₹-235 | 0.85 | 0.58 | rejected |
| 10 | 46 % | ₹+21 | 1.01 | 0.64 | rejected |
| 15 | 44 % | ₹+78 | 1.03 | 0.68 | rejected |

What this shows:

- **Same-day exits lose even before charges.** The gross profit factor is about 0.6: buying the open after a daily setup and selling at the close has no edge. Tighter stops and targets (0.5–1 ATR, 1–2R) did not help either (profit factor 0.29–0.41).
- **Longer holds are better, but not profitable.** Over 7 years a 10–15 day hold roughly breaks even after charges. In the unseen 2025–26 period it still loses.
- **The gates work as intended.** No version becomes a shadow, so the swing book places no swing entries. Do not bypass this with `SKOPAQ_SWING_RULES_ONLY` on a live account (it is paper only anyway).

Re-check on your own data with `skopaq swing-train` (it reports the test results and gate failures). New setups or features are needed before the swing book can trade.

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
