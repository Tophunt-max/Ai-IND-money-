"""Simulated paper sessions through the real pipeline (no broker, no network, no orders).

Runs ``ScalpEngine`` with the production ``Executor`` → ``EntryGate`` →
``SafetyChecker`` → ``OrderRouter`` → ``PaperEngine`` on a simulated clock, from 09:15 to
the 15:10 flatten, with REST-style quotes from a seeded random-walk price path (several
regimes per day). Qualification, the signal journal, exits and charges are the live
code's. It checks the invariants a session must keep and prints what happened:

* every BUY that reached the paper engine has a QUALIFIED decision in the journal;
* the book is flat at the end (everything flattened before 15:10);
* booked net P&L = gross − charges for every trade;
* no two entries overlapped.

This validates mechanics, not profitability: synthetic random-walk prices have no edge
by construction, so a negative net result is the expected, honest outcome.

    python scripts/paper_session_sim.py --days 3 --seed 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skopaq.broker.models import Quote  # noqa: E402
from skopaq.broker.paper_engine import PaperEngine  # noqa: E402
from skopaq.constants import SafetyRules  # noqa: E402
from skopaq.execution.executor import Executor  # noqa: E402
from skopaq.execution.order_router import OrderRouter  # noqa: E402
from skopaq.execution.safety_checker import SafetyChecker  # noqa: E402
from skopaq.scalping.engine import ScalpEngine  # noqa: E402
from skopaq.strategy.journal import SignalJournal  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOLS = ("SIMA", "SIMB", "SIMC")


def price_path(seed: int, day: int, symbol: str, start: float):
    """Per-second prices from 09:15 for one symbol-day: regime-switching random walk."""
    rng = random.Random(f"{seed}-{day}-{symbol}")
    prices, p, drift = [], start, 0.0
    for s in range(6 * 3600 + 60):
        if s % 1800 == 0:                       # a new regime every 30 minutes
            drift = rng.choice((-1, 0, 0, 1)) * rng.uniform(0.00001, 0.00004)
        p *= math.exp(drift + rng.gauss(0, 0.00012))
        prices.append(round(p, 2))
    return prices


class Wall:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class Client:
    """No history before 09:15; quotes from the paths at the simulated time."""

    def __init__(self, wall, open_at, paths):
        self.wall, self.open_at, self.paths = wall, open_at, paths

    async def get_historical(self, scrip, interval, start_time, end_time):
        return []

    async def get_quotes(self, codes, symbols=None):
        i = max(0, int((self.wall() - self.open_at).total_seconds()))
        return [Quote(symbol=s, ltp=self.paths[s][min(i, len(self.paths[s]) - 1)], volume=0)
                for s in (symbols or [])]


async def one_day(day: int, seed: int, journal_dir: Path) -> dict:
    open_at = datetime(2026, 6, 1, 9, 15, tzinfo=IST) + timedelta(days=day)
    paths = {s: price_path(seed, day, s, 500 + 300 * k) for k, s in enumerate(SYMBOLS)}
    wall = Wall(open_at + timedelta(seconds=1))
    config = SimpleNamespace(
        trading_mode="paper", scalp_symbols=",".join(SYMBOLS),
        scalp_entry_start="09:30", scalp_entry_end="14:45", scalp_flatten_at="15:10",
        scalp_rest_poll_seconds=1, max_sector_concentration_pct=1.0,
        signal_journal_dir=str(journal_dir))
    paper = PaperEngine(initial_capital=1_000_000)
    router = OrderRouter(config, paper)
    rules = SafetyRules(market_hours_only=False, require_stop_loss=False,
                        max_position_pct=1.0)
    safety = SafetyChecker(rules=rules)
    executor = Executor(router, safety)
    active = {"now": 0, "max": 0}
    orig = router.execute

    async def counted(order, signal):
        if order.side.value == "BUY":
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        try:
            return await orig(order, signal)
        finally:
            if order.side.value == "BUY":
                active["now"] -= 1

    router.execute = counted
    buys = []

    async def on_trade(signal, result):
        if signal.action == "BUY":
            buys.append(signal.symbol)

    async def scrip(client, symbol, exchange="NSE"):
        return f"NSE_{symbol}"

    with patch("skopaq.broker.scrip_resolver.resolve_scrip_code", scrip), \
            patch("skopaq.notifications.notify", new=lambda *a, **k: asyncio.sleep(0)):
        engine = ScalpEngine(config, executor, Client(wall, open_at, paths), router,
                             on_trade=on_trade, wall=wall, sleep=wall.sleep,
                             halted=lambda: False)
        report = await engine.run(asyncio.Event())
    open_rows = [p for p in paper.get_positions() if p.quantity > 0]
    return {"report": report, "buys": buys, "open": open_rows, "max_entries": active["max"]}


async def main(days: int, seed: int) -> int:
    journal_dir = Path(tempfile.mkdtemp(prefix="skopaq-sim-signals-"))
    totals = {"trades": 0, "net": 0.0, "qualified": 0, "rejected": 0, "expired": 0}
    problems: list[str] = []
    for d in range(days):
        res = await one_day(d, seed, journal_dir)
        rep = res["report"]
        totals["trades"] += len(rep.trades)
        totals["net"] += rep.net_pnl
        if res["open"]:
            problems.append(f"day {d}: positions left open {[p.symbol for p in res['open']]}")
        if res["max_entries"] > 1:
            problems.append(f"day {d}: {res['max_entries']} entries overlapped")
        print(f"day {d + 1}: {len(rep.trades)} trade(s), net ₹{rep.net_pnl:,.2f}, "
              f"entries {rep.entries}, refused by qualification {rep.skipped_quality}, "
              f"by charges {rep.skipped_cost}, by safety {rep.rejected}")
    rows = SignalJournal(journal_dir).signals()
    for r in rows:
        totals[r["status"].lower()] = totals.get(r["status"].lower(), 0) + 1
    executed = [r for r in rows if r.get("execution") and r["execution"]["status"] == "FILLED"]
    if any(r["status"] != "QUALIFIED" for r in executed):
        problems.append("an executed signal was not QUALIFIED")
    for r in executed:
        o = r.get("outcome")
        if o and abs(o["gross_pnl"] - o["charges"] - o["net_pnl"]) > 0.02:
            problems.append(f"{r['signal_id']}: net != gross - charges")
    reasons: dict[str, int] = {}
    for r in rows:
        for x in r.get("reasons") or []:
            key = re.sub(r"-?[\d.]+%?", "#", x)
            reasons[key] = reasons.get(key, 0) + 1
    print(json.dumps({"days": days, "seed": seed, **{k: round(v, 2) if isinstance(v, float)
                                                     else v for k, v in totals.items()},
                      "signals": len(rows), "executed_with_outcome": sum(
                          1 for r in executed if r.get("outcome")),
                      "rejection_reasons": reasons, "invariant_problems": problems,
                      "journal": str(journal_dir)}, indent=1, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.days, a.seed)))
