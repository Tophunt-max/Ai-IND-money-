"""The swing book's Adaptive AI: daily setups, ranked and filtered by the swing model,
traded through every existing gate.

``scan(symbols)`` — the scanner: for each symbol, the setup that fired on the last closed
daily bar (``skopaq.swing.setups``), scored by the swing model (``skopaq.swing.model``),
kept when the model's probability clears its validated threshold, ranked best first.
Without a usable model nothing qualifies unless ``SKOPAQ_SWING_RULES_ONLY`` is on (paper
accounts only), when ideas are ranked by their trend strength instead.

``trade(idea)`` — one entry: ``SignalGate.decide`` (net reward / risk after delivery
charges, data quality, stop bounds; journalled), then a ``TradingSignal`` to the
``Executor`` — ATR position sizing, the entry gate, the safety checker (kill switch,
limits, funds), paper or live — and an exit plan with the setup's R-multiple target.
The monitor then runs the stop, trail and target. Nothing here places an order itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Sequence

from skopaq.finance.charges import EQUITY_DELIVERY
from skopaq.swing.setups import WARMUP, Series, Setup, SwingParams

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

CandleSource = Callable[[str], Awaitable[Sequence[Any]]]


@dataclass
class SwingIdea:
    symbol: str
    setup: Setup
    prob: Optional[float]
    score: float
    features: dict
    candles: list = field(repr=False, default_factory=list)

    @property
    def reason(self) -> str:
        p = f" · P(win) {self.prob:.2f}" if self.prob is not None else ""
        return f"{self.setup.name}: {self.setup.reason}{p}"

    def as_dict(self) -> dict:
        s = self.setup
        return {
            "symbol": self.symbol,
            "setup": s.name,
            "reason": self.reason,
            "entry": s.entry,
            "stop": s.stop,
            "target": s.target,
            "prob": self.prob,
            "score": round(self.score, 4),
            "features": {k: round(v, 4) for k, v in self.features.items()},
        }


def params_from(config: Any) -> SwingParams:
    g = lambda n, d: getattr(config, n, d)  # noqa: E731
    return SwingParams(
        atr_mult=float(g("atr_multiplier", 2.0) or 2.0),
        rr=float(g("swing_target_rr", 2.0) or 2.0),
        slippage_pct=float(g("swing_slippage_pct", 0.001) or 0.001),
        notional_inr=float(g("swing_notional_inr", 100_000.0) or 100_000.0),
    )


class SwingEngine:
    def __init__(
        self,
        config: Any,
        *,
        paper: bool,
        model=None,
        model_state: str = "none",
        gate=None,
        candles: Optional[CandleSource] = None,
        params: Optional[SwingParams] = None,
    ) -> None:
        self.config, self.paper = config, paper
        self.model, self.model_state = model, model_state
        self.gate = gate
        self.params = params or params_from(config)
        if candles is None:
            from skopaq.swing.data import daily_candles

            candles = daily_candles
        self._candles = candles
        self.rules_only = paper and getattr(config, "swing_rules_only", False) is True

    @classmethod
    def from_config(cls, config: Any, *, paper: bool) -> "SwingEngine":
        from skopaq.strategy.gate import SignalGate
        from skopaq.strategy.journal import SignalJournal
        from skopaq.strategy.signal import QualifyRules
        from skopaq.swing.model import load_active

        model, state = load_active(config, paper=paper)
        g = lambda n, d: getattr(config, n, d)  # noqa: E731
        gate = None
        if g("swing_qualification_enabled", True) is not False:
            rules = QualifyRules(
                require_reliable_regime=False,
                min_net_rr=float(g("swing_min_net_rr", 1.0) or 1.0),
                slippage_pct=float(g("swing_slippage_pct", 0.001) or 0.001),
                min_stop_pct=0.005,
                max_stop_pct=0.15,
            )
            journal = None
            if isinstance(g("signal_journal_dir", None), str):
                try:
                    journal = SignalJournal.from_config(config)
                except Exception:
                    logger.warning("Signal journal unavailable", exc_info=True)
            # The swing model decides here; the intraday registry's models do not score
            # daily setups
            gate = SignalGate(
                journal=journal,
                rules=rules,
                model_dir=None,
                regime_dir=None,
                model_gate=False,
                source="simulated" if paper else "live",
            )
        return cls(config, paper=paper, model=model, model_state=state, gate=gate)

    # ── scan ────────────────────────────────────────────────────────────
    async def idea(self, symbol: str) -> Optional[SwingIdea]:
        try:
            candles = list(await self._candles(symbol))
        except Exception as exc:
            logger.info("Swing: no daily candles for %s (%s)", symbol, exc)
            return None
        if len(candles) <= WARMUP:
            return None
        s = Series(candles)
        i = len(s) - 1
        st = s.setup(i, self.params)
        if st is None:
            return None
        feats = s.features(i)
        prob = self.model.prob(feats, st.name) if self.model is not None else None
        trend = feats.get("dist_sma200", 0.0) + feats.get("sma50_slope", 0.0)
        score = prob if prob is not None else trend
        return SwingIdea(symbol, st, prob, float(score), feats, candles)

    async def scan(
        self, symbols: Sequence[str], limit: int = 5, *, concurrency: int = 5
    ) -> list[SwingIdea]:
        """Today's qualifying ideas, best first (at most ``limit``)."""
        if self.model is None and not self.rules_only:
            logger.info(
                "Swing: no %s swing model — no swing entries (train one with `skopaq swing-train`)",
                "champion or shadow" if self.paper else "champion",
            )
            return []
        sem = asyncio.Semaphore(concurrency)

        async def one(sym: str):
            async with sem:
                return await self.idea(sym)

        ideas = [x for x in await asyncio.gather(*(one(s) for s in symbols)) if x is not None]
        if self.model is not None:
            ideas = [x for x in ideas if x.prob is not None and x.prob >= self.model.threshold]
        ideas.sort(key=lambda x: x.score, reverse=True)
        return ideas[:limit]

    # ── trade ───────────────────────────────────────────────────────────
    async def trade(
        self,
        idea: SwingIdea,
        executor: Any,
        *,
        trade_date: str = "",
        regime_scale: float = 1.0,
        calendar_scale: float = 1.0,
    ):
        """Qualify and execute one idea; returns a ``TradeResult`` (never raises)."""
        from skopaq.broker.models import Exchange, ExecutionResult, TradingSignal
        from skopaq.execution.trade_booking import TradeResult

        st = idea.setup
        now = datetime.now(IST)
        confidence = int(round((idea.prob if idea.prob is not None else 0.6) * 100))
        signal = TradingSignal(
            symbol=idea.symbol,
            exchange=Exchange.NSE,
            action="BUY",
            confidence=max(0, min(100, confidence)),
            stop_loss=st.stop,
            reasoning=f"SWING {idea.reason}",
            agent_state={"target_rr": self.params.rr, "setup": st.name},
        )
        evidence = {
            "setup": st.name,
            "prob": idea.prob,
            "model": getattr(self.model, "version", None),
            "model_state": self.model_state,
            "threshold": getattr(self.model, "threshold", None),
        }
        result = TradeResult(
            symbol=idea.symbol, trade_date=trade_date, signal=signal, evidence=evidence
        )
        signal_id = ""
        if self.gate is not None:
            qty = max(1, int(self.params.notional_inr // st.entry))
            try:
                candidate, decision = self.gate.decide(
                    strategy_id=f"swing.{st.name}",
                    symbol=idea.symbol,
                    product=EQUITY_DELIVERY,
                    direction=1,
                    decided_at=now,
                    timeframe_s=86400,
                    entry=st.entry,
                    stop=st.stop,
                    target=st.target,
                    qty=qty,
                    reason=idea.reason[:300],
                    candles=idea.candles,
                    stale_after_s=4 * 86400,
                    extra={"swing_model": evidence},
                )
            except Exception as exc:
                logger.exception("Swing: qualification of %s failed", idea.symbol)
                result.error = f"qualification failed: {exc}"
                return result
            if not decision.qualified:
                result.execution = ExecutionResult(
                    success=False,
                    signal=signal,
                    mode="paper" if self.paper else "live",
                    safety_passed=False,
                    rejection_reason="Not qualified: " + "; ".join(decision.reasons),
                )
                return result
            signal_id = candidate.signal_id
            signal.agent_state["signal_id"] = signal_id
            evidence["signal_id"] = signal_id
        try:
            result.execution = await executor.execute_signal(
                signal,
                trade_date=trade_date or None,
                regime_scale=regime_scale,
                calendar_scale=calendar_scale,
            )
        except Exception as exc:
            logger.exception("Swing: execution of %s failed", idea.symbol)
            result.error = f"execution failed: {exc}"
            return result
        if self.gate is not None and signal_id:
            try:
                self.gate.executed(signal_id, result.execution, int(signal.quantity or 0))
            except Exception:
                logger.debug("Swing journal not updated", exc_info=True)
        return result
