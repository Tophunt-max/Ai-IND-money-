"""The signal contract and the trade-qualification step before the risk gateway.

A strategy proposes a ``SignalCandidate`` (entry, stop, target, evidence). ``qualify``
decides ``QUALIFIED`` / ``REJECTED`` / ``EXPIRED`` from deterministic checks and records
every reason, so the signal journal shows why a setup was or was not traded:

1. the data the decision used is valid, closed and fresh (``skopaq.market.quality``);
2. the signal has not expired;
3. stop and target are on the right sides of the entry and the stop is not too tight;
4. the regime is reliable (and, when the strategy restricts it, an allowed regime);
5. the higher timeframe confirms, when the strategy requires it;
6. candle evidence does not contradict the direction (``conflict`` or opposite bias,
   when the strategy requires agreement);
7. **net** reward/risk after estimated round-trip charges and slippage clears the minimum.

Qualification is *not* the risk gateway: a QUALIFIED signal still goes through
``Executor`` → ``EntryGate`` → ``SafetyChecker`` (limits, funds, kill switch), which can
refuse it. A model may add evidence, but nothing here lets it set prices or skip a check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from skopaq.finance.charges import round_trip

QUALIFIED, REJECTED, EXPIRED = "QUALIFIED", "REJECTED", "EXPIRED"
NO_TRADE = "NO_TRADE"

# What "confidence" means on a SignalCandidate: the strategy's rule-based score in
# [0, 1] (share of optional confirmations present), NOT a calibrated win probability.
# The learning module's calibrated model probability is stored separately (model_prob).
CONFIDENCE_MEANING = "rule-based confirmation score in [0,1]; not a win probability"


@dataclass
class SignalCandidate:
    strategy_id: str
    strategy_version: str
    symbol: str
    product: str                    # charge product (skopaq.finance.charges.PRODUCTS)
    direction: int                  # +1 long, -1 short (short: futures only)
    decided_at: datetime
    timeframe_s: int
    entry: float
    stop: float
    target: float
    qty: int = 0
    reason: str = ""
    invalidation: str = ""
    holding_minutes: Optional[float] = None
    expires_at: Optional[datetime] = None
    confidence: Optional[float] = None
    model_version: Optional[str] = None
    model_prob: Optional[float] = None
    evidence: dict[str, Any] = field(default_factory=dict)
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.target - self.entry)


@dataclass(frozen=True)
class QualifyRules:
    min_net_rr: float = 1.2             # net reward / net risk after costs and slippage
    slippage_pct: float = 0.0005        # assumed adverse slippage per side (fraction)
    min_stop_pct: float = 0.001         # a stop closer than 0.1 % is noise
    max_stop_pct: float = 0.05
    require_reliable_regime: bool = True
    allowed_regimes: Optional[tuple[str, ...]] = None   # trend labels; None = any reliable
    require_htf_confirmation: bool = False
    require_pattern_agreement: bool = False
    block_on_data_problems: bool = True
    max_spread_pct: Optional[float] = 0.005     # bid/ask spread over mid (when known)
    max_participation: Optional[float] = 0.10   # qty / average bar volume (when known)
    min_model_prob: Optional[float] = None      # champion model gate (None = off)
    block_model_outliers: bool = False          # refuse when the market is unlike training
    require_model_entry: bool = False           # a bundle's own entry rule must say yes


@dataclass
class Decision:
    status: str
    reasons: list[str]
    passed: list[str]
    net_reward: Optional[float] = None
    net_risk: Optional[float] = None
    net_rr: Optional[float] = None
    est_charges: Optional[float] = None
    est_slippage: Optional[float] = None

    @property
    def qualified(self) -> bool:
        return self.status == QUALIFIED

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 4)  # noqa: E731
        return {"status": self.status, "reasons": self.reasons, "passed": self.passed,
                "net_reward": r(self.net_reward), "net_risk": r(self.net_risk),
                "net_rr": r(self.net_rr), "est_charges": r(self.est_charges),
                "est_slippage": r(self.est_slippage)}


def economics(c: SignalCandidate, qty: int,
              rules: QualifyRules) -> tuple[float, float, float, float]:
    """(net reward ₹, net risk ₹, charges at target ₹, slippage ₹) for ``qty`` units.

    Slippage is assumed adverse on both sides; charges at the stop are added to the risk
    and charges at the target subtracted from the reward."""
    q = max(int(qty), 1)
    slip = rules.slippage_pct * (c.entry + c.target) * q
    slip_stop = rules.slippage_pct * (c.entry + c.stop) * q
    if c.direction > 0:
        cost_t = round_trip(c.product, c.entry, c.target, q).total
        cost_s = round_trip(c.product, c.entry, c.stop, q).total
    else:
        cost_t = round_trip(c.product, c.target, c.entry, q).total
        cost_s = round_trip(c.product, c.stop, c.entry, q).total
    reward = c.reward_per_unit * q - cost_t - slip
    risk = c.risk_per_unit * q + cost_s + slip_stop
    return reward, risk, cost_t, slip


def qualify(c: SignalCandidate, rules: QualifyRules = QualifyRules(), *,
            now: Optional[datetime] = None) -> Decision:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    passed: list[str] = []
    ev = c.evidence

    if c.expires_at is not None and now > c.expires_at:
        return Decision(EXPIRED, [f"expired at {c.expires_at.isoformat()}"], passed)

    quality = ev.get("data_quality") or {}
    if quality and not quality.get("ok", True) and rules.block_on_data_problems:
        reasons.append("data: " + "; ".join(quality.get("blocking") or ["not ok"]))
    elif quality:
        passed.append("data fresh and valid")

    if c.entry <= 0 or c.stop <= 0 or c.target <= 0:
        reasons.append("non-positive entry/stop/target")
    elif c.direction > 0 and not (c.stop < c.entry < c.target):
        reasons.append("long needs stop < entry < target")
    elif c.direction < 0 and not (c.target < c.entry < c.stop):
        reasons.append("short needs target < entry < stop")
    elif c.direction not in (1, -1):
        reasons.append("direction must be +1 or -1")
    else:
        stop_pct = c.risk_per_unit / c.entry
        if stop_pct < rules.min_stop_pct:
            reasons.append(f"stop {stop_pct:.3%} tighter than {rules.min_stop_pct:.3%}")
        elif stop_pct > rules.max_stop_pct:
            reasons.append(f"stop {stop_pct:.2%} wider than {rules.max_stop_pct:.2%}")
        else:
            passed.append(f"stop {stop_pct:.2%} of entry")

    regime = ev.get("regime") or {}
    trend = regime.get("trend")
    if rules.require_reliable_regime and trend == "unreliable":
        reasons.append(f"regime unreliable ({regime.get('reason', '')})")
    elif rules.allowed_regimes is not None and trend is not None \
            and trend not in rules.allowed_regimes:
        reasons.append(f"regime {trend} not in {list(rules.allowed_regimes)}")
    elif trend:
        passed.append(f"regime {regime.get('label', trend)}")

    htf = ev.get("htf") or {}
    if rules.require_htf_confirmation:
        if not htf.get("confirmed"):
            reasons.append(f"higher timeframe {htf.get('direction', 'unknown')} does not "
                           "confirm")
        else:
            passed.append("higher timeframe confirms")

    pats = ev.get("patterns") or {}
    bias = pats.get("bias")
    if rules.require_pattern_agreement and bias:
        want = "bullish" if c.direction > 0 else "bearish"
        if bias == "conflict" or (bias not in ("neutral", want)):
            reasons.append(f"candle evidence {bias} against a {want} entry")
        else:
            passed.append(f"candle evidence {bias}")

    spread = ev.get("spread_pct")
    if rules.max_spread_pct is not None and isinstance(spread, (int, float)):
        if spread > rules.max_spread_pct:
            reasons.append(f"spread {spread:.3%} wider than {rules.max_spread_pct:.3%}")
        else:
            passed.append(f"spread {spread:.3%}")
    avg_vol = ev.get("avg_bar_volume")
    if rules.max_participation is not None and isinstance(avg_vol, (int, float)) and c.qty:
        if avg_vol <= 0:
            reasons.append("no traded volume: illiquid")
        elif c.qty / avg_vol > rules.max_participation:
            reasons.append(f"size {c.qty} is {c.qty / avg_vol:.0%} of an average bar's "
                           f"volume (max {rules.max_participation:.0%})")
        else:
            passed.append(f"size {c.qty / avg_vol:.1%} of bar volume")

    if rules.min_model_prob is not None and c.model_prob is not None:
        if c.model_prob < rules.min_model_prob:
            reasons.append(f"model {c.model_version}: P(net win) {c.model_prob:.2f} < "
                           f"{rules.min_model_prob:.2f}")
        else:
            passed.append(f"model P(net win) {c.model_prob:.2f}")

    adaptive = ev.get("adaptive") or {}
    if rules.require_model_entry and adaptive:
        if adaptive.get("do_predict") == -1:
            reasons.append("model: not enough data for its features")
        elif not adaptive.get("entry"):
            reasons.append(f"model {adaptive.get('version', '')}: no entry "
                           f"({adaptive.get('entry_rule', '')}, score {adaptive.get('score')})")
        else:
            passed.append(f"model entry ({adaptive.get('entry_rule')})")
    if rules.block_model_outliers and adaptive.get("outlier"):
        reasons.append(f"market unlike the model's training data (dissimilarity "
                       f"{adaptive.get('di', 0):.2f} > {adaptive.get('di_threshold', 0):.2f})")

    allowed = ev.get("regime_allowed")
    if allowed is False:
        reasons.append(f"{c.strategy_id} is not validated for regime "
                       f"{regime.get('trend', '?')} (approved regime map)")

    d = Decision(REJECTED if reasons else QUALIFIED, reasons, passed)
    if c.entry > 0 and c.risk_per_unit > 0:
        reward, risk, cost, slip = economics(c, c.qty, rules)
        d.net_reward, d.net_risk, d.est_charges, d.est_slippage = reward, risk, cost, slip
        d.net_rr = reward / risk if risk > 0 else None
        if d.net_rr is None or d.net_rr < rules.min_net_rr:
            d.reasons.append(f"net reward/risk {d.net_rr or 0:.2f} < {rules.min_net_rr:.2f} "
                             "after charges and slippage")
            d.status = REJECTED
        else:
            d.passed.append(f"net reward/risk {d.net_rr:.2f}")
    return d


def default_expiry(decided_at: datetime, timeframe_s: int, bars: int = 1) -> datetime:
    """A candle-close signal is valid until the next bar closes."""
    return decided_at + timedelta(seconds=timeframe_s * bars)
