"""Contract-note reconciliation, LightGBM candidates, drift monitoring, the evolution
holdout and change limits, shared idempotency and the audit mirror rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from skopaq.finance.reconcile import (
    broker_quote,
    estimates_from_signals,
    parse_contract_note_csv,
    reconcile,
    save_report,
    saved_reports,
)
from skopaq.learning.dataset import FEATURE_NAMES, Example

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=IST)

NOTE = ("Trade Date,Order No,Scrip Name,Buy/Sell,Quantity,Rate,Brokerage,STT,Exchange Txn,"
        "SEBI Fees,Stamp Duty,CGST,SGST\n"
        "08-10-2026,EQ-1,TCS,Buy,10,4000,10,0,1.19,0.04,1.2,0.9,0.9\n"
        "08-10-2026,EQ-2,TCS,Sell,10,4050,10,10.13,1.2,0.04,0,0.9,0.9\n"
        "08-10-2026,EQ-3,INFY,Buy,5,1500,10,0,0.22,0.01,0.23,0.9,0.9\n")


def test_contract_note_parsing_and_reconciliation(tmp_path):
    rows = parse_contract_note_csv(NOTE)
    assert len(rows) == 3 and rows[0].order_id == "EQ-1" and rows[0].side == "BUY"
    assert rows[1].charges.stt == pytest.approx(10.13)
    assert rows[0].charges.gst == pytest.approx(1.8)      # CGST + SGST summed
    est = estimates_from_signals([
        {"symbol": "TCS", "outcome": {"charges": 47.0, "closed_at": "2026-10-08T14:00:00"}},
        {"symbol": "SBIN", "outcome": {"charges": 20.0, "closed_at": "2026-10-08T14:00:00"}},
        {"symbol": "LT", "outcome": None}])
    rep = reconcile(rows, est)
    d = rep.as_dict()
    tcs = next(g for g in d["groups"] if g["symbol"] == "TCS")
    assert tcs["broker"] == pytest.approx(37.40) and tcs["difference"] == pytest.approx(-9.60)
    assert any("TCS" in x for x in d["discrepancies"])
    assert d["unmatched_broker"] == ["2026-10-08 INFY"]
    assert d["unmatched_estimates"] == ["2026-10-08 SBIN"]
    save_report(rep, by="admin", directory=tmp_path)
    assert saved_reports(tmp_path)[0]["by"] == "admin"
    with pytest.raises(ValueError):
        parse_contract_note_csv("a,b\n1,2\n")
    [row] = parse_contract_note_csv("Order No,Brokerage,STT,Total\nEQ-9,10,0,12\n")
    assert row.charges.total == pytest.approx(12) and row.charges.other == pytest.approx(2)


async def test_broker_quoted_charges_come_from_get_margin():
    from skopaq.broker.models import MarginCharges, MarginEstimate

    class Client:
        async def get_margin(self, **kw):
            self.kw = kw
            return MarginEstimate(charges=MarginCharges(
                stt=10, exchange_charges=1.2, stamp_duty=0.6, sebi_turn_over_charges=0.04,
                brokerage=10, gst=2.0, ipft_charges=0.04))
    c = Client()
    ch = await broker_quote(c, security_id="11536", side="SELL", qty=10, price=4050,
                            product="INTRADAY")
    assert ch.schedule == "broker_quoted" and not ch.estimated
    assert ch.total == pytest.approx(10 + 1.24 + 0.6 + 0.04 + 10 + 2.0)
    assert c.kw["quantity"] == 10


def _examples(n=400, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        f = {k: float(rng.normal()) for k in FEATURE_NAMES}
        win = f["rsi"] + rng.normal() * 0.7 > 0
        at = T0 + timedelta(hours=3 * i)
        out.append(Example(at, f, int(win), 120.0 if win else -100.0,
                           closed_at=at + timedelta(minutes=20), symbol="TCS"))
    return out


def test_lightgbm_candidate_is_deterministic_and_round_trips():
    pytest.importorskip("lightgbm")
    from skopaq.learning.model import GBMModel, load_model
    from skopaq.learning.pipeline import train_candidate

    ex = _examples()
    a, b = GBMModel().fit(ex[:300]), GBMModel().fit(ex[:300])
    assert a.fingerprint() == b.fingerprint()
    again = load_model(a.to_dict())
    assert np.allclose(again.predict_proba(ex[300:]), a.predict_proba(ex[300:]))
    entry = train_candidate(ex, strategy_id="scalp.gate", model_kind="lightgbm")
    assert entry["model"]["kind"] == "lightgbm-v1" and entry["report"]["samples"] > 0
    with pytest.raises(ValueError):
        train_candidate(ex, strategy_id="x", model_kind="xgboost-magic")


def test_drift_monitor_compares_champion_training_features_with_recent_signals(tmp_path,
                                                                              monkeypatch):
    from skopaq.learning import monitor
    from skopaq.learning.registry import ModelRegistry

    monkeypatch.setenv("SKOPAQ_MODEL_DIR", str(tmp_path / "m"))
    config = SimpleNamespace(signal_journal_dir=str(tmp_path / "s"), trading_mode="paper")
    assert monitor.drift_now(config)["available"] is False
    reg = ModelRegistry(tmp_path / "m")
    rng = np.random.default_rng(0)
    e = reg.register(model={"kind": "x"}, report={}, params={},
                     dataset={"feature_sample": {"rsi": list(rng.normal(50, 5, 300))}},
                     strategy_id="scalp.gate")
    entry = reg.get(e["version"])
    entry["state"] = "champion"
    reg._write_json(reg._path(e["version"]), entry)
    reg._set_pointer("champion", e["version"])
    recent = [Example(T0, {"rsi": float(x)}, None, None) for x in rng.normal(80, 5, 100)]
    monkeypatch.setattr("skopaq.learning.dataset.from_journal",
                        lambda rows: SimpleNamespace(examples=recent))
    sent = []
    monkeypatch.setattr("skopaq.execution.order_alerts.get_alerter",
                        lambda: SimpleNamespace(alert=lambda *a, **k: sent.append(a)))
    res = monitor.drift_now(config, notify=True)
    assert res["alerts"] == ["rsi"] and sent and "Action:" in sent[0][2]


def test_evolution_picks_on_one_period_and_proves_on_another():
    from skopaq.backtest.evolve import holdout_split, within_change_limit

    df = pd.DataFrame({"Date": range(100), "Close": range(100)})
    sel, ho = holdout_split(df)
    assert len(sel) == 70 and len(ho) == 30
    assert sel["Date"].max() < ho["Date"].min()
    base = {"rsi_buy_threshold": 35, "stop_loss_pct": 3.0, "rsi_period": 14}
    assert within_change_limit(base, {**base, "rsi_buy_threshold": 40}, 0.2)
    assert not within_change_limit(base, {**base, "rsi_buy_threshold": 25}, 0.2)


class FakeShared:
    def __init__(self):
        self.d = {}

    async def claim(self, k):
        if k not in self.d:
            self.d[k] = None
            return True, None
        return False, self.d[k]

    async def store(self, k, v):
        self.d[k] = v

    async def forget(self, k):
        self.d.pop(k, None)


async def test_shared_idempotency_across_replicas():
    from skopaq.api.idempotency import IdempotencyStore

    shared = FakeShared()
    a, b = IdempotencyStore(), IdempotencyStore()          # two API processes
    async with a.guard("u", "order", {"q": 1}, "K", shared=shared) as slot:
        slot.result = {"ok": 1}
    async with b.guard("u", "order", {"q": 1}, "K", shared=shared) as slot:
        assert slot.replay == {"ok": 1}                     # the other replica's answer
    async with a.guard("u", "order", {"q": 2}, None, shared=shared) as slot:
        slot.result = {"ok": 2}
    with pytest.raises(HTTPException):
        async with b.guard("u", "order", {"q": 2}, None, shared=shared):
            pass

    class Down(FakeShared):
        async def claim(self, k):
            raise ConnectionError("redis down")
    with pytest.raises(HTTPException, match="not sent"):
        async with a.guard("u", "order", {"q": 3}, "K3", shared=Down()):
            pass


def test_audit_mirror_rows_carry_the_lifecycle_state():
    from skopaq.db.mirror import mirror_from_config, order_row, signal_row

    row = order_row({"event": "placed", "internal_id": "i", "order_id": "EQ-1", "qty": "10",
                     "status": "QUEUED"})
    assert row["lifecycle_state"] == "ACKNOWLEDGED" and row["qty"] == 10
    assert signal_row({"signal_id": "s", "event": "decision", "status": "REJECTED"})[
        "status"] == "REJECTED"
    assert mirror_from_config(SimpleNamespace(supabase_url="", audit_mirror_enabled=True)) is None
