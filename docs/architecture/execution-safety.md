# Execution safety, signals and learning

How a setup becomes a trade, and what stops it. This page also holds the audit's gap matrix
and the current readiness status.

## Pipeline

```
ticks / REST → CandleBuilder (closed bars only)
  → market/quality.check_series      invalid, duplicate, out-of-order, missing, forming, stale
  → market/indicators + patterns     EMA/SMA/RSI/MACD/ADX/ATR/BB/ROC/RelVol, swings, S/R, ORB
  → market/regime + timeframes       trend/range/vol label; higher timeframe from completed buckets
  → strategy (scalping/strategies)   Setup: entry, stop, target, reason
  → strategy/signal.qualify          QUALIFIED / REJECTED / EXPIRED + reasons → signal journal
  → Executor ─ BUY: EntryGate (one entry at a time) + reconciliation blockers
             └ SafetyChecker         kill switch, limits, loss streak, funds, risk state
  → OrderRouter → LiveOrderWorker    write-ahead journal → place → confirm fill / reconcile
  → position management              stop, breakeven, trail, target, time stop, flatten
  → finance/charges + ledger         per-order charges, net P&L, slippage
  → signal journal outcome           → learning dataset → candidate → shadow → champion
```

Indicators, sizing, risk checks, order states and money are deterministic code. An LLM can add
evidence to the swing pipeline, but cannot set prices or skip any check below.

## Signal contract

`skopaq/strategy/signal.py` defines `SignalCandidate`. Its fields:

- ids: signal, strategy and version, model version
- instrument and product, direction, decision time, timeframe
- entry, invalidation, stop and target; holding period
- costs and slippage assumptions, expiry
- evidence: indicators, patterns, regime, data quality, higher timeframe

`qualify` rejects a setup in these cases:

- the data is stale, invalid or still forming;
- the stop is on the wrong side, or too tight or too wide;
- the regime is unreliable (when required), or a required higher-timeframe or pattern
  confirmation is missing;
- **net** reward/risk, after round-trip charges and adverse slippage on both sides, is below
  `SKOPAQ_SCALP_MIN_NET_RR`.

The regime and higher-timeframe filters are **off** by default. No out-of-sample evidence
supports them yet, so they are recorded as evidence only.

`confidence` is a rule-based confirmation score, not a win probability. A model's
calibrated probability is stored separately as `model_prob`.

Every decision goes to `~/.skopaq/signals/YYYY-MM-DD.jsonl` (`SKOPAQ_SIGNAL_JOURNAL_DIR`),
together with its later execution and outcome lines. The dashboard shows them on
**Signals & risk**.

## Sequential entries and reconciliation

`skopaq/execution/entry_gate.py` controls BUYs:

- **One entry at a time.** A BUY holds the gate from the safety check until the broker's
  answer is final. Within a process this is an `asyncio.Lock`. Across processes on one host
  in live mode it is an flock on `<SKOPAQ_ORDER_LOCK_DIR>/entry-account.lock`.
- **Waiting is bounded.** A second entry waits `SKOPAQ_ENTRY_LOCK_WAIT_SECONDS`, then is
  refused.
- **The lock fails closed.** In live mode, an unusable lock directory refuses entries.
- **Unknown outcomes block BUYs:**
  - an uncertain BUY;
  - an own BUY still working;
  - a journalled `submitting` line with no answer. It is written before every placement,
    so a process killed mid-placement leaves evidence.

  An admin resolves these from the dashboard after checking the broker's order book. The
  resolution is audited.
- **SELLs never take the gate.** Protective exits keep running while entries are blocked.

> The lock is per host. Two machines trading one account are **not** coordinated. Run one
> trading host per account.

Other changes in this area:

- **No paper fallback in live mode.** A live order without a live client used to be
  filled on paper (the MCP server). It is now refused.
- **Risk state survives restarts.** Today's losing streak (`SKOPAQ_MAX_CONSECUTIVE_LOSSES`,
  ceiling 4, can only be lowered) and the last loss time (cool-down) are seeded from stored
  trades.
- **Fail-closed risk state.** In live mode, if Supabase is configured but cannot be read,
  new BUYs are refused.
- **Net P&L for loss limits.** The loss limits record P&L after estimated charges.

## Charges and net P&L

`skopaq/finance/charges.py` is one versioned schedule (`nse-indstocks-2024-10`). Each rate
carries its effective date and source. It covers intraday and delivery equity, options and
futures, and computes the net break-even exit.

- GST is levied on brokerage + exchange + SEBI fees only.
- Brokerage is per order, so partial fills are never charged twice. This fixes the
  scalper's partial exits, which used to re-charge the entry brokerage.
- Components the schedule does not model are listed in `excluded`.

`skopaq/finance/ledger.py` builds P&L from confirmed fills only. It covers average prices,
gross/unrealized/net P&L, slippage, broker-confirmed charges and discrepancies.

> **Not yet wired:** contract-note charges are not fetched from the broker. Net P&L is
> labelled *estimated* until they are.

## Learning lifecycle

There are three separate things:

- **Agent memory** is prompt context; it changes no weights.
- **Statistical learning** is `learning/tracker.py` and `report.py`.
- **Model training**:
  1. `dataset.py` builds point-in-time features from journal or backtest evidence. Labels
     are the net outcome; a missing outcome is *unlabelled*, never a loss.
  2. `check_point_in_time` rejects leakage.
  3. The split is chronological with an embargo; walk-forward folds are also run.
  4. `model.py` is a deterministic NumPy logistic baseline.
  5. `evaluation.py` measures, on the unseen test set:
     - net expectancy with a bootstrap CI;
     - profit factor;
     - drawdown;
     - Brier vs base rate and calibration;
     - drift (PSI).
  6. `registry.py` moves a model from candidate → shadow (gates passed) → champion. Promotion
     needs `mandatory_paper_days_for_new_strategy` days in shadow plus a named admin
     approval. Rollback is supported, and history is kept.

`assert_no_safety_keys` refuses any artifact naming a `SafetyRules` field or a hard config
limit.

To train a candidate (no orders; INDstocks token needed for candles):

```bash
skopaq learn-train --symbols RELIANCE,TCS --days 20 --journal
```

> **Not yet wired:** the champion's probability is not used to gate live entries. That stays
> off until a model has passed the gates and the shadow period on real data.

## API hardening

- `/api/chat/*` returns 503 without `SKOPAQ_API_TOKEN` (it used to be open).
- `trade_stock` is never auto-approved in live mode: `/message` refuses it and `/tool`
  returns 403.
- Errors no longer echo exception text.
- `/api/status`, `/api/portfolio` and `/api/scanner/*` need a login. `/api/scanner/candidates`
  drains a queue, so it needs an admin.
- In live mode, `/docs` is off by default. The API also refuses to start without dashboard
  auth, or with `CORS=*`.
- The login lockout trusts only `SKOPAQ_TRUSTED_PROXY_HOPS` entries of `X-Forwarded-For`.
- `POST /control/orders` and `/control/close` accept an `Idempotency-Key` (the dashboard
  sends one). An identical body within 5 s is refused. This state is per process.

## Gap matrix

| Area | Status |
|---|---|
| Closed-candle entries, IST alignment, uncertain-placement reconcile, no blind re-send, SELL lock, no short sale, kill switch, protective exits under halt | IMPLEMENTED AND VERIFIED (existing suite) |
| Candle patterns, MACD/ADX/Bollinger/RelVol/swings/S-R, data-quality gate, multi-timeframe without look-ahead, candle regime | IMPLEMENTED (new, unit-tested) |
| Signal contract + QUALIFIED/REJECTED/EXPIRED + persisted reasons; net R:R after costs | IMPLEMENTED for the scalper; F&O engine and swing pipeline still log only |
| Sequential entries, write-ahead journal, reconciliation blockers, loss streak, restart-seeded cool-down, fail-closed risk state | IMPLEMENTED (new, unit-tested) |
| Live→paper silent fallback; chat fail-open; unauthenticated status/portfolio; spoofable lockout; duplicate order submits | FIXED |
| Versioned charges incl. delivery; per-order ledger; partial-exit double charge | IMPLEMENTED / FIXED; contract-note reconciliation MISSING |
| Model training, OOS gates, champion/challenger, drift, rollback, safety immutability | IMPLEMENTED; model gate on live entries deliberately OFF |
| Cross-host coordination of one account | MISSING (documented: one host per account) |
| F&O: long CE/PE and long futures only; no option writing or short futures | Unchanged; verified by existing tests |
| Startup reconciliation before the scalper/F&O engine start | EXISTS BUT NEEDS FIXING (now mitigated: entries blocked by journal blockers) |
| Shared-state rate limiting / idempotency across API replicas | MISSING (single API process assumed) |

## Readiness

**READY FOR PAPER TRADING.** Live use needs all of the following first:

- paper sessions on real INDstocks data;
- contract-note charge reconciliation;
- a review of this change set;
- an explicit, controlled live authorization.
