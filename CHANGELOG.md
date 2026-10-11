# Changelog

All notable changes to SkopaqTrader. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break things).

## [Unreleased]

### Security

- `/api/chat/*` is disabled (503) without `SKOPAQ_API_TOKEN` (it was open), and never
  auto-approves `trade_stock` in live mode; error responses no longer echo exceptions.
- `/api/status`, `/api/portfolio`, `/api/scanner/*` need a dashboard login (the scanner's
  queue-draining route an admin). Live mode: `/docs` off by default, and the API refuses to
  start without dashboard auth or with `SKOPAQ_CORS_ORIGINS=*`.
- The failed-login lockout trusts only `SKOPAQ_TRUSTED_PROXY_HOPS` entries of
  `X-Forwarded-For` (the left-most entry was client-controlled).
- Manual orders and closes take an `Idempotency-Key`; a duplicate body within 5 s is refused.

### Changed

- **Live mode never fills on paper:** an order without a live broker client is refused
  (the MCP server's `place_order` used to report a paper fill in live mode).
- **One entry at a time** (`skopaq/execution/entry_gate.py`) across engines and processes on
  the host; BUYs wait for unreconciled placements. A write-ahead `submitting` journal line
  precedes every placement. Exits are unaffected.
- Loss limits record P&L **net** of estimated charges; a losing streak
  (`SKOPAQ_MAX_CONSECUTIVE_LOSSES`, ceiling 4) stops BUYs; the streak and cool-down survive
  restarts; live mode fails closed when stored P&L cannot be read.
- The scalper qualifies each setup (data quality, net reward/risk after charges and
  slippage) and journals every candidate with its evidence and reasons; the partial-exit
  charge double-count is fixed. Setups whose net R:R is below 1.0 are no longer traded.

### Added

- `skopaq/market/`: indicators, candlestick patterns, data quality, multi-timeframe
  resampling without look-ahead, candle regime. `skopaq/finance/`: versioned charge
  schedule (intraday, delivery, options, futures) and an execution ledger.
- `skopaq/strategy/`: signal contract, qualification, evidence, signal journal.
- Learning lifecycle: point-in-time dataset, NumPy logistic baseline, out-of-sample gates,
  champion/challenger registry with shadow period, human approval and rollback;
  `skopaq learn-train`.
- Dashboard: **Signals & risk** page (signal log, reconciliation, risk limits), technical
  analysis panel on **Market**, model registry on **AI learning**.
- `scripts/paper_session_sim.py`: simulated paper sessions through the real pipeline.
- Docs: `docs/architecture/execution-safety.md`.
- 13-state order lifecycle replayed from the journal; startup reconciliation before a
  process's first BUY (adopts / clears journalled placements, reports external orders);
  cross-host entry and session leases (Upstash Redis, `SKOPAQ_ACCOUNT_LEASE_ENABLED`).
- F&O and swing (AI) BUYs qualified and journalled like scalps; spread / liquidity limits;
  champion / shadow model probabilities on every signal (gate opt-in); approved
  regime → strategy map with idle-when-uncovered; LightGBM candidates; drift monitor.
- Contract-note CSV charge reconciliation (dashboard / `skopaq charges-reconcile`);
  broker-quoted charges from `GET /margin`; F&O partial-exit charge double count fixed.
- Open losses count toward the daily loss stop; entry-block, stale-data, competing-session,
  external-order and drift alerts with an incident id and the action to take.
- Candle builder closes quiet candles by timer and drops duplicate / late ticks.
- `evolve.py`: parameters chosen on 70 % of the history, adopted only if they beat the
  current ones on the held-out 30 %, within the per-evolution change limit.
- Supabase migration `005_trading_audit.sql` and a best-effort journal mirror; shared API
  state across replicas (`SKOPAQ_API_SHARED_STATE_ENABLED`).
- **Adaptive AI, FreqAI-style** (`skopaq/adaptive/`, native, no Freqtrade code): automatic
  multi-timeframe / shifted / correlated feature expansion, Dissimilarity Index outlier
  detection and training-set filtering, LightGBM / XGBoost / CatBoost candidates,
  cost-net forward targets, rolling backtests that emulate periodic retraining, automatic
  retraining after sessions into candidate / shadow (never champion), Optuna hyperopt of
  the scalper's parameters with a holdout and admin approval, an offline RL research
  module; `skopaq adaptive-train`, `skopaq hyperopt`, `skopaq rl-research`; the
  Adaptive AI card on the dashboard. Look-ahead guard: candles after the decision are
  dropped whatever the caller passes.
- **Adaptive framework**: your own strategy (FreqAI-style feature / target hooks,
  regressors, custom entry rule) with a look-ahead guard that refuses future-peeking
  features; data pipeline with scaler choice, PCA, one-class SVM, Dissimilarity Index,
  DBSCAN and noise (all JSON, no pickle); recency `weight_factor`; models LightGBM,
  XGBoost (+RF), CatBoost, random forest, extra trees, linear, PyTorch MLP and a tabular
  transformer, multi-target, early stopping; rich predictions (per-target value, label and
  live prediction mean/std, z-score, `do_predict`, DI and detector flags, entry rule,
  model age, top features); verified bundles (SHA-256 manifest, library versions,
  export / import / verify, retention); `skopaq model-export|import|verify`.
- **RL agents (PPO, A2C, DQN)** in native PyTorch, trained in a replay of the scalper's own
  rules net of charges and tested on later unseen days; policies saved as safetensors with
  a SHA-256 manifest in a separate RL registry (candidate → shadow → admin-approved
  champion, rollback). A policy trades as the `rl_policy` scalp strategy behind every
  gate: the shadow policy on paper, live only as the champion with
  `SKOPAQ_RL_ALLOW_LIVE` (`.env` only). `skopaq rl-train|rl-status|rl-promote|rl-rollback`,
  dashboard card with promote / rollback.
- **Continual learning** (`SKOPAQ_ADAPTIVE_CONTINUAL_LEARNING`, `SKOPAQ_RL_CONTINUAL_LEARNING`,
  `--continual`): a run continues the previous model of the same strategy and kind —
  boosted trees added to the previous ensemble, PyTorch / logistic from the previous
  weights, the parent's pipeline kept frozen, the rolling backtest chained; RL policies
  from the previous weights and scaling. Lineage recorded in the bundle and registry.
- **Dashboard redesign, trading-terminal style and phone first:** exchange green / red
  theme with monospace prices; a live NIFTY / BANKNIFTY / VIX ticker and kill-switch pill on
  every page; phone tabs Home · Market · Trade · Positions · More (an app grid of every
  page). Market is a watchlist (NIFTY 50 or your own list, sort by gainers / losers) with a
  stock view and BUY / SELL buttons that prefill the order ticket. Charts: candles with
  zoom, pan and pinch, an RSI pane and a crosshair price; equity curves with drawdown;
  daily P&L, per-symbol and per-trade bars; Monte Carlo histogram; win / loss and signal
  donuts; option OI profile with PCR and max pain. Also: real option expiries, the
  scanner's live queue (admin) and the backend's public health on the login screen.
- **Your strategy without code:** a dashboard builder (features, timeframes, periods,
  correlated symbols, targets, entry rule) validated on the server with the look-ahead
  guard, saved per revision, and selected for training with an audit trail; bundles
  rebuild their spec on load; `adaptive-train --strategy <name|spec.json>`.
- Dashboard: order lifecycle, charges reconciliation, risk capacity on the home page,
  regime map and drift on AI learning, chart studies (EMA, patterns, signal levels), an
  offline banner; Vitest + Testing Library tests and a CI job for the dashboard.

### Removed

- **Kite Connect (Zerodha).** INDstocks is now the only broker. Gone:
  - `skopaq/broker/kite_client.py`, `skopaq/options/gtt.py`, `skopaq/trading/` and the
    `kiteconnect` dependency.
  - The `/api/kite/*` routes.
  - The `SKOPAQ_KITE_*`, `SKOPAQ_PUBLIC_BASE_URL` and `SKOPAQ_API_BASE_URL` settings (old
    values in `.env` are ignored).
  - The dashboard's Kite card, Kite portfolio, mutual funds and GTT views.
  - Telegram `/login`.
  - Eleven MCP tools: `place_gtt_order`, `list_gtt_orders`, `setup_swing_trade`,
    `place_amo_order`, `place_bracket`, `place_cover`, `place_basket`,
    `buy_option_contract`, `trade_future`, `invest_mutual_fund` and `list_mutual_funds`.

  The MCP order tools placed real Zerodha orders outside the `SafetyChecker` and the kill
  switch. The only MCP order tool left is `place_order`. Mutual funds are no longer
  supported, because the INDstocks API has no mutual-fund endpoints.

### Added

- **Going live on AWS** (`docs/deployment/go-live.md`).
  - **Automatic daily token from TOTP** (`skopaq/broker/auto_token.py`, `skopaq token
    auto`, and **Broker → Automatic daily token** on the dashboard).
    - It needs `SKOPAQ_INDSTOCKS_CLIENT_ID`, `_MPIN` and `_TOTP_SECRET` in `.env` only.
      The scheduler then makes the day's token at the pre-flight, and before a session
      that has none.
    - It works under a file lock, at most once a minute, and only when the stored token
      would not last the session.
    - After 2 failures it pauses for 15 minutes, which stays below the broker's lockout.
    - Secrets are never logged.
  - **Live readiness check** (`skopaq/execution/readiness.py`, `skopaq preflight
    [--live]`, `GET /api/dashboard/readiness`, and **Control → Live readiness**). It
    checks:
    - the token and whether it lasts the session;
    - the account: NSE and F&O onboarded, DDPI;
    - the host clock against the broker's;
    - the egress IP against `SKOPAQ_INDSTOCKS_STATIC_IPS`;
    - funds, the kill switch, the WebSocket budget, the control dir and the database.

    A live scheduler sends what fails to Telegram at the pre-flight.
  - **Static-IP gate:** with `SKOPAQ_INDSTOCKS_STATIC_IPS` set, a live session refuses to
    start (PRE_OPEN) from an egress IP that is not whitelisted.

- **F&O trading: index options buying** (`skopaq/scalping/fno_engine.py`,
  `fno_rules.py`, `docs/trading/fno.md`). It runs beside the daily session
  (`SKOPAQ_FNO_ENABLED`, off by default) or alone (`skopaq fno`).
  - **Direction:** the scalping strategies on the underlying's 1-minute candles. A
    bullish setup buys a CE. A bearish setup (the same strategies on inverted candles)
    buys a PE.
  - **Contract:** from the live option chain, ATM by default
    (`SKOPAQ_FNO_STRIKE_OFFSET`), with a spread filter. On expiry day the next expiry is
    used.
  - **Size:** whole lots by premium risk, capped by `SKOPAQ_FNO_MAX_LOTS`, the premium
    cap and the safety lot cap.
  - **Charges:** a trade whose expected profit does not cover the F&O charges several
    times over is skipped.
  - **Exits:**
    - the underlying's stop and target;
    - a premium stop, with breakeven at +1 R and then a trail;
    - a time stop;
    - a flatten at 15:10.
  - **Limits:** trades a day, open positions, daily loss, a cool-down after a loss, and
    the kill switch.
  - **Futures:** `SKOPAQ_FNO_INSTRUMENT=futures`, long only. Index futures are refused by
    the immutable order-value and position limits.
  - **Dashboard:** an F&O card (P&L, open contracts with Close, the day's trades) and an
    F&O settings card. A live **Close all F&O at the broker** works without a session.
- **Derivative orders through the whole pipeline.**
  - `TradingSignal` gains `segment`, `security_id` and `lot_size`.
  - **Executor:** builds DERIVATIVE orders: INTRADAY/MARGIN, whole lots, no Yahoo price,
    no ATR sizing.
  - **Safety:** an F&O SELL may only close a long position of the same contract, so
    there is no option writing and no short futures. Options are paid from the
    option-buying balance, futures from an estimated margin. One lot passes the position
    percentage like one share.
  - **Live SELL checks:** they read the F&O positions (no holdings) and leave out
    order-book rows of the other segment.
  - **Exit re-pricing:** uses the `NFO_` / `BFO_` LTP on the ₹0.05 tick.
  - **Paper engine:** never writes options. Its F&O rows keep their contract.

- **Intraday scalping engine** (`skopaq/scalping/`, `docs/trading/scalping.md`). It trades
  long scalps on live ticks with the INTRADAY product, and runs beside the daily session
  (`SKOPAQ_SCALP_ENABLED`, off by default) or alone (`skopaq scalp`).
  - **Strategies:** `vwap_pullback`, `ema_rsi`, `orb` and `range_reversal`, evaluated on
    1-minute candles seeded from REST history.
  - **Limits:** trades a day, open scalps, daily loss, a cool-down after a loss, and the
    kill switch.
  - **Sizing:** by risk, capped by value and by the safety share cap.
  - **Charges:** a scalp whose target does not pay its round-trip charges several times
    over is skipped.
  - **Exits:** stop and target, then breakeven at 1 R and a one-ATR trail, a time stop,
    and a flatten at 15:10.
  - **Backtest:** `skopaq scalp-backtest SYMBOL --days N` runs the same strategy and exit
    code on past 1-minute candles.
  - **Dashboard:** a Scalper card (P&L after charges, per strategy, open scalps with
    Close, the day's trades) and a Scalping settings card.
  - `TradingSignal.product` now carries INTRADAY through the Executor and onto trade rows.
    The swing monitor and CLOSING manage CNC rows only.
- **Control center in the dashboard** (new **Control** page, admin actions). From the page
  you can:
  - pause or resume new BUYs;
  - turn auto sessions on or off;
  - start today's session now (live: or a monitor);
  - stop the running session, which then sells what it holds;
  - see live positions (LTP, P&L, stop, target, high), close one or all, and edit a stop or
    target;
  - place a manual BUY or SELL through the safety checks (live: type LIVE);
  - see today's INDstocks order book and cancel open orders;
  - change the main exit and risk settings.

  **How it works:**
  - The API, the scheduler and the session talk through files in `SKOPAQ_CONTROL_DIR`
    (`skopaq/execution/control.py`: status, stop/start requests, claimed commands).
  - The daemon and the monitor publish their status every few seconds, and the page streams
    it with Server-Sent Events (`/api/dashboard/control/stream`).
  - With a session running, actions go to its monitor. Without one, live closes and orders
    are placed by the API through the same Executor and SafetyChecker.
  - Every action is logged and sent to Telegram.
  - API: `skopaq/api/dashboard_control.py`.
- **Live prices over the INDstocks WebSocket.**
  - `skopaq/broker/websocket.py` is rewritten from the official docs:
    - `PriceFeed` subscribes with `SEGMENT:TOKEN`, keeps the newest tick per instrument,
      and drops stale ticks.
    - `OrderUpdateFeed` decodes the double-encoded frames and the short status codes.
    - Both send `Authorization: <token>` with no `Bearer`, reconnect with backoff,
      re-subscribe, and ignore heartbeats.
  - **Position monitor:** while the feed is on (`SKOPAQ_WS_PRICE_FEED_ENABLED`, default on),
    it checks stops and targets every `SKOPAQ_MONITOR_TICK_POLL_SECONDS` (1 s) on fresh
    ticks. Stale ticks fall back to REST quotes at the old pace, and the AI tier and the
    broker resync keep their pace in seconds.
  - The daemon and `skopaq monitor` start and stop the feed.
  - New `skopaq/market/candles.py`: 1-minute candles from ticks, plus EMA, RSI, ATR and
    VWAP.
  - New `skopaq ticks SYMBOL [--orders]` prints the live feed, to check it on the server.
- **Target, stop-loss and partial profit booking for every position**
  (`skopaq/execution/exit_plan.py`, paper and live). Each filled BUY gets an exit plan from
  its fill price and the position sizer's stop. Positions without one get a plan from the
  average price and the hard stop.
  - **Stop-loss:** the monitor sells everything at the BUY's own stop when that is tighter
    than the hard stop.
  - **Target:** at the target it sells `SKOPAQ_MONITOR_PARTIAL_BOOKING_PCT` of the
    position (default half; `1` = all). The rest gets a stop at breakeven that trails
    `SKOPAQ_MONITOR_TRAILING_STOP_PCT` below the high.
  - **Target modes** (`SKOPAQ_MONITOR_TARGET_MODE`): `rr` (default 1:2,
    `SKOPAQ_MONITOR_TARGET_RR`), `pct`, `inr` (₹ profit on the position), `off`.
  - **Restarts:** plans are saved per day in `SKOPAQ_EXIT_PLAN_DIR` with the high-water
    mark and what was booked, so a restarted or recovery monitor does not book twice and
    keeps the high.
  - **Where they show:** the BUY's stop and target are stored on its trade row (the
    dashboard's open positions show them), and every setting is on the Environment page.
  - **Paper P&L:** a paper SELL of part of a position is now booked by quantity instead of
    closing the whole BUY row.
- **F&O groundwork on INDstocks.**
  - New `INDstocksClient` methods: `get_option_chain` (`/market/option-chain`, with IV and
    Greeks), `get_expiries`, `search_derivatives`, `get_margin` (`/margin`) and
    `get_derivative_positions`. `get_funds` now also returns the intraday, option-buy,
    option-sell and futures balances.
  - New `skopaq/broker/fno.py` maps a name to the INDstocks ids: index or stock underlying,
    expiries, lot size, option contract, future.
  - `OrderRequest` gains `lot_size`. It refuses a derivative quantity that is not a whole
    number of lots, `CNC` on a derivative and `MARGIN` on equity.
  - `max_lots_per_position` counts lots for derivatives.
  - The naked-option rule also recognises INDstocks chain symbols
    (`NIFTY-Aug2026-24450-CE`).
- **Option chain from INDstocks** on the dashboard, MCP and the strategy selectors. All
  strikes are kept (ITM and OTM) and each contract carries its `security_id`, Delta and Theta.
  There is a new `GET /api/dashboard/options/expiries` endpoint.
- **Portfolio shows F&O positions**, and the Telegram EOD summary includes them.
- **Telegram `/token`** and a 09:00 IST INDstocks token check, replacing the Kite login
  reminder. The 09:25 IST scan uses INDstocks quotes.

- **Custom OpenAI-compatible AI gateway** (`SKOPAQ_CUSTOM_LLM_BASE_URL`, `_API_KEY`,
  `_MODEL`, `_JUDGE_MODEL`), e.g. CodeCraft API. When set, every agent role uses it first and
  the other providers stay as fallbacks. Environment page: **Test connection** lists the
  gateway's models and sends one test prompt (`POST /api/dashboard/llm/check`, admin).
- **Dashboard redesign and every backend feature in the web UI.** A new design (sidebar,
  mobile tab bar, cards, charts, `lucide-react` icons) on every page, and new pages:
  Portfolio (INDstocks and Kite positions, holdings, funds, orders, mutual funds, GTT),
  Broker (set today's INDstocks token, Kite login and status), Options (chain and selling
  ideas), AI learning (tracker insights, symbol record, memory search), Backtest (backtest and
  Monte Carlo), Jobs. Scheduler gains **Settle now**, Scanner the background scanner status.
  New read-only API in `skopaq/api/dashboard_tools.py`; new job kinds `backtest`,
  `montecarlo`, `settle`. Nothing in the dashboard places a broker order.
- **Environment settings in the dashboard** (Settings → ⚙️ Environment, admins only). Set,
  change or reset `SKOPAQ_*` settings (mode, scheduler, INDstocks token, Telegram, LLM
  keys, risk and daemon limits) without editing `ENV_FILE` and redeploying. They are saved in
  `~/.skopaq/env_overrides.json` on the home volume, win over `ENV_FILE`, and survive
  deploys. Secrets are never sent back to the browser. Turning live trading on needs typing
  `LIVE`, and every change is logged (`env_overrides.log`) and sent to Telegram. The
  scheduler picks changes up between sessions, never during one. Login and API access
  settings, state directories, `SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK` and
  `SKOPAQ_TRADING_HALTED` stay `ENV_FILE`-only (docs/deployment/dashboard.md).

Moves the vendored TradingAgents from v0.5.1 to **v0.5.2** (upstream commit
`5eb5085`, 65 commits). No Skopaq-facing behaviour change beyond the notes
below.

### Fixed

- **BSE orders sent the NSE algo id.** INDstocks wants `99999` on NSE and
  `9999999999999999` on BSE. The id is now chosen per exchange, and
  `SKOPAQ_INDSTOCKS_ALGO_ID_NSE` / `_BSE` take registered ids.
- **Quotes and data calls could exceed the broker's limits.** The broker allows 5 a
  second; they were allowed 100. They now have their own limiters at 4 a second. Order
  calls use a rolling window of 8 a second, under the broker's 10 and SEBI's 10 orders a
  second for unregistered retail algos. A 429 on a GET is retried twice after a pause; an
  order is never re-sent.
- **The daemon could open more WebSocket connections than INDstocks allows** (3 per
  account). The monitor now shares the scalper's or F&O engine's price feed.
- The token file is written atomically, so a container never reads a half-written token.
- Trade rows wrote product `INTRADAY`, which the `trades` table's CHECK refuses (CNC, MIS,
  NRML). INTRADAY is now written `MIS`, and MARGIN is written `NRML`.

### Changed

- **The dashboard's market data comes from INDstocks.** Index quotes (NIFTY 50, Bank
  NIFTY, India VIX, SENSEX) and every chart now come from INDstocks live, as stock quotes
  already did.
  - Index quotes use the index's id from the index instruments file, priced as
    `NIDX_<id>` / `BIDX_<id>`. The working code is probed once and remembered.
  - Charts use `/market/historical`, paged within the broker's window per call.
  - Yahoo Finance is only the fallback.
  - The Market page refreshes every 5 s while NSE is open, and shows the source on every
    quote and chart.
  - The F&O engine uses the same verified index code.
- **Order size limits are set from the dashboard** (**Control → Exits & risk**).
  - Equity orders were capped at 5 shares, so most scalps could not pay their charges.
  - Equity now has `SKOPAQ_MAX_SHARES_PER_ORDER` (default 1000).
  - F&O now has `SKOPAQ_MAX_LOTS_PER_ORDER` (default 5).
  - Both apply to every strategy and are clamped to immutable ceilings in `SafetyRules`:
    `max_shares_per_position` 5000 and `max_lots_per_position` 20. 20 lots is below
    NSE's freeze quantity for index options.
  - The order-value (₹5 lakh, ₹2 lakh in the daemon) and position-size (15 %) rules still
    bound every BUY.
  - `skopaq preflight` shows the limits in force.
- **Brokerage is booked at ₹10 per executed order**, the INDstocks API fee
  (`INDSTOCKS_BROKERAGE_PER_ORDER_INR`). Live orders were booked at ₹20 and paper orders
  at ₹5.

### Breaking

- **The `parallel_analysts` config key is gone.** Upstream v0.5.2 (#1255) made
  the parallel analyst graph the only layout, using the same isolated-subgraph
  design Skopaq had added behind that key. Config files still naming
  `parallel_analysts` are ignored. Sequential analyst runs are no longer
  available. Upstream also stops an analyst after `max_tool_rounds` and asks it
  to write its report, so a model that keeps calling tools can no longer run
  the graph into its recursion limit.
- **Upstream requires Python 3.11+ and pandas 3.** Python is already 3.11+ here;
  the pandas floor moves and is a real runtime change, not just a version
  string.

### Upstream changes worth knowing

- `tradingagents.decision_log` → `tradingagents.memory.log`;
  `tradingagents.graph.settlement` → `tradingagents.memory.settlement`;
  `tradingagents.graph.reflection` → `tradingagents.memory.reflection`. No
  action needed for CLI or config users.
- The Portfolio Manager's decision is now the top-level `final_trade_decision`;
  `judge_decision` left `RiskDebateState`.
- The price and indicator tools take their ticker from state instead of from
  the model, so every vendor now receives the exchange-qualified symbol
  (`RELIANCE.NS`). The INDstocks vendor strips the suffix it does not want.

### Internal

- `VENDOR_LIST` is re-added by Skopaq; upstream deleted it in v0.5.2. See
  [`UPSTREAM_CHANGES.md`](UPSTREAM_CHANGES.md) modification 2.
- Verified against upstream's own suite: 1137 passed, identical to the
  pristine v0.5.2 baseline. Skopaq's `tests/unit`: 1664 passed.


## [0.2.0] — 2026-09-26

This release moves SkopaqTrader onto TradingAgents v0.5.1. It adds calibrated
decisions with TypeSafe Jev, hardens order safety end to end, and makes the
stack run unattended on a Mac mini M4 (or any Docker host). Paper trading is
still the default everywhere.

### Upgrade notes

- **Apply `supabase/migrations/003_system_flags.sql`** in the Supabase SQL
  editor. The kill switch uses it to halt every process and machine at once.
- **Copy the new settings from `.env.example`**:
  - scheduler: `SKOPAQ_SCHEDULER_*`;
  - API security: `SKOPAQ_API_TOKEN`, `SKOPAQ_CORS_ORIGINS`;
  - URLs: `SKOPAQ_PUBLIC_BASE_URL`, `SKOPAQ_API_BASE_URL`;
  - Telegram: `SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS`;
  - Jev: `SKOPAQ_JEV_*`;
  - live order confirmation: `SKOPAQ_ORDER_*`, `SKOPAQ_MONITOR_RESYNC_CYCLES`
    and `SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK` (off by default; see below).
- **Live BUYs that don't fill within 30 seconds are cancelled**
  (`SKOPAQ_ORDER_FILL_TIMEOUT_SECONDS`). INDstocks turns MARKET orders into
  LIMITs at the live price, so some entries will not fill and are cancelled
  rather than left resting.
- **In live mode a SELL is refused if the broker's order book can't be read.**
  You get a critical alert. `SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK=true`
  overrides this; every use is logged and alerted.
- **Kite orders are real, whatever the trading mode.** With a Kite session, the
  MCP server's advanced-order tools (AMO, bracket, cover, basket, options,
  futures, mutual funds, GTT, swing trade) place real Zerodha orders even in
  paper mode, outside the safety checks. Claude Code asks your permission for
  each one. Behaviour unchanged, now documented.
- **Telegram is private by default.** Only chats in
  `SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS` (or `SKOPAQ_TELEGRAM_CHAT_ID`) get
  answers. Trades wait for `/confirm`.
- **Railway's daemon cron now runs at 09:15 IST**, up from 09:10. Disable it
  before starting the Docker scheduler, or two daemons will trade the same
  account.
- **`docker/Dockerfile` is gone.** Every deployment (compose, Fly, Railway)
  builds the root `Dockerfile`.
- **INDstocks only accepts whitelisted IPs.** An always-on host needs a static
  egress IPv4 on that whitelist. A Cloudflare Tunnel does not provide one; see
  `docs/deployment/mac-mini.md` §3.
- **LLM models moved** to Gemini 3.8 Flash, Grok 4.6 (OpenRouter) and Claude
  Opus 5.

### Added

- **TradingAgents v0.5.1 base.** It brings the append-only decision log,
  settled against NIFTY once each call's holding window has traded, and
  upstream's own test suite runs in CI against our `tradingagents/`. Every
  local modification is listed in `UPSTREAM_CHANGES.md`.
- **Always-on deployment** (`docker-compose.yml`, runbook
  `docs/deployment/mac-mini.md`, checks `scripts/macmini/verify.sh`):
  - api, telegram and a scheduler service run on persistent volumes;
  - one non-root image, built natively for linux/arm64 and amd64;
  - a health check per service.
- **Scheduler** (`skopaq schedule`):
  - runs one daemon session per NSE trading day at 09:15 IST, with catch-up
    until 11:30 and a hard stop at 15:45;
  - checks the INDstocks token at 08:45;
  - settles decisions at 18:30;
  - optional dead-man's-switch ping;
  - holds a single-instance lock.
  - In live mode it keeps positions protected with `skopaq monitor` after a
    failed, killed or interrupted session, and resumes that protection after a
    restart.
- **TypeSafe Jev**, off unless `SKOPAQ_JEV_ENABLED=true`:
  - calibrated confidence for entry signals, where a confident contradiction
    becomes HOLD;
  - the sell analyst's exit call;
  - catalyst scoring in the scanner;
  - the MCP `quick_decision` tool;
  - it can run through a TypeSafe-compatible gateway such as OpenRouter
    (`SKOPAQ_JEV_BASE_URL`).
- **Kill switch**: `skopaq halt` / `skopaq resume`, the Telegram `/halt` and
  `/resume` commands, and the MCP `halt_trading` / `resume_trading` tools.
  While halted, every BUY is rejected on every path, and SELLs still protect
  open positions.
- **Track record** (`skopaq report`, MCP `performance_report`):
  - AI calls against NIFTY;
  - closed trades: win rate, profit factor, drawdown;
  - confidence calibration.
- **`skopaq monitor` exit code**: exits 4 in live mode when positions or
  unconfirmed orders are left, so the scheduler alerts.
- **`docs/indstocks_api.md`**: the INDstocks API reference that CLAUDE.md
  already pointed to.
- **`skopaq settle`**: settles past decisions whose holding window has traded.
- **`skopaq memory legacy`**: exports or deletes pre-v0.5.1 agent memories.
- **Parallel analysts** in isolated message channels (opt-in upstream, on in
  Skopaq).
- **CI on every PR**:
  - unit tests on Python 3.11, 3.12 and 3.14;
  - a lint gate for syntax errors and undefined names;
  - upstream TradingAgents' suite;
  - a Docker image build with the compose stack brought up and health-checked.
- **API access control**: `SKOPAQ_API_TOKEN` guards the endpoints that run
  tools or hand out credentials, and CORS origins are configurable.

### Changed

- **Protective exits are MARKET orders.** This covers the monitor's hard stop,
  trailing stop, EOD exit and AI SELL, and the daemon's closing sell-all. Exit
  P&L is measured against the cost basis of the shares sold.
- **Risk limits on new positions apply to BUYs only**: position size, order
  value, lot count, the daily/weekly/monthly loss limits and the cool-down
  after a loss. A SELL can only reduce shares you hold. Market hours, the
  order rate limit and the no-short-sale rule still apply to SELLs.
- **Loss limits hold across processes.** Daily, weekly and monthly limits are
  seeded from P&L already realised (Supabase), not only from trades in the
  current process.
- **Daemon exit codes**: 1 when a session fails, 3 when pre-open fails, 0
  otherwise (one stock's failed analysis no longer counts as a failure).
- **Kite**: a saved access token expires at 06:00 IST, and login links use
  `SKOPAQ_PUBLIC_BASE_URL` instead of a hard-coded Fly host.

### Fixed

- **Stop-losses below entry never executed.** Exits were LIMIT orders at the
  entry price.
  - Paper refused the fill on every cycle.
  - Live would have left a resting order above the market while recording the
    exit at breakeven.
  - Separately, after one loss, the cool-down and the loss limits blocked the
    next stop-loss.
- **Live orders counted as filled when merely accepted.** An order now counts
  only when INDstocks confirms it, at the real average price and filled
  quantity.
  - A rejected, cancelled or unconfirmed order never counts.
  - A protective exit that rests is re-priced to the instrument's tick size and
    re-placed, within a deadline that finishes before the scheduler's shutdown.
    If it still doesn't fill, you get a critical alert.
  - An unfilled entry is cancelled, and only the filled quantity reaches trade
    rows, P&L and the monitor.
  - Stuck and late orders are journalled, so a recovery `skopaq monitor` picks
    them up, and their fills are booked exactly once.
- **The no-short-sale check** ignored today's sales, counted paper shares
  twice, and did not count pending SELL orders. It now reads the order book
  before positions.
  - It subtracts open SELLs, and filled SELLs that positions don't show yet.
  - It also subtracts Skopaq's own stuck exits.
  - A per-symbol lock stops two processes from selling the same shares.
  - Protective exits never dip into older delivery holdings.
- **Paper SELLs could close live trade rows**, even when refused, so phantom
  P&L reached the live loss limits.
- **Live broker data**: holdings parsed as zero, a null field crashed
  positions, and the client read the wrong order-status field.
- **The Docker image**:
  - the container user could not write its own state, so every analysis
    failed;
  - unquoted `>=` specifiers were shell redirections, so version pins were
    dropped;
  - secrets could reach the image;
  - the MCP stdio server was corrupted by a startup banner.
- **Compose**: containers that first-mounted a fresh volume together could
  fail with `mkdir ...: file exists`.
- **The scheduler**:
  - a full disk could freeze a live session;
  - a mistyped setting crash-looped every container;
  - a second scheduler treated the running session as interrupted.
- **Tests**: with a real `.env` loaded, the suite wrote a halt to production
  Supabase. Tests could also read the host's Kite session file.
- **Telegram**: the bot answered anyone who found it. It now serves only the
  allow-list, and trades wait for `/confirm`.
- **Other fixes**:
  - INDstocks instruments were downloaded on every lookup;
  - a stale token file shadowed a valid env token;
  - the checkpoint key ignored parallel mode;
  - three undefined-name bugs were found by the new lint gate.

### Known limits

- INDstocks turns MARKET orders into LIMITs at the live price. Exits are
  re-priced and retried, but a very fast fall can still leave one unfilled
  until the next attempt.
- The Kite MCP order tools act outside Skopaq's safety checks (see Upgrade
  notes).
- Holiday lists must be updated each December (`skopaq/risk/calendar.py` or
  `SKOPAQ_NSE_HOLIDAYS`). Without them the scheduler fails closed and alerts.

## [0.1.0] — 2026-04-08

The initial platform, built on TradingAgents v0.2.0:

- the multi-agent analysis pipeline;
- INDstocks and Kite Connect brokers, with a paper engine;
- the autonomous daemon, with position monitoring and an AI sell analyst;
- the multi-model scanner;
- options selling, GTT orders and CNC swing trading;
- an MCP server with 31 tools;
- the Telegram bot, with scheduled jobs and notifications;
- backtesting and strategy refinement;
- Fly.io, Railway and Vercel deployments;
- the MkDocs documentation site.

[0.2.0]: https://github.com/Skopaq-AI/skopaqtrader/compare/4f6dd89...main
[0.1.0]: https://github.com/Skopaq-AI/skopaqtrader/tree/4f6dd89
