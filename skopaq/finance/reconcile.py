"""Reconcile Skopaq's estimated charges against the broker's numbers.

Two broker sources, in order of authority:

1. **Contract notes** (``parse_contract_note_csv``): the broker's legal record. INDstocks
   has no contract-note API, so the CSV/spreadsheet export from the broker's back office
   is imported (dashboard → Signals & risk, or ``skopaq charges-reconcile FILE``). The
   column names are matched flexibly (``Order No``/``order_id``, ``Brokerage``, ``STT``,
   ``Exchange Txn``, ``SEBI``, ``Stamp``, ``GST``/``IGST``+``CGST``+``SGST``, ``Total``).
2. **Broker-quoted charges** (``broker_quote``): ``GET /margin``'s ``charges`` for the
   executed quantity and price — the broker's own computation, used when no contract note
   is in yet (labelled ``broker_quoted``, never ``confirmed``).

``reconcile`` compares them with the estimates booked from the signal journal per
(date, symbol), and per order id where both sides have one, and lists the differences
above a tolerance. A reconciled day's report is saved; until then net P&L stays
*estimated*.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from skopaq.finance.charges import Charges

_ALIASES = {
    "order_id": ("order no", "order number", "order id", "order_id", "orderid"),
    "trade_date": ("trade date", "date", "trade_date", "order date"),
    "symbol": ("symbol", "scrip", "security", "instrument", "trading symbol", "scrip name"),
    "side": ("buy/sell", "side", "b/s", "txn type", "transaction type"),
    "qty": ("quantity", "qty", "traded qty"),
    "price": ("price", "rate", "trade price", "avg price"),
    "brokerage": ("brokerage",),
    "stt": ("stt", "stt/ctt", "securities transaction tax"),
    "exchange": ("exchange txn", "exchange transaction charges", "transaction charges",
                 "exchange charges", "exch txn charges", "txn charges"),
    "sebi": ("sebi", "sebi fees", "sebi turnover fees", "sebi charges"),
    "stamp": ("stamp", "stamp duty"),
    "gst": ("gst", "igst", "cgst", "sgst", "utgst"),
    "total": ("total", "total charges", "net charges"),
}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("_", " "))


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip() or 0)
    except ValueError:
        return 0.0


@dataclass
class NoteRow:
    trade_date: Optional[date]
    symbol: str
    order_id: str
    side: str
    qty: float
    price: float
    charges: Charges


def parse_contract_note_csv(text: str) -> list[NoteRow]:
    """Rows of a contract-note CSV export (unknown columns are ignored; several GST
    columns are summed). Raises ValueError when no charge column is recognised."""
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [_norm(h) for h in rows[0]]
    cols: dict[str, list[int]] = {}
    for i, h in enumerate(header):
        for key, names in _ALIASES.items():
            if h in names or (key == "gst" and h.endswith("gst")):
                cols.setdefault(key, []).append(i)
                break
    if not {"brokerage", "stt", "total"} & set(cols):
        raise ValueError("no charge columns recognised (Brokerage / STT / Total)")
    out = []
    for r in rows[1:]:
        def get(key: str) -> str:
            idx = cols.get(key, [])
            return r[idx[0]].strip() if idx and idx[0] < len(r) else ""

        def total(key: str) -> float:
            return sum(_num(r[i]) for i in cols.get(key, []) if i < len(r))

        d = None
        raw = get("trade_date")
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                d = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        ch = Charges(brokerage=total("brokerage"), stt=total("stt"),
                     exchange=total("exchange"), sebi=total("sebi"), stamp=total("stamp"),
                     gst=total("gst"), schedule="contract-note", estimated=False)
        # The note's total is authoritative: what its listed components do not explain
        # is kept as ``other`` (never dropped, never double counted)
        if "total" in cols:
            rest = total("total") - ch.total
            if abs(rest) > 1e-6:
                ch = Charges(**{**ch.__dict__, "other": rest})
        side = get("side").upper()
        out.append(NoteRow(trade_date=d, symbol=get("symbol").upper(),
                           order_id=get("order_id"),
                           side="BUY" if side.startswith("B") else "SELL" if side else "",
                           qty=_num(get("qty")), price=_num(get("price")), charges=ch))
    return out


async def broker_quote(client: Any, *, security_id: str, side: str, qty: int, price: float,
                       product: str, segment: str = "EQUITY",
                       exchange: str = "NSE") -> Charges:
    """The broker's charge computation for an executed order (``GET /margin``)."""
    est = await client.get_margin(security_id=security_id, side=side, quantity=int(qty),
                                  price=float(price), product=product, segment=segment,
                                  exchange=exchange)
    c = est.charges
    g = lambda name: float(getattr(c, name, 0) or 0)  # noqa: E731
    # IPFT is not in the schedule's components: carried in ``exchange`` (both are
    # exchange-levied turnover charges)
    return Charges(brokerage=g("brokerage") or float(est.brokerage or 0), stt=g("stt"),
                   exchange=g("exchange_charges") + g("ipft_charges"),
                   sebi=g("sebi_turn_over_charges"), stamp=g("stamp_duty"),
                   gst=g("gst"), schedule="broker_quoted", estimated=False)


@dataclass
class ReconReport:
    days: list[str] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    broker_total: float = 0.0
    estimated_total: float = 0.0
    unmatched_broker: list[str] = field(default_factory=list)
    unmatched_estimates: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"days": self.days, "groups": self.groups,
                "discrepancies": self.discrepancies,
                "broker_total": round(self.broker_total, 2),
                "estimated_total": round(self.estimated_total, 2),
                "difference": round(self.broker_total - self.estimated_total, 2),
                "unmatched_broker": self.unmatched_broker,
                "unmatched_estimates": self.unmatched_estimates}


def estimates_from_signals(signals: Iterable[dict]) -> dict[tuple[str, str], float]:
    """Estimated charges of closed trades per (date, symbol) from the signal journal."""
    out: dict[tuple[str, str], float] = {}
    for s in signals:
        o = s.get("outcome") or {}
        if o.get("charges") is None:
            continue
        day = str(o.get("closed_at") or s.get("decided_at") or "")[:10]
        key = (day, str(s.get("symbol") or "").upper())
        out[key] = out.get(key, 0.0) + float(o["charges"])
    return out


def reconcile(notes: list[NoteRow], estimates: dict[tuple[str, str], float], *,
              tolerance: float = 1.0) -> ReconReport:
    rep = ReconReport()
    broker: dict[tuple[str, str], float] = {}
    for n in notes:
        day = n.trade_date.isoformat() if n.trade_date else ""
        key = (day, n.symbol)
        broker[key] = broker.get(key, 0.0) + n.charges.total
    rep.days = sorted({k[0] for k in broker} | {k[0] for k in estimates})
    for key in sorted(set(broker) | set(estimates)):
        b, e = broker.get(key), estimates.get(key)
        row = {"date": key[0], "symbol": key[1], "broker": None if b is None else round(b, 2),
               "estimated": None if e is None else round(e, 2)}
        if b is None:
            rep.unmatched_estimates.append(f"{key[0]} {key[1]}")
        elif e is None:
            rep.unmatched_broker.append(f"{key[0]} {key[1]}")
        else:
            diff = b - e
            row["difference"] = round(diff, 2)
            if abs(diff) > tolerance:
                rep.discrepancies.append(f"{key[0]} {key[1]}: broker ₹{b:.2f} vs estimate "
                                         f"₹{e:.2f} ({diff:+.2f})")
        rep.broker_total += b or 0.0
        rep.estimated_total += e or 0.0
        rep.groups.append(row)
    return rep


def reports_dir(config: Any = None) -> Path:
    env = os.environ.get("SKOPAQ_CHARGES_DIR", "").strip()
    return Path(env or "~/.skopaq/charges").expanduser()


def save_report(report: ReconReport, *, by: str, directory: Optional[Path] = None) -> Path:
    d = directory or reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"recon-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps({**report.as_dict(), "by": by,
                                "saved_at": datetime.now().isoformat()}, indent=1))
    return path


def saved_reports(directory: Optional[Path] = None, limit: int = 20) -> list[dict]:
    d = directory or reports_dir()
    out = []
    for p in sorted(d.glob("recon-*.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out
