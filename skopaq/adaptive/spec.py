"""No-code strategies: a user builds and selects their own adaptive strategy from the
dashboard (or a JSON file) without writing or uploading code.

A spec is plain data, validated against fixed lists, and turned into an
``AdaptiveStrategy`` (``SpecStrategy``) whose hooks compute only the indicators named here —
all from past bars (rolling windows and EWMs that look backwards, a session VWAP that
accumulates from the open), so a spec cannot look ahead; ``check_lookahead`` still runs
before it is saved and before every training. Example::

    {"name": "momentum_5m",
     "timeframes": [60, 300], "periods": [10, 20], "shifted": 2,
     "features": ["rsi", "roc", "atr_pct", "rel_vol", "ema_gap", "macd_hist",
                  "vwap_dist", "minute_of_session"],
     "correlated": ["HDFCBANK"],
     "targets": [{"type": "win", "horizon": 15, "min_move_pct": 0.15}],
     "entry": {"type": "probability", "min": 0.6}}

``StrategyStore`` keeps the specs in ``<model_dir>/strategies/`` and which strategy
training uses (``active.json``): the built-in default, the ``.env`` code strategy
(``SKOPAQ_ADAPTIVE_STRATEGY``) or a saved spec. Every change is logged with who made it.
Selecting a strategy changes what the *next* training learns; the trained model is still
only a candidate behind the gates, shadow period and admin approval.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

from skopaq.adaptive.strategy import AdaptiveStrategy

# name: (hook, description) — "period" features are computed per period, per timeframe
FEATURES: dict[str, tuple[str, str]] = {
    "rsi": ("period", "RSI (Wilder)"),
    "roc": ("period", "rate of change over the period"),
    "atr_pct": ("period", "average true range / close"),
    "bb_width": ("period", "Bollinger band width"),
    "sma_dist": ("period", "close / SMA − 1"),
    "ema_dist": ("period", "close / EMA − 1"),
    "zscore": ("period", "(close − SMA) / standard deviation"),
    "rel_vol": ("period", "volume / average volume"),
    "stoch_k": ("period", "stochastic %K"),
    "willr": ("period", "Williams %R"),
    "cci": ("period", "commodity channel index"),
    "pct_change": ("basic", "1-bar return"),
    "ema_gap": ("basic", "(EMA9 − EMA21) / close"),
    "macd_hist": ("basic", "MACD histogram (12/26/9) / close"),
    "body": ("basic", "candle body / range"),
    "close_pos": ("basic", "close position in the bar's range"),
    "vwap_dist": ("basic", "close / session VWAP − 1"),
    "minute_of_session": ("standard", "minutes since 09:15"),
    "day_of_week": ("standard", "0 = Monday"),
}
TARGETS: dict[str, tuple[str, str]] = {
    "win": ("classifier", "1 if a long from the next open gains more than min_move_pct "
                          "after `horizon` bars (charges ≈ 0.12 %)"),
    "forward_return": ("regressor", "return from the next open to the close `horizon` "
                                    "bars later"),
    "max_up": ("regressor", "best high within `horizon` bars vs the next open"),
    "max_down": ("regressor", "worst low within `horizon` bars vs the next open"),
}
ENTRY_TYPES = ("auto", "probability", "zscore", "value")
TIMEFRAMES = (60, 180, 300, 600, 900, 1800, 3600)
_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


class SpecError(ValueError):
    """The spec is not valid (the message says what to fix)."""


def _ints(v: Any, what: str, lo: int, hi: int, max_n: int) -> list[int]:
    if not isinstance(v, (list, tuple)) or not v:
        raise SpecError(f"{what}: a non-empty list")
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or int(x) != x \
                or not lo <= int(x) <= hi:
            raise SpecError(f"{what}: whole numbers {lo}–{hi} (got {x!r})")
        out.append(int(x))
    if len(out) > max_n:
        raise SpecError(f"{what}: at most {max_n}")
    return list(dict.fromkeys(out))


def validate(raw: Mapping[str, Any]) -> dict:
    """The normalised spec, or ``SpecError``."""
    if not isinstance(raw, Mapping):
        raise SpecError("the strategy must be a JSON object")
    unknown = set(raw) - {"name", "description", "timeframes", "periods", "shifted",
                          "features", "correlated", "targets", "entry"}
    if unknown:
        raise SpecError(f"unknown field(s): {', '.join(sorted(unknown))}")
    name = str(raw.get("name", "")).strip()
    if not _NAME.match(name) or name == "default":
        raise SpecError("name: 3–41 characters, lower-case letters, digits and _, "
                        "starting with a letter (not 'default')")
    tfs = _ints(raw.get("timeframes", [60, 300]), "timeframes", 60, 3600, 4)
    if tfs[0] != 60:
        raise SpecError("timeframes: the first (base) timeframe must be 60 s — the scalper "
                        "trades 1-minute candles")
    bad_tf = [t for t in tfs if t not in TIMEFRAMES]
    if bad_tf:
        raise SpecError(f"timeframes: {bad_tf} not in {list(TIMEFRAMES)}")
    periods = _ints(raw.get("periods", [10, 20]), "periods", 2, 200, 4)
    shifted = raw.get("shifted", 2)
    if isinstance(shifted, bool) or not isinstance(shifted, int) or not 0 <= shifted <= 5:
        raise SpecError("shifted: 0–5")
    feats = raw.get("features")
    if not isinstance(feats, (list, tuple)) or not feats:
        raise SpecError("features: pick at least one")
    feats = list(dict.fromkeys(str(f) for f in feats))
    bad = [f for f in feats if f not in FEATURES]
    if bad:
        raise SpecError(f"features: unknown {bad} (choose from {sorted(FEATURES)})")
    corr = [str(s).strip().upper() for s in (raw.get("correlated") or [])]
    if len(corr) > 5 or any(not _SYMBOL.match(s) for s in corr):
        raise SpecError("correlated: up to 5 NSE symbols")
    targets = raw.get("targets") or [{"type": "win", "horizon": 15}]
    if not isinstance(targets, (list, tuple)) or len(targets) > 4:
        raise SpecError("targets: 1–4")
    norm_t, tasks = [], set()
    for t in targets:
        if not isinstance(t, Mapping) or t.get("type") not in TARGETS:
            raise SpecError(f"targets: type must be one of {sorted(TARGETS)}")
        h = t.get("horizon", 15)
        if isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 120:
            raise SpecError("targets: horizon 1–120 bars")
        mm = t.get("min_move_pct", 0.12)
        if isinstance(mm, bool) or not isinstance(mm, (int, float)) or not 0 <= mm <= 10:
            raise SpecError("targets: min_move_pct 0–10")
        norm_t.append({"type": t["type"], "horizon": h, "min_move_pct": float(mm)})
        tasks.add(TARGETS[t["type"]][0])
    if len(tasks) > 1:
        raise SpecError("targets: all classifiers (win) or all regressors, not both")
    if len({(t["type"], t["horizon"]) for t in norm_t}) != len(norm_t):
        raise SpecError("targets: duplicates")
    entry = raw.get("entry") or {"type": "auto"}
    if not isinstance(entry, Mapping) or entry.get("type") not in ENTRY_TYPES:
        raise SpecError(f"entry: type one of {list(ENTRY_TYPES)}")
    e = {"type": entry["type"]}
    if e["type"] != "auto":
        v = entry.get("min")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -100 <= v <= 100:
            raise SpecError("entry: a numeric 'min'")
        if e["type"] == "probability" and (tasks != {"classifier"} or not 0 < v < 1):
            raise SpecError("entry: probability needs a win target and 0 < min < 1")
        if e["type"] in ("zscore", "value") and tasks != {"regressor"}:
            raise SpecError(f"entry: {e['type']} needs a regressor target")
        e["min"] = float(v)
    desc = str(raw.get("description", ""))[:300]
    return {"name": name, "description": desc, "timeframes": tfs, "periods": periods,
            "shifted": shifted, "features": feats, "correlated": corr, "targets": norm_t,
            "entry": e}


def _target_col(t: Mapping[str, Any]) -> str:
    return f"&-{t['type']}_{t['horizon']}"


class SpecStrategy(AdaptiveStrategy):
    """An ``AdaptiveStrategy`` built from a validated spec."""

    def __init__(self, spec: Mapping[str, Any]) -> None:
        self.spec = validate(spec)
        s = self.spec
        self.name = s["name"]
        self.timeframes = tuple(s["timeframes"])
        self.periods = tuple(s["periods"])
        self.shifted = s["shifted"]
        self.correlated = tuple(s["correlated"])
        self.label_period = max(t["horizon"] for t in s["targets"])
        self.model_type = TARGETS[s["targets"][0]["type"]][0]
        self.primary_target = _target_col(s["targets"][0])
        self._f = set(s["features"])

    def _want(self, name: str) -> bool:
        return name in self._f

    def feature_engineering_expand_all(self, df: pd.DataFrame, period: int,
                                       metadata: dict) -> pd.DataFrame:
        c, h, lo, v = df["close"], df["high"], df["low"], df["volume"]
        out: dict[str, pd.Series] = {}
        if self._want("rsi"):
            d = c.diff()
            g = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
            ls = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False,
                                        min_periods=period).mean()
            out["%-rsi"] = 100 - 100 / (1 + g / ls.replace(0, np.nan))
        if self._want("roc"):
            out["%-roc"] = c.pct_change(period)
        if self._want("atr_pct"):
            tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()],
                           axis=1).max(axis=1)
            out["%-atr_pct"] = tr.rolling(period).mean() / c
        mid, sd = c.rolling(period).mean(), c.rolling(period).std(ddof=0)
        if self._want("bb_width"):
            out["%-bb_width"] = 4 * sd / mid
        if self._want("sma_dist"):
            out["%-sma_dist"] = c / mid - 1
        if self._want("zscore"):
            out["%-zscore"] = (c - mid) / sd.replace(0, np.nan)
        if self._want("ema_dist"):
            out["%-ema_dist"] = c / c.ewm(span=period, adjust=False).mean() - 1
        if self._want("rel_vol"):
            out["%-rel_vol"] = v / v.rolling(period).mean()
        hh, ll = h.rolling(period).max(), lo.rolling(period).min()
        rng = (hh - ll).replace(0, np.nan)
        if self._want("stoch_k"):
            out["%-stoch_k"] = (c - ll) / rng * 100
        if self._want("willr"):
            out["%-willr"] = (hh - c) / rng * -100
        if self._want("cci"):
            tp = (h + lo + c) / 3
            md = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
            out["%-cci"] = (tp - tp.rolling(period).mean()) / (0.015 * md.replace(0, np.nan))
        return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1) if out else df

    def feature_engineering_expand_basic(self, df: pd.DataFrame,
                                         metadata: dict) -> pd.DataFrame:
        c, h, lo, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
        out: dict[str, pd.Series] = {}
        if self._want("pct_change"):
            out["%-pct_change"] = c.pct_change()
        if self._want("ema_gap"):
            out["%-ema_gap"] = (c.ewm(span=9, adjust=False).mean()
                                - c.ewm(span=21, adjust=False).mean()) / c
        if self._want("macd_hist"):
            macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
            out["%-macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
        rng = (h - lo).replace(0, np.nan)
        if self._want("body"):
            out["%-body"] = (c - o) / rng
        if self._want("close_pos"):
            out["%-close_pos"] = (c - lo) / rng
        if self._want("vwap_dist"):
            day = pd.to_datetime(df["date"]).dt.date
            tp = (h + lo + c) / 3 * v.fillna(0)
            cum_v = v.fillna(0).groupby(day).cumsum()
            vwap = tp.groupby(day).cumsum() / cum_v.replace(0, np.nan)
            out["%-vwap_dist"] = c / vwap - 1
        return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1) if out else df

    def feature_engineering_standard(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dt = pd.to_datetime(df["date"])
        out = {}
        if self._want("minute_of_session"):
            out["%-minute_of_session"] = dt.dt.hour * 60 + dt.dt.minute - (9 * 60 + 15)
        if self._want("day_of_week"):
            out["%-day_of_week"] = dt.dt.dayofweek
        return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1) if out else df

    def set_targets(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        o_next = df["open"].shift(-1)
        out = {}
        for t in self.spec["targets"]:
            n = t["horizon"]
            if t["type"] in ("win", "forward_return"):
                fwd = df["close"].shift(-n) / o_next - 1
                out[_target_col(t)] = (np.where(fwd.isna(), np.nan,
                                                (fwd > t["min_move_pct"] / 100).astype(float))
                                       if t["type"] == "win" else fwd)
            else:
                col = "high" if t["type"] == "max_up" else "low"
                # bars t+1 … t+n: reverse, roll, reverse back (the label may look ahead)
                win = df[col][::-1].rolling(n, min_periods=n)
                ext = (win.max() if col == "high" else win.min())[::-1].shift(-1)
                out[_target_col(t)] = ext / o_next - 1
        return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1)

    def entry_signal(self, prediction: Mapping[str, Any], metadata: dict) -> Optional[bool]:
        e = self.spec["entry"]
        if e["type"] == "auto":
            return None
        t = (prediction.get("targets") or {}).get(self.primary_target) or {}
        v = t.get("value")
        if v is None:
            return None
        if e["type"] in ("probability", "value"):
            return float(v) >= e["min"]
        mu, sd = t.get("pred_mean"), t.get("pred_std")
        if mu is None or not sd:
            return None
        return (float(v) - mu) / sd >= e["min"]


# ── storage and selection ────────────────────────────────────────────────────


class StrategyStore:
    def __init__(self, model_dir: Path | str) -> None:
        self.dir = Path(model_dir).expanduser() / "strategies"

    def _path(self, name: str) -> Path:
        if not _NAME.match(name):
            raise SpecError("not a strategy name")
        return self.dir / f"{name}.json"

    def _log(self, **event: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        event["at"] = datetime.now(timezone.utc).isoformat()
        with open(self.dir / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")

    def _write(self, path: Path, data: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, path)

    def save(self, raw: Mapping[str, Any], *, by: str, check: bool = True) -> dict:
        spec = validate(raw)
        if check:
            check_spec(spec)
        old = self.get(spec["name"])
        entry = {"spec": spec, "saved_by": by, "saved_at": datetime.now(timezone.utc).isoformat(),
                 "revision": (old or {}).get("revision", 0) + 1}
        self._write(self._path(spec["name"]), entry)
        self._log(event="saved", name=spec["name"], by=by, revision=entry["revision"])
        return entry

    def get(self, name: str) -> Optional[dict]:
        try:
            return json.loads(self._path(name).read_text())
        except (OSError, ValueError, SpecError):
            return None

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json")):
            if p.name == "active.json":
                continue
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                continue
        return out

    def delete(self, name: str, *, by: str) -> None:
        act = self.active()
        if act and act.get("kind") == "spec" and act.get("name") == name:
            raise SpecError(f"{name} is the active strategy: select another one first")
        p = self._path(name)
        if not p.exists():
            raise KeyError(name)
        p.unlink()
        self._log(event="deleted", name=name, by=by)

    def active(self) -> Optional[dict]:
        try:
            return json.loads((self.dir / "active.json").read_text())
        except (OSError, ValueError):
            return None

    def activate(self, choice: str, *, by: str, reason: str = "") -> dict:
        """``default`` | ``env`` | a saved spec's name."""
        choice = (choice or "").strip()
        if choice in ("default", "env"):
            sel = {"kind": choice}
        elif self.get(choice) is not None:
            sel = {"kind": "spec", "name": choice}
        else:
            raise KeyError(choice)
        sel.update(set_by=by, reason=reason[:300],
                   at=datetime.now(timezone.utc).isoformat())
        self._write(self.dir / "active.json", sel)
        self._log(event="activated", by=by, reason=reason[:300],
                  **{k: v for k, v in sel.items() if k in ("kind", "name")})
        return sel

    def history(self, limit: int = 50) -> list[dict]:
        try:
            lines = (self.dir / "history.jsonl").read_text().splitlines()[-limit:]
        except OSError:
            return []
        return [json.loads(x) for x in lines if x.strip()]


def check_spec(spec: Mapping[str, Any]) -> None:
    """Build the spec on a synthetic session and run the look-ahead guard (raises)."""
    import random
    from datetime import timedelta

    from skopaq.adaptive.strategy import check_lookahead
    from skopaq.market.candles import Candle

    ist = timezone(timedelta(hours=5, minutes=30))
    rng = random.Random(11)
    candles, p = [], 1000.0
    for d in range(3):
        t0 = datetime(2026, 1, 5, 9, 15, tzinfo=ist) + timedelta(days=d)
        for i in range(375):
            c = p * (1 + rng.gauss(0, 0.001))
            candles.append(Candle(t0 + timedelta(minutes=i), p, max(p, c) * 1.0003,
                                  min(p, c) * 0.9997, c, 1000 + rng.randint(0, 500)))
            p = c
    s = SpecStrategy(spec)
    corr = {sym: candles for sym in s.correlated}
    check_lookahead(s, candles, correlated=corr, cuts=3)


def resolve(config: Any, *, store: Optional[StrategyStore] = None) -> AdaptiveStrategy:
    """The strategy training uses: the dashboard's selection, else the ``.env`` code
    strategy (``SKOPAQ_ADAPTIVE_STRATEGY``), else the built-in default."""
    from skopaq.adaptive.strategy import load_strategy
    from skopaq.learning.registry import model_dir

    code = str(getattr(config, "adaptive_strategy", "") or "")
    store = store or StrategyStore(model_dir(config))
    sel = store.active() or {}
    if sel.get("kind") == "spec":
        entry = store.get(sel.get("name", ""))
        if entry is None:
            raise SpecError(f"the selected strategy {sel.get('name')!r} no longer exists")
        return SpecStrategy(entry["spec"])
    if sel.get("kind") == "default":
        return AdaptiveStrategy()
    return load_strategy(code)
