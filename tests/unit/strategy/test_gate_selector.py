"""SignalGate (spread, liquidity, model and regime gates), regime-adaptive selection, the
candle builder's timer close and dedup, swing qualification and F&O journaling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from skopaq.learning.dataset import FEATURE_NAMES, Example
from skopaq.learning.model import LogisticModel
from skopaq.learning.registry import ModelRegistry
from skopaq.market.candles import Candle, CandleBuilder
from skopaq.strategy.gate import SignalGate
from skopaq.strategy.journal import SignalJournal
from skopaq.strategy.selector import RegimeMapStore, build_regime_map
from skopaq.strategy.signal import QualifyRules

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 8, 11, 0, tzinfo=IST)


def bars(n=60, step=0.1, vol=1000):
    return [Candle(NOW - timedelta(minutes=n - i), 100 + i * step, 100.3 + i * step,
                   99.8 + i * step, 100.2 + i * step, vol) for i in range(n)]


def decide(gate, **over):
    kw = dict(strategy_id="scalp.orb", symbol="TCS", product="equity_intraday", direction=1,
              decided_at=NOW, timeframe_s=60, entry=106.0, stop=105.0, target=109.0, qty=100,
              reason="test", candles=bars())
    kw.update(over)
    return gate.decide(**kw)


def test_spread_and_participation_limits(tmp_path):
    gate = SignalGate(journal=SignalJournal(tmp_path), rules=QualifyRules(
        require_reliable_regime=False, max_spread_pct=0.003, max_participation=0.1))
    c, d = decide(gate)
    assert d.qualified, d.reasons
    _, d = decide(gate, extra={"spread_pct": 0.01})
    assert any("spread" in r for r in d.reasons)
    _, d = decide(gate, qty=500)                  # 500 vs 1000 average bar volume
    assert any("average bar's volume" in r for r in d.reasons)
    assert len(SignalJournal(tmp_path).signals()) == 3


def _champion(tmp_path, *, threshold=0.5):
    rng = np.random.default_rng(0)
    ex = []
    for i in range(300):
        f = {n: float(rng.normal()) for n in FEATURE_NAMES}
        ex.append(Example(NOW + timedelta(hours=i), f, int(f["stop_pct"] < 0), 1.0))
    m = LogisticModel().fit(ex)
    reg = ModelRegistry(tmp_path)
    clock = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    reg._clock = lambda: clock["t"]
    e = reg.register(model=m.to_dict(), report={}, params={"threshold": threshold},
                     dataset={}, strategy_id="scalp.gate")
    entry = reg.get(e["version"])
    entry.update(state="shadow", shadow_since=clock["t"].isoformat())
    reg._write_json(reg._path(e["version"]), entry)
    clock["t"] += timedelta(days=8)
    reg.promote(e["version"], approved_by="admin", reason="test")
    return e["version"]


def test_model_probability_is_recorded_and_only_gates_when_enabled(tmp_path):
    version = _champion(tmp_path / "models", threshold=0.99)
    rules = QualifyRules(require_reliable_regime=False)
    recorded = SignalGate(journal=None, rules=rules, model_dir=str(tmp_path / "models"))
    c, d = decide(recorded)
    assert c.model_version == version and 0 <= c.model_prob <= 1 and d.qualified
    gated = SignalGate(journal=None, rules=rules, model_dir=str(tmp_path / "models"),
                       model_gate=True)
    _, d = decide(gated)
    assert not d.qualified and any("P(net win)" in r for r in d.reasons)


def test_regime_map_selection_needs_approval_and_can_idle(tmp_path):
    ex = [SimpleNamespace(net_pnl=p, strategy_id="scalp.orb", meta={"regime": "trending_up"})
          for p in [50, 40, -10, 60, 30] * 8]
    ex += [SimpleNamespace(net_pnl=p, strategy_id="scalp.range_reversal",
                           meta={"regime": "trending_up"}) for p in [-20, 10, -30] * 12]
    m = build_regime_map(ex, min_trades=30)
    assert m["regimes"]["trending_up"]["scalp.orb"]["allowed"]
    assert not m["regimes"]["trending_up"]["scalp.range_reversal"]["allowed"]
    store = RegimeMapStore(tmp_path)
    store.save_candidate(m, dataset={})
    rules = QualifyRules(require_reliable_regime=False)
    assert SignalGate(journal=None, rules=rules, regime_dir=str(tmp_path)).regime_map is None
    with pytest.raises(ValueError):
        store.approve(by=" ", reason="x")
    store.approve(by="admin", reason="validated")
    gate = SignalGate(journal=None, rules=rules, regime_dir=str(tmp_path))
    up = bars(120, step=0.5)
    names, trend = gate.strategies(["orb", "range_reversal"], up)
    assert trend == "trending_up" and names == ["orb"]
    rng = [Candle(NOW - timedelta(minutes=120 - i), 100, 100.7, 99.3,
                  100.4 if i % 2 else 99.6, 1000) for i in range(120)]
    names, trend = gate.strategies(["orb"], rng)
    assert trend == "ranging" and names == []            # no validated strategy: idle
    _, d = decide(gate, strategy_id="scalp.range_reversal", candles=up)
    assert any("not validated for regime" in r for r in d.reasons)
    assert store.rollback() is None and store.load() is None


def test_candle_builder_closes_on_time_and_drops_duplicates_and_late_ticks():
    closed = []
    b = CandleBuilder(60, on_close=closed.append)
    t0 = NOW.replace(minute=0)
    b.add(100.0, t0 + timedelta(seconds=5), 1000)
    b.add(100.0, t0 + timedelta(seconds=5), 1000)        # the same tick twice
    assert b.dropped == 1 and b.current.ticks == 1
    assert b.flush(t0 + timedelta(seconds=61)) is None    # inside the grace period
    c = b.flush(t0 + timedelta(seconds=63))
    assert c is not None and closed == [c] and b.current is None
    b.add(101.0, t0 + timedelta(seconds=50))              # late tick of the closed candle
    assert b.dropped == 2 and b.current is None
    b.add(101.0, t0 + timedelta(seconds=70))
    assert b.current.start == t0 + timedelta(seconds=60)


async def test_swing_buys_are_qualified_before_the_executor(tmp_path):
    from unittest.mock import AsyncMock

    from skopaq.broker.models import TradingSignal
    from skopaq.graph.skopaq_graph import SkopaqTradingGraph

    executor = SimpleNamespace(execute_signal=AsyncMock(), _router=SimpleNamespace(mode="paper"))
    graph = SkopaqTradingGraph({}, executor)
    gate = SignalGate(journal=SignalJournal(tmp_path),
                      rules=QualifyRules(require_reliable_regime=False, min_stop_pct=0.005,
                                         max_stop_pct=0.15))
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    daily = [Candle(today - timedelta(days=80 - i), 100 + i, 102 + i, 99 + i, 101 + i, 10_000)
             for i in range(80)]

    async def candles(symbol):
        return daily
    graph.set_signal_gate(gate, candles)
    sig = TradingSignal(symbol="TCS", action="BUY", confidence=80, entry_price=180.0)
    assert await graph._qualify_swing(sig) is None             # 4 % stop, 2 R target
    assert sig.agent_state["signal_id"]

    async def none(symbol):
        return []
    graph.set_signal_gate(gate, none)
    refused = await graph._qualify_swing(TradingSignal(symbol="TCS", action="BUY",
                                                       confidence=80, entry_price=180.0))
    assert not refused.success and "Not qualified" in refused.rejection_reason
    assert [r["status"] for r in SignalJournal(tmp_path).signals()] == ["QUALIFIED", "REJECTED"]


def test_fno_settings_carry_option_specific_rules():
    from skopaq.scalping.fno_rules import FnoSettings

    s = FnoSettings.from_config(SimpleNamespace(fno_min_net_rr=1.5))
    r = s.qualify_rules
    assert s.qualify and r.min_net_rr == 1.5 and r.max_stop_pct >= 0.25
    assert r.max_participation is None
    assert not FnoSettings.from_config(SimpleNamespace(fno_qualification_enabled=False)).qualify
