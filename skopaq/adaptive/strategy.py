"""User-defined adaptive strategies: you write the features and the targets (FreqAI's
strategy hooks), the framework does the rest.

Subclass ``AdaptiveStrategy`` and override any of::

    feature_engineering_expand_all(df, period, metadata)   # per timeframe × period
    feature_engineering_expand_basic(df, metadata)          # per timeframe
    feature_engineering_standard(df, metadata)              # once, on the base timeframe
    set_targets(df, metadata)                               # the labels
    entry_signal(prediction, metadata) -> bool               # optional entry rule

``df`` is a pandas DataFrame (``date``, ``open``, ``high``, ``low``, ``close``,
``volume``). Feature columns start with ``%`` and target columns with ``&`` (FreqAI's
convention). ``metadata`` is ``{"symbol", "tf", "is_correlated"}``.

The framework (``build_frame``) then:

* runs the expand hooks on every ``timeframes`` entry (higher timeframes are resampled
  from the base bars, aligned to 09:15 IST, and joined to a base bar only once the
  higher bar has *closed* — no partial bar ever leaks), for every ``periods`` value;
* adds ``shifted`` copies of every ``%`` column (``%x_shift-1``, …);
* adds the same features of each ``correlated`` symbol, prefixed ``SYMBOL|``;
* runs ``set_targets`` on the base timeframe.

**Look-ahead guard.** ``check_lookahead`` recomputes the features on the history cut at
several points and requires every feature of every earlier bar to be unchanged. A
strategy that uses ``shift(-n)``, a centred rolling window or any future value in a
feature is refused before training (``LookAheadError``). Targets may look ahead — that is
what they are — and their horizon (``label_period``) sets the embargo between training,
validation and test.

Load your own class with ``SKOPAQ_ADAPTIVE_STRATEGY=path/to/file.py:ClassName`` (an
.env setting, not editable from the dashboard: it runs code). Without code, build one in
the dashboard (``skopaq.adaptive.spec``): it is data, validated, and selectable there.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd


class LookAheadError(ValueError):
    """A feature changed when later candles were added: it uses the future."""


class AdaptiveStrategy:
    """The default strategy: a FreqAI-like feature set and a cost-aware forward target."""

    name = "default"
    timeframes: tuple[int, ...] = (60, 300, 900)       # seconds; the first is the base
    periods: tuple[int, ...] = (10, 20)
    shifted: int = 2
    correlated: tuple[str, ...] = ()
    label_period: int = 15                              # bars the target looks ahead
    # "classifier": targets are classes (0/1 …); "regressor": continuous values
    model_type: str = "classifier"
    cost_pct: float = 0.0012                           # round-trip charges + slippage

    # ── hooks ───────────────────────────────────────────────────────────

    def feature_engineering_expand_all(self, df: pd.DataFrame, period: int,
                                       metadata: dict) -> pd.DataFrame:
        c = df["close"]
        d = c.diff()
        gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        df["%-rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        df["%-roc"] = c.pct_change(period)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(),
                        (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
        df["%-atr_pct"] = tr.rolling(period).mean() / c
        mid = c.rolling(period).mean()
        sd = c.rolling(period).std(ddof=0)
        df["%-bb_width"] = 4 * sd / mid
        df["%-sma_dist"] = c / mid - 1
        df["%-rel_vol"] = df["volume"] / df["volume"].rolling(period).mean()
        return df

    def feature_engineering_expand_basic(self, df: pd.DataFrame,
                                         metadata: dict) -> pd.DataFrame:
        c = df["close"]
        df["%-pct_change"] = c.pct_change()
        df["%-ema_gap"] = (c.ewm(span=9, adjust=False).mean()
                           - c.ewm(span=21, adjust=False).mean()) / c
        df["%-body"] = (df["close"] - df["open"]) / (df["high"] - df["low"]).replace(0, np.nan)
        return df

    def feature_engineering_standard(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dt = pd.to_datetime(df["date"])
        df["%-minute_of_session"] = dt.dt.hour * 60 + dt.dt.minute - (9 * 60 + 15)
        df["%-day_of_week"] = dt.dt.dayofweek
        return df

    def set_targets(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """``&-win``: does a long from the next open, held ``label_period`` bars, beat the
        round-trip costs? (1 / 0)."""
        fwd = df["close"].shift(-self.label_period) / df["open"].shift(-1) - 1
        df["&-win"] = np.where(fwd.isna(), np.nan, (fwd > self.cost_pct).astype(float))
        return df

    def entry_signal(self, prediction: Mapping[str, Any], metadata: dict) -> Optional[bool]:
        """Return True/False to decide an entry from the prediction, None to leave it to
        the default gate (probability ≥ the validated threshold)."""
        return None


# ── frame building ──────────────────────────────────────────────────────────

_IST_OPEN_S = (9 * 60 + 15) * 60
_EPOCH = pd.Timestamp(0, tz="UTC")


def _secs(values: Any) -> pd.Series:
    """Epoch seconds (int64) of datetimes: join keys free of timezone/unit mismatches."""
    t = pd.to_datetime(pd.Series(values), utc=True)
    return ((t - _EPOCH) // pd.Timedelta(seconds=1)).astype("int64")


def candles_to_df(candles: Sequence[Any]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [c.start for c in candles], "open": [float(c.open) for c in candles],
        "high": [float(c.high) for c in candles], "low": [float(c.low) for c in candles],
        "close": [float(c.close) for c in candles],
        "volume": [float(c.volume) if c.volume is not None else np.nan for c in candles]})


def _resample(df: pd.DataFrame, seconds: int, base: int) -> pd.DataFrame:
    """Complete higher-timeframe bars from base bars, each stamped with its *close* time."""
    if df.empty:
        return df.assign(close_s=pd.Series(dtype="int64"))
    t = pd.to_datetime(df["date"])
    midnight = t.dt.normalize()
    off = (t - midnight).dt.total_seconds()
    anchor = np.where(off >= _IST_OPEN_S, _IST_OPEN_S, 0)
    bucket = midnight + pd.to_timedelta(anchor + ((off - anchor) // seconds) * seconds, unit="s")
    g = df.assign(bucket=bucket).groupby("bucket", sort=True)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum"), n=("close", "size"))
    last_base = _secs(g["date"].max().values).to_numpy()
    out = out.reset_index().rename(columns={"bucket": "date"})
    out["close_s"] = _secs(out["date"]).to_numpy() + seconds
    # Only buckets whose last base bar ends at the bucket's end are complete
    full = last_base + base >= out["close_s"].to_numpy()
    return out[full].drop(columns="n").reset_index(drop=True)


def _features_for(strategy: AdaptiveStrategy, df: pd.DataFrame, symbol: str,
                  corr: bool, base: int) -> pd.DataFrame:
    """All expand features of one symbol, joined onto its base bars (point in time)."""
    s = strategy
    base_df = df[["date"]].copy()
    base_df["bar_close"] = _secs(df["date"]).to_numpy() + base
    for tf in s.timeframes:
        meta = {"symbol": symbol, "tf": tf, "is_correlated": corr}
        tdf = df.copy() if tf == base else _resample(df, tf, base)
        if tdf.empty:
            continue
        cols0 = set(tdf.columns)
        pieces = []
        for p in s.periods:
            got = s.feature_engineering_expand_all(tdf.copy(), p, meta)
            new_cols = [c for c in got.columns if c.startswith("%") and c not in cols0]
            pieces.append(got[new_cols].rename(columns=lambda c: f"{c}-period_{p}"))
        got = s.feature_engineering_expand_basic(tdf.copy(), meta)
        new_cols = [c for c in got.columns if c.startswith("%") and c not in cols0]
        pieces.append(got[new_cols].rename(columns=lambda c: f"{c}-gen"))
        part = pd.concat([tdf.reset_index(drop=True)]
                         + [x.reset_index(drop=True) for x in pieces], axis=1)
        feats = [c for c in part.columns if c.startswith("%")]
        shifted = [part[feats].shift(n).add_suffix(f"_shift-{n}")
                   for n in range(1, s.shifted + 1)]
        part = pd.concat([part, *shifted], axis=1) if shifted else part
        feats = [c for c in part.columns if c.startswith("%")]
        prefix = f"{symbol}|" if corr else ""
        renamed = part[feats].rename(columns={c: f"{prefix}{c}@{tf}s" for c in feats})
        if tf == base:
            renamed["key"] = base_df["bar_close"].to_numpy()
        else:
            renamed["key"] = part["close_s"].to_numpy()
        renamed = renamed.sort_values("key")
        base_df = pd.merge_asof(base_df.sort_values("bar_close"), renamed,
                                left_on="bar_close", right_on="key",
                                direction="backward").drop(columns="key").copy()
    return base_df.drop(columns=["bar_close"])


def build_frame(strategy: AdaptiveStrategy, candles: Sequence[Any], symbol: str = "",
                correlated: Optional[Mapping[str, Sequence[Any]]] = None,
                with_targets: bool = True) -> pd.DataFrame:
    """Features (``%…``) and targets (``&…``) of every base bar of ``candles``."""
    s = strategy
    base = s.timeframes[0]
    df = candles_to_df(candles)
    if df.empty:
        return df
    frame = _features_for(s, df, symbol, False, base).reset_index(drop=True)
    std = s.feature_engineering_standard(df.copy(), {"symbol": symbol, "tf": base,
                                                     "is_correlated": False})
    std_cols = [c for c in std.columns if c.startswith("%") and c not in df.columns]
    if std_cols:
        frame = pd.concat([frame, std[std_cols].reset_index(drop=True).rename(
            columns=lambda c: f"{c}@std")], axis=1).copy()
    for sym in s.correlated:
        cdf = candles_to_df(list((correlated or {}).get(sym) or []))
        if cdf.empty:
            continue
        cf = _features_for(s, cdf, sym, True, base)
        cf = cf.assign(bar_close=_secs(cf["date"]).to_numpy() + base).drop(
            columns="date").sort_values("bar_close")
        frame = frame.assign(bar_close=_secs(frame["date"]).to_numpy() + base)
        frame = pd.merge_asof(frame.sort_values("bar_close"), cf, on="bar_close",
                              direction="backward").drop(columns="bar_close").copy()
    extra = {f"_{col}": df[col].to_numpy() for col in ("open", "high", "low", "close",
                                                         "volume")}
    if with_targets:
        tgt = s.set_targets(df.copy(), {"symbol": symbol, "tf": base, "is_correlated": False})
        extra.update({c: tgt[c].to_numpy() for c in tgt.columns if c.startswith("&")})
    frame = frame.reset_index(drop=True)
    return pd.concat([frame, pd.DataFrame(extra, index=frame.index)], axis=1)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(c for c in frame.columns if c.startswith("%") or "|%" in c)


def target_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(c for c in frame.columns if c.startswith("&"))


def check_lookahead(strategy: AdaptiveStrategy, candles: Sequence[Any], *,
                    correlated: Optional[Mapping[str, Sequence[Any]]] = None,
                    cuts: int = 4, tolerance: float = 1e-9) -> None:
    """Raise ``LookAheadError`` if any feature of a bar changes once later bars exist."""
    n = len(candles)
    if n < 50:
        return
    full = build_frame(strategy, candles, "", correlated, with_targets=False)
    cols = feature_columns(full)
    for k in range(1, cuts + 1):
        cut = int(n * k / (cuts + 1))
        end = candles[cut - 1].start
        corr_cut = {s: [c for c in v if c.start <= end]
                    for s, v in (correlated or {}).items()}
        part = build_frame(strategy, candles[:cut], "", corr_cut, with_targets=False)
        a = part[cols].to_numpy(float)
        b = full[cols].iloc[:cut].to_numpy(float)
        both = ~(np.isnan(a) | np.isnan(b))
        diff = np.where(both, np.abs(a - b), 0.0)
        # a value present with the future and missing without it is also look-ahead
        appeared = np.isnan(a) & ~np.isnan(b)
        bad = (diff > tolerance * np.maximum(1.0, np.abs(b))) | appeared
        if bad.any():
            j = int(np.where(bad.any(axis=0))[0][0])
            raise LookAheadError(
                f"feature {cols[j]!r} changes when later candles are added (cut at bar "
                f"{cut}): it uses future data — fix the strategy before training")


def load_strategy(spec: str = "") -> AdaptiveStrategy:
    """``path/to/file.py:ClassName`` → an instance; ``file.json`` → a no-code spec
    (``skopaq.adaptive.spec``); empty → the default strategy."""
    spec = (spec or "").strip()
    if not spec:
        return AdaptiveStrategy()
    if spec.endswith(".json"):
        import json as _json

        from skopaq.adaptive.spec import SpecStrategy

        return SpecStrategy(_json.loads(Path(spec).expanduser().read_text()))
    path, _, cls_name = spec.rpartition(":")
    if not path or not cls_name:
        raise ValueError("SKOPAQ_ADAPTIVE_STRATEGY must be 'path/to/file.py:ClassName'")
    file = Path(path).expanduser()
    mod_spec = importlib.util.spec_from_file_location(f"skopaq_user_strategy_{file.stem}", file)
    if mod_spec is None or mod_spec.loader is None:
        raise ValueError(f"cannot load {file}")
    module = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(module)
    cls = getattr(module, cls_name, None)
    if cls is None or not (isinstance(cls, type) and issubclass(cls, AdaptiveStrategy)):
        raise ValueError(f"{cls_name} in {file} is not an AdaptiveStrategy subclass")
    return cls()


def last_row_features(strategy: AdaptiveStrategy, candles: Sequence[Any], as_of: Any,
                      correlated: Optional[Mapping[str, Sequence[Any]]] = None
                      ) -> dict[str, float]:
    """Features of the last bar closed at ``as_of`` (live inference)."""
    base = strategy.timeframes[0]
    cut = as_of.timestamp() if as_of is not None else math.inf
    known = [c for c in candles if c.start.timestamp() + base <= cut]
    corr = {s: [c for c in v if c.start.timestamp() + base <= cut]
            for s, v in (correlated or {}).items()}
    if not known:
        return {}
    frame = build_frame(strategy, known[-2000:], "", corr, with_targets=False)
    row = frame.iloc[-1]
    return {c: float(row[c]) if pd.notna(row[c]) else math.nan for c in feature_columns(frame)}
