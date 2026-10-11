# Adaptive AI (FreqAI-style)

`skopaq/adaptive/` brings FreqAI's ideas to Indian equities, implemented natively.
Freqtrade (and FreqAI) is GPL-3.0, while this project is Apache-2.0, so no Freqtrade code
is copied. FreqAI is also tied to Freqtrade's crypto exchange layer.

## What it does

| FreqAI idea | Here |
|---|---|
| Feature expansion across timeframes, periods, shifts and correlated pairs | `features.FeatureExpander` (details below). Values use only bars closed at the decision time |
| Outlier detection: Dissimilarity Index, SVM/DBSCAN | `outliers.DissimilarityIndex` on live points. `filter_training` (robust z-score, optional Isolation Forest) on the training rows |
| Many model libraries | LightGBM, XGBoost, CatBoost and the NumPy logistic baseline (`--model`). Saved as text/JSON, never pickle |
| Periodic live retraining | `adaptive_auto_retrain`: retrains at the end of a session after `adaptive_retrain_hours` (see "Where it differs") |
| Backtesting that emulates retraining | `engine.rolling_backtest`: trains on the last `train_days`, predicts the next `retrain_days`, repeats. Every prediction is out of sample |
| Hyperopt | `hyperopt.hyperopt` (Optuna TPE, fixed seed) for the scalper's strategy parameters |
| Reinforcement learning | `rl.py`: environment net of charges and a tabular Q-learning baseline. Research only, never wired to orders |

`FeatureExpander` builds, for each timeframe (1, 5 and 15 minutes) and each period:

- RSI, ROC, ATR %, Bollinger width, distance from the SMA, relative volume;
- ADX, DI and the EMA 9/21 gap;
- RSI and ROC shifted by 1–3 bars;
- the same set for each correlated symbol;
- session minute and day of the week.

## Where it differs from FreqAI, deliberately

FreqAI uses a retrained model at once. Here a retrained model is only a **candidate**:

1. **Gates on unseen data.** Before a model may move on, it must pass all of these on the
   test period:
   - at least 100 test samples;
   - at least 30 gated trades;
   - beat the baseline and the current champion;
   - profit factor ≥ 1.1;
   - a 90 % confidence interval of expectancy above zero;
   - a drawdown no worse than the baseline's;
   - a better Brier score than the base rate.
2. **Shadow.** A model that passes is recorded on every signal but never decides.
3. **Champion.** It is promoted only after the shadow days and a named admin's approval.

Rollback restores the previous champion.

- **The gate is opt-in.** The champion's probability and Dissimilarity Index are recorded on
  every signal (`evidence.adaptive`). They decide entries only with `SKOPAQ_*_MODEL_GATE_ENABLED`
  and `SKOPAQ_BLOCK_MODEL_OUTLIERS`.
- **Labels are net of costs.** The `forward` target asks whether a fixed-notional long over
  `horizon_bars` makes money after Indian charges and slippage, not merely whether the price
  rises.
- **Hyperopt needs approval.** Parameters are searched on 70 % of the days and must beat the
  current ones on the other 30 %. Only then can an admin approve them, and rollback is
  supported. Risk limits are never part of the search.

## Commands (no orders)

```bash
skopaq adaptive-train --symbols RELIANCE,TCS,INFY --correlated HDFCBANK --days 30 --model lightgbm
skopaq hyperopt --symbols RELIANCE,TCS --days 30 --trials 60
skopaq rl-research RELIANCE --days 30
```

To install the optional libraries: `pip install -e ".[ml]"` (LightGBM, XGBoost, CatBoost,
Optuna). Without them, training falls back to the logistic baseline.

The dashboard's **AI learning → Adaptive AI** card shows:

- the configuration and the last retrain;
- the latest model's test and rolling-retrain results (every bar vs model vs model with the
  outlier gate);
- the hyperopt candidate, with its approve and roll-back buttons.

## Limits

- Results on synthetic data in the tests prove the mechanics, not an edge. Use real INDstocks
  history: the token is set in `.env`.
- The RL module is a baseline simulator, not a strategy.
- Feature expansion costs a few milliseconds per bar: training on 30 days × 10 symbols takes
  minutes. The daemon's retrain has a 15-minute budget.
