"""``SignalGate``: one place every rule engine (scalper, F&O, swing) turns a setup into a
journalled decision.

It builds the point-in-time evidence, adds the champion model's probability (recorded on
every signal; it only decides when ``min_model_prob`` is set) and the shadow model's
(recorded, never deciding), applies the approved regime map, qualifies, journals the
decision and later the execution and outcome, and raises the deduplicated alerts that
matter (stale data blocking entries).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, Sequence

from skopaq.strategy.journal import SignalJournal
from skopaq.strategy.signal import Decision, QualifyRules, SignalCandidate, default_expiry, qualify

logger = logging.getLogger(__name__)


class _Models:
    """The registry's champion and shadow models, loaded once (never raises)."""

    def __init__(self, directory: Any) -> None:
        self.champion = self.shadow = None
        self.champion_version = self.shadow_version = None
        self.champion_threshold: Optional[float] = None
        if not isinstance(directory, str) or not directory:
            return
        try:
            from skopaq.learning.model import load_model
            from skopaq.learning.registry import ModelRegistry

            reg = ModelRegistry(directory)
            for attr, entry in (("champion", reg.champion()), ("shadow", reg.shadow())):
                if entry and entry.get("model"):
                    setattr(self, attr, load_model(entry["model"]))
                    setattr(self, f"{attr}_version", entry["version"])
                    if attr == "champion":
                        self.champion_threshold = (entry.get("params") or {}).get("threshold")
        except Exception:
            logger.warning("Model registry not loaded: signals carry no model probability",
                           exc_info=True)

    def score(self, model, features: dict) -> Optional[float]:
        if model is None:
            return None
        try:
            return model.predict_one(features)
        except Exception:
            return None


class SignalGate:
    def __init__(self, *, journal: Optional[SignalJournal], rules: QualifyRules,
                 model_dir: Any = None, regime_dir: Any = None,
                 model_gate: bool = False, source: str = "live") -> None:
        self.journal = journal
        self.rules = rules
        self.source = source
        self.models = _Models(model_dir)
        self.regime_map = None
        if isinstance(regime_dir, str) and regime_dir:
            try:
                from skopaq.strategy.selector import RegimeMapStore

                self.regime_map = RegimeMapStore(regime_dir).load()
            except Exception:
                logger.warning("Regime map not loaded: every strategy runs", exc_info=True)
        if model_gate and self.models.champion is not None:
            thr = self.models.champion_threshold
            self.rules = QualifyRules(**{**rules.__dict__,
                                         "min_model_prob": float(thr) if thr else 0.5})

    # ── Strategy selection ──────────────────────────────────────────────

    def strategies(self, names: Sequence[str], candles: Sequence[Any],
                   prefix: str = "scalp.") -> tuple[list[str], Optional[str]]:
        """The configured strategies allowed in the current regime (all of them without
        an approved regime map), and the regime trend label used."""
        if self.regime_map is None:
            return list(names), None
        from skopaq.market.regime import classify

        trend = classify(candles).trend
        return self.regime_map.strategies_for(trend, names, prefix), trend

    # ── Decisions ───────────────────────────────────────────────────────

    def decide(self, *, strategy_id: str, symbol: str, product: str, direction: int,
               decided_at: datetime, timeframe_s: int, entry: float, stop: float,
               target: float, qty: int, reason: str, candles: Sequence[Any],
               holding_minutes: Optional[float] = None, htf_s: Optional[int] = None,
               evidence_direction: Optional[int] = None,
               stale_after_s: Optional[float] = None,
               extra: Optional[dict] = None) -> tuple[SignalCandidate, Decision]:
        from skopaq.learning.dataset import features_from_evidence
        from skopaq.strategy.evidence import build_evidence

        try:
            evidence = build_evidence(candles, timeframe_s, decided_at,
                                      direction=evidence_direction or direction,
                                      htf_s=htf_s, source=self.source,
                                      stale_after_s=stale_after_s)
        except Exception as exc:              # evidence must never crash an engine
            logger.warning("Evidence of %s failed: %s", symbol, exc)
            evidence = {"error": str(exc),
                        "data_quality": {"ok": False, "blocking": ["evidence failed"]}}
        evidence.update(extra or {})
        vols = [c.volume for c in list(candles)[-20:] if getattr(c, "volume", None)]
        if vols and "avg_bar_volume" not in evidence:
            evidence["avg_bar_volume"] = sum(vols) / len(vols)
        if self.regime_map is not None:
            trend = (evidence.get("regime") or {}).get("trend")
            evidence["regime_allowed"] = self.regime_map.allowed(strategy_id, trend or "")
            evidence["regime_map"] = self.regime_map.version
        c = SignalCandidate(
            strategy_id=strategy_id, strategy_version="1", symbol=symbol, product=product,
            direction=direction, decided_at=decided_at, timeframe_s=timeframe_s,
            entry=entry, stop=stop, target=target, qty=qty, reason=reason,
            invalidation=f"price beyond {stop:.2f}", holding_minutes=holding_minutes,
            expires_at=default_expiry(decided_at, timeframe_s), evidence=evidence)
        try:
            feats = features_from_evidence(evidence, entry=entry, stop=stop, target=target,
                                           decided_at=decided_at)
        except Exception:
            feats = {}
        c.model_prob = self.models.score(self.models.champion, feats)
        c.model_version = self.models.champion_version
        shadow = self.models.score(self.models.shadow, feats)
        if shadow is not None:
            evidence["shadow_model"] = {"version": self.models.shadow_version,
                                        "prob": round(shadow, 4)}
        d = qualify(c, self.rules, now=decided_at)
        if self.journal is not None:
            self.journal.record_decision(c, d)
        quality = evidence.get("data_quality") or {}
        if not quality.get("ok", True):
            from skopaq.execution.order_alerts import get_alerter

            get_alerter().alert(
                "WARNING", f"data-blocked:{symbol}",
                f"{symbol}: new entries skipped on bad market data "
                f"({'; '.join(quality.get('blocking') or [])}). Action: check the price "
                "feed / INDstocks token; exits are unaffected.", dedup_s=900)
        return c, d

    def executed(self, signal_id: str, result: Any, qty: float) -> None:
        if self.journal is None or not signal_id:
            return
        from skopaq.broker.models import filled_quantity_of, is_unconfirmed

        status = ("FILLED" if result.success else
                  "UNCONFIRMED" if is_unconfirmed(result) else "REFUSED")
        self.journal.record_execution(
            signal_id, status=status,
            filled=float(filled_quantity_of(result, qty)) if result.success else 0,
            fill_price=float(result.fill_price) if result.fill_price else None,
            reason=result.rejection_reason or "")

    def closed(self, signal_id: str, *, exit_price: float, gross: float, charges: float,
               reason: str, closed_at: datetime) -> None:
        if self.journal is None or not signal_id:
            return
        self.journal.record_outcome(signal_id, exit_price=exit_price,
                                    net_pnl=round(gross - charges, 2),
                                    gross_pnl=round(gross, 2), charges=round(charges, 2),
                                    exit_reason=reason, closed_at=closed_at)


def gate_from_config(config: Any, rules: QualifyRules, *, paper: bool,
                     model_gate_attr: str) -> SignalGate:
    """A gate with the config's journal, model registry and regime map (test doubles
    without directory strings get none of them)."""
    journal = None
    if isinstance(getattr(config, "signal_journal_dir", None), str):
        try:
            journal = SignalJournal.from_config(config)
        except Exception:
            logger.warning("Signal journal unavailable", exc_info=True)
    model_dir = getattr(config, "model_dir", None)
    if isinstance(model_dir, str):
        import os

        model_dir = os.environ.get("SKOPAQ_MODEL_DIR", "").strip() or model_dir
    return SignalGate(journal=journal, rules=rules, model_dir=model_dir,
                      regime_dir=model_dir,
                      model_gate=getattr(config, model_gate_attr, False) is True,
                      source="simulated" if paper else "live")
