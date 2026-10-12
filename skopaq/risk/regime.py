"""Market regime detection using India VIX + NIFTY trend filter.

Classifies the current market into one of four regimes and returns a
position-scaling multiplier.  In high-volatility or crisis regimes the
system automatically reduces position size — or halts trading entirely.

Regime grid::

    VIX < 13       → LOW_VOL    (complacent market, trend-following friendly)
    13 ≤ VIX < 20  → NORMAL     (typical volatility)
    20 ≤ VIX < 30  → HIGH_VOL   (elevated fear, reduce size)
    VIX ≥ 30       → CRISIS     (panic, go to cash)

Trend overlay::

    NIFTY > 200 SMA  → UPTREND
    NIFTY ≤ 200 SMA  → DOWNTREND

Position scale::

    LOW_VOL  + UPTREND   = 1.2  (full gas)
    NORMAL   + UPTREND   = 1.0  (standard)
    NORMAL   + DOWNTREND = 0.7  (cautious)
    HIGH_VOL + any       = 0.5  (half size)
    CRISIS   + any       = 0.0  (no new trades)

Data sources:
    - India VIX: INDstocks index quote/candles (``^INDIAVIX``)
    - NIFTY 50: INDstocks daily candles (``^NSEI``, for the SMA)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── VIX thresholds (empirical, Indian market specific) ──────────────────────

VIX_LOW_THRESHOLD = 13.0
VIX_NORMAL_THRESHOLD = 20.0
VIX_HIGH_THRESHOLD = 30.0


# ── Position scale matrix ───────────────────────────────────────────────────
# Key: (vix_regime, trend) → scale
_SCALE_MATRIX: dict[tuple[str, str], float] = {
    ("LOW_VOL", "UPTREND"): 1.2,
    ("LOW_VOL", "DOWNTREND"): 0.8,
    ("LOW_VOL", "SIDEWAYS"): 1.0,
    ("NORMAL", "UPTREND"): 1.0,
    ("NORMAL", "DOWNTREND"): 0.7,
    ("NORMAL", "SIDEWAYS"): 0.8,
    ("HIGH_VOL", "UPTREND"): 0.5,
    ("HIGH_VOL", "DOWNTREND"): 0.3,
    ("HIGH_VOL", "SIDEWAYS"): 0.4,
    ("CRISIS", "UPTREND"): 0.0,
    ("CRISIS", "DOWNTREND"): 0.0,
    ("CRISIS", "SIDEWAYS"): 0.0,
}


@dataclass
class MarketRegime:
    """Classification of the current market state."""

    label: str  # "LOW_VOL", "NORMAL", "HIGH_VOL", "CRISIS"
    vix: Optional[float]  # India VIX value, or None if unavailable
    trend: str  # "UPTREND", "DOWNTREND", "SIDEWAYS"
    position_scale: float  # Multiplier for position sizing (0.0 – 1.2)

    @property
    def should_trade(self) -> bool:
        """Whether the system should accept new trades."""
        return self.position_scale > 0.0


class RegimeDetector:
    """Classifies the current market regime from India VIX and NIFTY trend.

    This is a stateless classifier — give it data and it returns a regime.
    Data fetching is the caller's responsibility (keeps this class testable).
    """

    def detect(
        self,
        india_vix: Optional[float] = None,
        nifty_price: Optional[float] = None,
        nifty_sma200: Optional[float] = None,
    ) -> MarketRegime:
        """Classify current market regime.

        Args:
            india_vix: Current India VIX level (None = assume NORMAL).
            nifty_price: Current NIFTY 50 spot price.
            nifty_sma200: NIFTY 50 200-day simple moving average.

        Returns:
            MarketRegime with label, VIX, trend, and position_scale.
        """
        # Step 1: Classify VIX regime
        vix_regime = self._classify_vix(india_vix)

        # Step 2: Classify trend
        trend = self._classify_trend(nifty_price, nifty_sma200)

        # Step 3: Look up position scale
        scale = _SCALE_MATRIX.get((vix_regime, trend), 1.0)

        regime = MarketRegime(
            label=vix_regime,
            vix=india_vix,
            trend=trend,
            position_scale=scale,
        )

        logger.info(
            "Market regime: %s + %s → scale=%.1f (VIX=%.1f)",
            vix_regime, trend, scale,
            india_vix if india_vix is not None else -1,
        )

        return regime

    def _classify_vix(self, india_vix: Optional[float]) -> str:
        """Map India VIX to volatility regime."""
        if india_vix is None:
            return "NORMAL"  # Conservative default when data unavailable

        if india_vix < VIX_LOW_THRESHOLD:
            return "LOW_VOL"
        elif india_vix < VIX_NORMAL_THRESHOLD:
            return "NORMAL"
        elif india_vix < VIX_HIGH_THRESHOLD:
            return "HIGH_VOL"
        else:
            return "CRISIS"

    def _classify_trend(
        self,
        nifty_price: Optional[float],
        nifty_sma200: Optional[float],
    ) -> str:
        """Classify NIFTY trend relative to 200 SMA."""
        if nifty_price is None or nifty_sma200 is None or nifty_sma200 <= 0:
            return "SIDEWAYS"  # Insufficient data

        ratio = nifty_price / nifty_sma200

        if ratio > 1.02:
            return "UPTREND"  # 2% above SMA → clear uptrend
        elif ratio < 0.98:
            return "DOWNTREND"  # 2% below SMA → clear downtrend
        else:
            return "SIDEWAYS"  # Within ±2% band


def fetch_regime_data() -> tuple[Optional[float], Optional[float], Optional[float]]:
    """India VIX, NIFTY 50 price and NIFTY 50 SMA200 from INDstocks daily candles.

    Returns:
        (india_vix, nifty_price, nifty_sma200) — any may be None on failure (no token,
        INDstocks unavailable); ``RegimeDetector.detect`` handles missing values.
    """
    from skopaq.broker import live_quotes

    async def fetch() -> tuple[Optional[float], Optional[float], Optional[float]]:
        india_vix = nifty_price = nifty_sma200 = None
        try:
            vix = await live_quotes.get_history("^INDIAVIX", "1mo")
            if vix["candles"]:
                india_vix = float(vix["candles"][-1]["c"])
        except Exception:
            logger.warning("Failed to fetch India VIX from INDstocks", exc_info=True)
        try:
            nifty = await live_quotes.get_history("^NSEI", "1y")
            closes = [float(c["c"]) for c in nifty["candles"] if c.get("c")]
            if closes:
                nifty_price = closes[-1]
                if len(closes) >= 200:
                    nifty_sma200 = sum(closes[-200:]) / 200
                else:
                    nifty_sma200 = sum(closes) / len(closes)
                    logger.info("Using %d-day SMA (< 200 available)", len(closes))
        except Exception:
            logger.warning("Failed to fetch NIFTY 50 from INDstocks", exc_info=True)
        return india_vix, nifty_price, nifty_sma200

    try:
        return live_quotes.run_sync(fetch(), timeout=60)
    except Exception:
        logger.warning("Regime data unavailable", exc_info=True)
        return None, None, None
