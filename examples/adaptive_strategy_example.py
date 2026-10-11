"""An example adaptive strategy: your own features, your own targets.

Use it with::

    skopaq adaptive-train --strategy examples/adaptive_strategy_example.py:MomentumRegressor

or set ``SKOPAQ_ADAPTIVE_STRATEGY=examples/adaptive_strategy_example.py:MomentumRegressor``
in ``.env`` (the scheduler's retraining and the live gate then use it).

Rules the framework enforces:
* feature columns start with ``%``, targets with ``&``;
* features may only use the current and earlier bars — ``check_lookahead`` refuses a
  strategy whose features change when later candles arrive (try ``shift(-1)`` below);
* targets may look ahead ``label_period`` bars; that horizon becomes the embargo between
  training, validation and test.
"""

from __future__ import annotations

import pandas as pd

from skopaq.adaptive.strategy import AdaptiveStrategy


class MomentumRegressor(AdaptiveStrategy):
    name = "momentum_regressor"
    timeframes = (60, 300)                 # 1-minute base, 5-minute higher timeframe
    periods = (5, 15, 30)
    shifted = 2
    correlated = ("HDFCBANK",)             # its features are added, prefixed "HDFCBANK|"
    label_period = 20
    model_type = "regressor"               # predict a value, not a class

    def feature_engineering_expand_all(self, df: pd.DataFrame, period: int,
                                       metadata: dict) -> pd.DataFrame:
        c = df["close"]
        df["%-mom"] = c / c.shift(period) - 1
        df["%-zscore"] = (c - c.rolling(period).mean()) / c.rolling(period).std()
        df["%-range"] = (df["high"] - df["low"]).rolling(period).mean() / c
        # df["%-cheat"] = c.shift(-1)      # ← look-ahead: training would be refused
        return df

    def feature_engineering_expand_basic(self, df: pd.DataFrame,
                                         metadata: dict) -> pd.DataFrame:
        df["%-ret"] = df["close"].pct_change()
        df["%-vol_chg"] = df["volume"].pct_change()
        return df

    def feature_engineering_standard(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dt = pd.to_datetime(df["date"])
        df["%-minute"] = dt.dt.hour * 60 + dt.dt.minute
        return df

    def set_targets(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # The mean return of the next label_period bars (FreqAI's &-s_close idea)
        fwd = df["close"].shift(-self.label_period).rolling(self.label_period).mean()
        df["&-s_close"] = fwd / df["close"] - 1
        return df

    def entry_signal(self, prediction: dict, metadata: dict):
        """Enter when the predicted return is 1.5 standard deviations above the mean of
        recent predictions (the framework keeps those statistics live)."""
        t = prediction["targets"]["&-s_close"]
        if t["pred_std"]:
            return t["value"] > t["pred_mean"] + 1.5 * t["pred_std"]
        return None
