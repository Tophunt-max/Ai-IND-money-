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
| Reinforcement learning | PPO, A2C and DQN (`rl_agents.py`, PyTorch) in an environment that replays the scalper's own rules net of charges (`rl_env.py`). A policy trades as the `rl_policy` scalp strategy behind every gate (`rl_live.py`, see below). `rl.py` stays a tabular research baseline |

`FeatureExpander` builds, for each timeframe (1, 5 and 15 minutes) and each period:

- RSI, ROC, ATR %, Bollinger width, distance from the SMA, relative volume;
- ADX, DI and the EMA 9/21 gap;
- RSI and ROC shifted by 1–3 bars;
- the same set for each correlated symbol;
- session minute and day of the week.

## Your own strategy (full control)

Subclass `skopaq.adaptive.strategy.AdaptiveStrategy`. These hooks match FreqAI's:

- `feature_engineering_expand_all(df, period, metadata)` — runs once per timeframe and period;
- `feature_engineering_expand_basic(df, metadata)` — once per timeframe;
- `feature_engineering_standard(df, metadata)` — once, on the base timeframe;
- `set_targets(df, metadata)` — sets the targets;
- `entry_signal(prediction, metadata)` (optional) — decides an entry from the prediction.

Naming: features start with `%` and targets with `&`. Set `model_type = "regressor"` to
predict values instead of classes. A full example is in
`examples/adaptive_strategy_example.py`. Use your class with
`--strategy file.py:Class` or `SKOPAQ_ADAPTIVE_STRATEGY` (set in .env only, because it
runs code).

Higher timeframes are resampled from the base bars and joined only once they have closed.
Shifted copies and the correlated symbols' features are added automatically.

**Look-ahead guard.** Features are recomputed with the history cut at several points. If
any earlier value changes (for example `shift(-1)` or a centred window), training is
refused. FreqAI has no such check.

## Data pipeline

`skopaq/adaptive/pipeline.py` is fitted on the training rows only. The steps, in order:

1. Variance threshold.
2. Scaler: `minmax` (FreqAI's default), `standard`, `robust` or `none`.
3. **PCA** (99.9 % of variance).
4. Outlier detectors, each optional:
   - **One-class SVM** — removes training outliers and flags live points;
   - **Dissimilarity Index**;
   - **DBSCAN** — removes training noise and flags live points far from every core point.
5. Training **noise**.

`weight_factor` weights recent rows more, using FreqAI's formula. All fitted parameters
are stored as JSON. The SVM and DBSCAN checks are re-implemented in NumPy from the stored
support and core points.

## Models

| kind | library | task | saved as |
|---|---|---|---|
| lightgbm | LightGBM | classifier / regressor, early stopping | `lightgbm.txt` |
| xgboost, xgboost_rf | XGBoost | both, early stopping | `xgboost.json` |
| catboost | CatBoost | both, early stopping | `catboost.cbm` |
| random_forest, extra_trees | scikit-learn | both | `trees.npz` (arrays, no pickle) |
| linear | NumPy | logistic / ridge | `linear.json` |
| mlp, ft_transformer | PyTorch | both, early stopping | `torch.safetensors` |

Each target gets its own model (multi-target).

## Prediction output

Each prediction returns:

- **Per target:**
  - `value`;
  - training `label_mean` / `label_std`;
  - `pred_mean` / `pred_std`, taken from the last 500 live predictions once there are 20,
    otherwise from validation (like FreqAI's `fit_live_predictions`);
  - `zscore`.
- **Data check:** `do_predict` (1 ok, 0 outlier, -1 not enough data) and the `missing_features`
  count.
- **Outliers:** the `di` value and each detector's flag (`detectors`).
- **Entry:** `entry`, plus the `entry_rule` used: a probability threshold or mean + k·std,
  chosen on validation, or the strategy's `entry_signal`. Backtests and live use the same
  rule.
- **Model:** `version`, `age_hours`, `top_features`.

The scalper's signals store this output in `evidence.adaptive`. With the model gate on, a
bundle decides with its own entry rule.

## Model saving

Each model is saved as a bundle in `<model_dir>/artifacts/<version>/`:

- the model files;
- `pipeline.json.gz`;
- `bundle.json`, which holds the strategy layout, features, targets, statistics, entry rule
  and feature importance;
- `report.json`;
- `manifest.json`, which holds the SHA-256 and size of every file, the library and Python
  versions, and the training data fingerprint.

On load, every file is checked against its hash, and any change refuses the model. The
commands:

- `skopaq model-verify` — checks all bundles;
- `skopaq model-export VERSION file.tar.gz` and `skopaq model-import file.tar.gz` — move a
  bundle between hosts. The import refuses `..` paths and links, and lands as a candidate
  that is gated again;
- `SKOPAQ_ADAPTIVE_KEEP_MODELS` — retention. The champion, shadow and rollback target are
  never deleted.

Install the optional libraries:

- `pip install -e ".[ml]"` — LightGBM, XGBoost, CatBoost, Optuna, scikit-learn;
- `.[torch]` — PyTorch, using the CPU wheels.

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

## Reinforcement learning: PPO, A2C, DQN

FreqAI drives stable-baselines3; here the three algorithms are compact native PyTorch
implementations (`skopaq/adaptive/rl_agents.py`), saved as `safetensors` + JSON with a
SHA-256 manifest (no pickle).

**Environment** (`rl_env.py`): one session of 1-minute candles, decided on each close and
filled at the next open with slippage. Actions: hold, enter long, exit (masked when they
do not apply). Every entry gets the live geometry (ATR stop bounded 0.4–2.5 ATR, `rr`
target) and is managed by the scalper's own `update_trail` / `exit_reason` (stop, trail,
target, time stop) and the square-off time; the agent can only exit earlier. An entry the
live scalper would refuse on cost (its reward-to-charges check, or net reward/risk after
charges and slippage below `SKOPAQ_SCALP_MIN_NET_RR`) is not taken in training either. The reward is
the change of equity after every charge, so the rewards of a day add up to its net P&L.
Observations: 10 point-in-time features plus the position state.

**Evaluation:** trained on each symbol's earlier sessions, replayed greedily on its later
unseen sessions; the report (net, profit factor, bootstrap CI, drawdown, charges, exits)
goes through the registry's gates against a buy-and-hold baseline.

**Live / paper trading** (`rl_live.py`), opt in with `SKOPAQ_RL_ENABLED=true` and
`rl_policy` in `SKOPAQ_SCALP_STRATEGIES`:

| | Policy used |
|---|---|
| Paper account | RL champion, else the shadow policy (`SKOPAQ_RL_PAPER_USE_SHADOW`) |
| Live account | the RL champion only (gates passed, shadow days, named admin approval) **and** `SKOPAQ_RL_ALLOW_LIVE=true` in `.env` (locked against dashboard overrides) |

On a closed candle the policy's *enter* becomes a scalp `Setup` that passes the regime map,
the cost check, `SignalGate` qualification (net R:R after charges, spread, liquidity,
model gate), sizing, the entry gate and the safety checker (kill switch, limits, funds)
like any other strategy; the decision and its action scores are journalled as
`evidence.rl`. Its *exit* sells a position it opened through the normal exit path; it
cannot move a stop or a target. RL registry: `<model_dir>/rl` (separate from the
model gate's champion). Dashboard: **AI learning → Reinforcement learning**.

## Commands (no orders)

```bash
skopaq adaptive-train --symbols RELIANCE,TCS,INFY --correlated HDFCBANK --days 30 --model lightgbm
skopaq hyperopt --symbols RELIANCE,TCS --days 30 --trials 60
skopaq rl-research RELIANCE --days 30
skopaq rl-train --symbols RELIANCE,TCS --algo ppo --days 60      # or a2c / dqn
skopaq rl-status
skopaq rl-promote <version> --by "Your Name" --reason "7 paper days reviewed"
skopaq rl-rollback --by "Your Name" --reason "..."
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
- An RL policy learns from the same few weeks the gates test it on later; with little data
  PPO / A2C often learn "never trade" (charges dominate), which is the honest outcome.
- Feature expansion costs a few milliseconds per bar: training on 30 days × 10 symbols takes
  minutes. The daemon's retrain has a 15-minute budget.
