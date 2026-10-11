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

## More execution safeguards

- **Order lifecycle** (`skopaq/execution/order_lifecycle.py`): 13 explicit states
  (CREATED, VALIDATED, SUBMITTED, ACKNOWLEDGED, PARTIALLY_FILLED, FILLED, CANCEL_PENDING,
  CANCELLED, REJECTED, EXPIRED, FAILED, UNKNOWN, RECONCILIATION_REQUIRED) and the allowed
  transitions between them.
  - Every step is journalled: the executor writes `created`/`validated`; the worker writes
    `submitting`, `placed`, `cancel_sent`, `final`, `uncertain`, `stuck` and `not_placed`.
  - The lifecycle is replayed from the journal. A transition the table does not allow is
    kept, because the broker is the truth, and flagged.
  - The dashboard shows it on Signals & risk.
- **Startup reconciliation** (`startup_reconcile.py`). Before a live process's first BUY:
  - unresolved placements are matched against the order book: one candidate → `reconciled`
    (adopted); none → `reconciled_absent`; several → still blocking, CRITICAL alert;
  - external orders are reported;
  - an unreadable order book keeps entries blocked.
- **Cross-host leases** (`lease.py`, `SKOPAQ_ACCOUNT_LEASE_ENABLED`, Upstash Redis):
  - an entry lease is held inside the host flock;
  - a daemon session lease makes a competing session on another host refuse to start, with
    a CRITICAL alert;
  - if Redis is enabled but unreachable, entries are refused.
- **Open losses count:** unrealized losses (never gains) count toward the daily loss stop.
- **Alerts:** each alert carries an incident id and the action to take:
  - entry blocked by reconciliation, risk state, daily/weekly/monthly lockout, loss streak or
    kill switch;
  - stale data;
  - competing session or a lost session lease;
  - external orders;
  - model drift.
- **Candles:** a quiet instrument's candle is closed by a timer (`CandleBuilder.flush`).
  Duplicate ticks and late ticks for a closed candle are dropped.

## Strategy coverage

- **Scalper:** qualified per setup.
- **F&O engine:** qualified on the premium, with option-specific stop bounds and spread.
- **Swing (AI) pipeline:** BUYs are qualified on daily candles before the executor. The
  LLM's levels are used, else the exit planner's defaults.

All three journal their decision, execution and outcome (swing: decision and execution).
Spread and liquidity limits (`SKOPAQ_SCALP_MAX_SPREAD_PCT`, `SKOPAQ_SCALP_MAX_PARTICIPATION`)
apply when quotes carry bid/ask and bars carry volume.

**Regime-adaptive selection** (`strategy/selector.py`):

1. `skopaq learn-train --regimes` builds a regime → strategy map from held-out trades. A
   strategy is allowed only with at least 30 trades and a 90 % CI of net expectancy above
   zero.
2. The map is used only after an admin approves it, and can be rolled back.
3. With an approved map, a regime that no strategy covers means no trade.
4. Without one, every configured strategy runs (the fallback).

**Model gate:** the champion's probability is recorded on every signal, and the shadow
model's probability too. It decides entries only with `SKOPAQ_*_MODEL_GATE_ENABLED=true`.
LightGBM is an optional candidate (`--model lightgbm`, `pip install .[ml]`); it is stored
as text, never as a pickle.

**Evolution:** `skopaq/backtest/evolve.py` picks RSI parameters on the first 70 % of the
history and adopts them only if they beat the current parameters on the last 30 %. Moves are
limited by `max_parameter_change_per_evolution_pct`.

## Charges reconciliation

`skopaq/finance/reconcile.py` imports the broker's contract-note CSV (INDstocks has no
contract-note API) from the dashboard or with `skopaq charges-reconcile`. It compares it with
the journal's estimates per day and symbol and saves the report. `broker_quote` reads
`GET /margin` charges as *broker-quoted* figures (not confirmed ones).

## Audit trail and shared state

- **Supabase mirror:** migration `005_trading_audit.sql` holds `signal_events`,
  `order_events` (with the lifecycle state) and `model_events`, written best effort by
  `skopaq/db/mirror.py`. It uses a bounded background queue and never delays an order.
- **Shared API state:** `SKOPAQ_API_SHARED_STATE_ENABLED` puts the login lockout and the
  duplicate-order keys in Redis, so they hold for every API replica.

## Gap matrix

| Area | Status |
|---|---|
| Closed-candle entries, uncertain-placement reconcile, no blind re-send, SELL lock, no short sale, kill switch, protective exits under halt | IMPLEMENTED AND VERIFIED (existing suite) |
| Candle patterns, indicators, data quality, timer close / tick dedup, multi-timeframe, candle regime | IMPLEMENTED (unit-tested) |
| Signal qualification + journal for scalper, F&O and swing; spread / liquidity checks | IMPLEMENTED (unit-tested) |
| Sequential entries (process, host, cross-host lease), write-ahead journal, startup reconciliation, 13-state lifecycle | IMPLEMENTED (unit-tested; Redis lease tested with a fake client) |
| Loss streak, restart-seeded cool-down, fail-closed risk state, open losses in the daily stop | IMPLEMENTED (unit-tested) |
| Versioned charges, per-order ledger, contract-note CSV reconciliation, broker-quoted charges | IMPLEMENTED; automatic contract-note download is not possible (no INDstocks API) |
| Learning: dataset, logistic + LightGBM, OOS gates, champion/challenger, drift monitor, regime map, evolution holdout | IMPLEMENTED; model gate on live entries is OFF by default |
| Alerts with incident id and action | IMPLEMENTED |
| Supabase audit mirror (migration 005), shared API state across replicas | IMPLEMENTED (best effort / opt-in) |
| Dashboard: signals, risk capacity, lifecycle, reconciliation, charges, models, regimes, drift, chart overlays, offline banner | IMPLEMENTED; verified in a headless browser (desktop and mobile) |
| Frontend tests | IMPLEMENTED (Vitest + Testing Library; CI job) |

## Readiness

**READY FOR PAPER TRADING.** Live use needs all of the following first:

- paper sessions on real INDstocks data;
- at least one contract-note reconciliation;
- a reviewed change set;
- an explicit, controlled live authorization.
