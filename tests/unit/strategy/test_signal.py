"""Signal qualification (skopaq/strategy/signal.py), evidence and the signal journal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from skopaq.market.candles import Candle
from skopaq.strategy.evidence import build_evidence
from skopaq.strategy.journal import SignalJournal
from skopaq.strategy.signal import (
    EXPIRED,
    QUALIFIED,
    REJECTED,
    QualifyRules,
    SignalCandidate,
    qualify,
)

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 8, 11, 0, tzinfo=IST)


def cand(**over) -> SignalCandidate:
    base = dict(strategy_id="scalp.orb", strategy_version="1", symbol="TCS",
                product="equity_intraday", direction=1, decided_at=NOW, timeframe_s=60,
                entry=1000.0, stop=990.0, target=1030.0, qty=50,
                evidence={"data_quality": {"ok": True, "blocking": []},
                          "regime": {"trend": "trending_up", "label": "trending_up/normal"},
                          "patterns": {"bias": "bullish"}, "htf": {"confirmed": True}})
    base.update(over)
    return SignalCandidate(**base)


def test_a_good_setup_qualifies_with_net_numbers():
    d = qualify(cand(), now=NOW)
    assert d.status == QUALIFIED, d.reasons
    assert d.net_reward < 30 * 50 and d.net_risk > 10 * 50       # costs on both sides
    assert d.est_charges > 0 and d.net_rr > 1.2


def test_stale_or_invalid_data_is_no_trade():
    ev = {"data_quality": {"ok": False, "blocking": ["stale: last bar closed 300s ago"]}}
    d = qualify(cand(evidence=ev), now=NOW)
    assert d.status == REJECTED and "stale" in d.reasons[0]


def test_wrong_side_stops_tight_stops_and_expiry():
    assert qualify(cand(stop=1005.0), now=NOW).status == REJECTED
    assert any("tighter" in r for r in qualify(cand(stop=999.5, target=1001), now=NOW).reasons)
    assert qualify(cand(direction=-1), now=NOW).status == REJECTED    # long levels, short dir
    e = qualify(cand(expires_at=NOW - timedelta(seconds=1)), now=NOW)
    assert e.status == EXPIRED


def test_net_reward_risk_after_costs_can_reject_a_gross_winner():
    # Gross 1.5 R with a 0.3 % stop: charges and slippage leave < 1 R net
    c = cand(entry=1000.0, stop=997.0, target=1004.5, qty=50)
    d = qualify(c, QualifyRules(min_net_rr=1.0, slippage_pct=0.0002), now=NOW)
    assert d.status == REJECTED and any("net reward/risk" in r for r in d.reasons)


def test_optional_regime_htf_and_pattern_filters():
    ev = {"regime": {"trend": "unreliable", "reason": "few bars"},
          "htf": {"confirmed": False, "direction": "down"}, "patterns": {"bias": "conflict"}}
    strict = QualifyRules(require_htf_confirmation=True, require_pattern_agreement=True)
    d = qualify(cand(evidence=ev), strict, now=NOW)
    text = " ".join(d.reasons)
    assert "regime unreliable" in text and "higher timeframe" in text and "conflict" in text
    allowed = QualifyRules(allowed_regimes=("ranging",))
    assert "not in" in " ".join(qualify(cand(), allowed, now=NOW).reasons)
    lenient = QualifyRules(require_reliable_regime=False)
    assert qualify(cand(evidence={"regime": {"trend": "unreliable"}}), lenient,
                   now=NOW).status == QUALIFIED


def test_evidence_uses_only_closed_candles(tmp_path):
    candles = [Candle(NOW - timedelta(minutes=60 - i), 100 + i * 0.1, 100.3 + i * 0.1,
                      99.8 + i * 0.1, 100.2 + i * 0.1, 1000) for i in range(60)]
    ev = build_evidence(candles, 60, NOW, htf_s=300)
    assert ev["data_quality"]["ok"] and ev["last_bar"] < NOW.isoformat()
    assert {"indicators", "patterns", "regime", "htf"} <= set(ev)
    forming = build_evidence(candles, 60, NOW - timedelta(seconds=30))
    assert not forming["data_quality"]["ok"]                 # the last bar was still open


def test_signal_journal_round_trip(tmp_path):
    j = SignalJournal(tmp_path, mode="paper")
    c = cand()
    d = qualify(c, now=NOW)
    assert j.record_decision(c, d)
    j.record_execution(c.signal_id, status="FILLED", filled=50, fill_price=1000.5,
                       day=NOW.date())
    j.record_outcome(c.signal_id, exit_price=1030, net_pnl=1400.0, gross_pnl=1475.0,
                     charges=75.0, exit_reason="target", closed_at=NOW + timedelta(minutes=9))
    rej = cand(stop=1005.0)
    j.record_decision(rej, qualify(rej, now=NOW))
    rows = j.signals()
    assert [r["status"] for r in rows] == [QUALIFIED, REJECTED]
    assert rows[0]["execution"]["status"] == "FILLED" and rows[0]["outcome"]["net_pnl"] == 1400
    assert rows[1]["reasons"] and rows[1]["outcome"] is None
    assert SignalJournal(tmp_path / "nope").signals() == []
