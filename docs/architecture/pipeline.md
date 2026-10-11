# Trading Pipeline (Adaptive AI)

SkopaqTrader has **one decision-maker: the Adaptive AI** — machine-learning models trained on our own market data, signals and trades. There is no LLM and no API AI anywhere in the trading path.

## The three books

| Book | Engine | Decides with | Command |
|------|--------|--------------|---------|
| Swing (delivery, out the same day) | `skopaq/swing/engine.py` | daily setups + the swing model | `skopaq daemon`, `skopaq scan` |
| Intraday scalps | `skopaq/scalping/engine.py` | rule strategies, adaptive models, RL policies | `skopaq scalp` |
| F&O (options buying) | `skopaq/scalping/fno_engine.py` | index setups + adaptive models | `skopaq fno` |

All three send every entry through the same `SignalGate` (`skopaq/strategy/gate.py`) and the same `Executor` → `SafetyChecker` → `OrderRouter`.

## Swing book

```
PRE_OPEN   load the swing model (champion; on paper the shadow if there is no champion)
SCANNING   SwingEngine.scan(universe)
             for each symbol: a year of daily candles (today's forming bar dropped)
             → the setup on the last closed bar (trend_pullback / breakout / oversold_bounce)
             → point-in-time features → the model's P(win)
             → kept when P(win) ≥ the threshold chosen on validation data; best first
TRADING    SwingEngine.trade(idea)
             → SignalGate.decide: net reward/risk after delivery charges, data quality,
               stop distance 0.5–15 %, journalled QUALIFIED / REJECTED
             → Executor: ATR position sizing, regime and calendar scaling,
               SafetyChecker (kill switch, limits, funds), paper or live
             → exit plan: the setup's ATR stop and its R-multiple target
MONITORING PositionMonitor: stop, target (partial booking), trailing, EOD exit at 15:20
CLOSING    sells what is still held; REPORTING sends the session summary
```

Without a usable model the swing book makes **no entries**. `SKOPAQ_SWING_RULES_ONLY=true` lets a paper account trade the raw setups (ranked by trend strength) to collect a record; it is ignored on a live account.

### Setups

Rules on closed daily bars (`skopaq/swing/setups.py`); the first that fires wins:

| Setup | When |
|-------|------|
| `trend_pullback` | close > SMA50 > SMA200, EMA20 > SMA50, a low within 1 % of EMA20, green bar above EMA20, RSI 40–65 |
| `breakout` | close above the 20-day high on > 1.5× average volume, closing in the top 30 % of the bar, above SMA50 |
| `oversold_bounce` | above SMA200, yesterday's RSI < 35, close above yesterday's high |

Entry = the last close; stop = entry − `SKOPAQ_ATR_MULTIPLIER` × ATR(14); target = entry + `SKOPAQ_SWING_TARGET_RR` × risk.

### The swing model

`skopaq/swing/model.py`:

1. **Dataset** — every historical bar where a setup fired, with its features (returns, RSI, ATR %, distance to the moving averages, SMA50 slope, volume ratio, range position, gap, the setup) and the outcome of trading it: buy at the next open plus slippage, stop first, then target, else out at that day's close — the same-day exit the live book makes — **net of delivery charges**.
2. **Split by date** — train (60 %), validation (15 %: picks the probability threshold), test (the latest 25 %, never seen).
3. **Gates** — the model becomes a **shadow** only if, on the test dates, it has enough samples and taken trades, beats taking every setup, has a profit factor ≥ 1.1, a 90 % expectancy CI above zero, no worse drawdown and a better Brier score. Otherwise it stays a **candidate**.
4. **Promotion** — after the shadow period (`SafetyRules`, 7 days) an admin promotes it to **champion** (`skopaq swing-promote`, dashboard AI learning page). Live sessions trade only a champion. `skopaq swing-rollback` restores the previous one.

Bundles are pickle-free (the model library's own format, the pipeline as JSON, a SHA-256 manifest verified on every load); a tampered bundle is refused and the book makes no entries.

## What the AI cannot change

`SafetyRules` (`skopaq/constants.py`), the loss limits, the kill switch, position and order caps, the stop on every BUY and paper-by-default are outside every model. Learning only decides *which* qualified setups to take.
