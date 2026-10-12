# CLAUDE.md — SkopaqTrader Project Context

This file provides context for AI coding agents working on the SkopaqTrader codebase.

## Project Overview

Algorithmic trading platform for Indian equities with **one AI: the Adaptive AI** (machine-learning models trained on our own market data — no LLM, no API AI). A custom `skopaq/` layer holds the INDstocks broker integration, the Adaptive AI (intraday models, RL agents, the swing model), and an autonomous, safety-gated execution pipeline. The data vendors in `tradingagents/dataflows` come from [TradingAgents v0.5.2](https://github.com/TauricResearch/TradingAgents) (Apache 2.0); its LLM agents were removed.

## Architecture

- **`tradingagents/dataflows`** — Vendored upstream market-data vendors (yfinance, Alpha Vantage, INDstocks routing). Only the data layer is kept; changes are tracked in `UPSTREAM_CHANGES.md`.
- **`skopaq/`** — Everything else: broker client, Adaptive AI, execution pipeline, CLI, config, risk management, daemon.

### Key Flow (swing book)

```
skopaq scan / daemon SCANNING → SwingEngine.scan (daily setups → swing model P(win) ≥ threshold)
  → SignalGate (net R:R after delivery charges, data quality, stop bounds; journalled)
  → Executor: PositionSizer → SafetyChecker → OrderRouter → INDstocks/Paper
  → PositionMonitor (stop, target at the setup's R multiple, trailing) → exit
```

The swing engine is `skopaq/swing/engine.py`; setups and their simulated outcomes are in `skopaq/swing/setups.py`, and the model, its registry (champion / shadow) and gates in `skopaq/swing/model.py`. Without a swing model there are no swing entries, unless `SKOPAQ_SWING_RULES_ONLY=true` on a paper account. Intraday (`skopaq scalp`, `skopaq fno`) uses the adaptive models in `skopaq/adaptive/` and `skopaq/learning/` through the same SignalGate.

### Daemon Flow (autonomous)

```
PRE_OPEN → SCANNING → ANALYZING → TRADING → MONITORING → CLOSING → REPORTING
```

The daemon (`skopaq/execution/daemon.py`) is a finite state machine that composes all subsystems into a single unattended trading session. On an always-on host the scheduler (`skopaq/execution/scheduler.py`, `skopaq schedule`, the compose `scheduler` service) starts one session per NSE trading day at 09:15 IST, catches up until 11:30 if the host was down, stops a session still running at 15:45, and in live mode keeps a failed session's positions protected with `skopaq monitor` (which exits 4 when it ends with positions still open or orders unconfirmed — after the close, or earlier on a stop — alerted as "check the broker").

## MCP Server (Claude Code Integration)

SkopaqTrader exposes a **MCP server** (`skopaq/mcp_server.py`) that provides 26 trading tools directly inside Claude Code (the core ones are listed below). Configured in `.claude/.mcp.json`.

**IMPORTANT**: When fetching market data, quotes, or portfolio info — always use the MCP tools (`mcp__skopaq__*`). Do NOT write Python/Bash code to call `INDstocksClient` or other broker modules directly. The MCP tools handle authentication, scrip resolution, and error handling internally.

**Available MCP tools** (auto-allowed for read-only, require permission for trades):

| Tool | What it does |
|------|-------------|
| `get_quote` | Real-time stock quote (LTP, OHLC, bid/ask) |
| `get_historical` | OHLCV candles (1/5/15/60 min resolution) |
| `get_positions` | Open positions with P&L |
| `get_holdings` | Delivery holdings |
| `get_funds` | Cash balance, margin, collateral |
| `get_orders` | Today's orders with status |
| `swing_ideas` | Today's swing ideas from the Adaptive AI swing model (no orders) |
| `swing_idea` | The setup and model probability for one symbol |
| `gather_all_analysis_data` | Raw market, news and fundamentals data for your own review |
| `check_safety` | Pre-trade safety validation |
| `place_order` | Execute order, safety-checked (the paper engine: the MCP server has no live INDstocks client) |
| `halt_trading` / `resume_trading` | Kill switch: stop / allow new BUYs everywhere |
| `performance_report` | Track record: journalled signals, closed trades |
| `system_status` | Health check (version, mode, swing model champion / shadow) |

**Custom slash commands**: `/quote RELIANCE`, `/analyze TCS` (swing setup + data), `/scan` (swing ideas), `/portfolio`, `/trade INFY`

## Common Commands

```bash
# Run unit tests (1,700+ tests, no API keys needed)
python3 -m pytest tests/unit/ -x -q

# Run a specific test file
python3 -m pytest tests/unit/execution/test_daemon.py -v

# CI (.github/workflows/ci.yml) runs on every PR: unit tests on Python 3.11/3.12/3.14,
# lint for syntax errors and undefined names (ruff --select E9,F63,F7,F82),
# and a build of the Docker image with the compose stack brought up and health-checked

# Run integration tests (requires .env with real keys)
python3 -m pytest tests/integration/ -v -m integration

# CLI commands
skopaq status              # Health check
skopaq preflight --live    # Live readiness: token, account, clock, static IP, funds (exit 1 on a failure)
skopaq token auto          # Today's INDstocks token from TOTP (the scheduler does it at 08:45)
skopaq swing-train         # Train the swing model on daily history (registers a shadow; no orders)
skopaq swing-status        # Swing model champion / shadow and their test metrics
skopaq swing-promote VERSION --by NAME --reason TEXT  # Shadow → champion (live uses champions only)
skopaq swing-rollback      # Back to the previous swing champion
skopaq scan                # Today's swing ideas (setup, entry/stop/target, P(win)); no orders
skopaq daemon --once --paper  # Full autonomous session
skopaq schedule --check    # Show the scheduler's plan (the compose service runs `skopaq schedule`)
skopaq scalp               # Intraday scalper now (paper; --live real INTRADAY orders), until 15:10
skopaq scalp-backtest RELIANCE --days 5  # Scalping strategies on past 1-minute candles
skopaq fno                 # F&O engine now: buys CE/PE on index setups (paper; --live), until 15:10
skopaq ticks RELIANCE      # Live INDstocks WebSocket ticks + 1-min candles/indicators (read only)
skopaq monitor             # Monitor existing positions; live: exits 4 if it ends with positions open or orders unconfirmed
skopaq report              # Track record: journalled signals, closed trades
skopaq learn-train --symbols RELIANCE,TCS --days 20 --regimes  # candidate model + regime map (no orders)
skopaq charges-reconcile note.csv  # broker contract note vs estimated charges (exit 3 on a discrepancy)
skopaq halt "reason"       # Kill switch: reject every BUY everywhere
skopaq resume              # Lift the kill switch
skopaq serve               # FastAPI server
```

## Configuration

- **`SkopaqConfig`** in `skopaq/config.py` — Pydantic Settings with `env_prefix="SKOPAQ_"`
- **`.env`** file at repo root (gitignored) — all secrets live here
- **`.env.example`** — template with all configurable keys and placeholders
- **Dashboard overrides** — `skopaq/env_overrides.py` applies settings saved from the dashboard at process start
- **No LLM keys** — there is no LLM provider; the only API keys are the broker's, Supabase's and optional data vendors'

## Adaptive AI

- **Swing** (`skopaq/swing/`): three daily setups (trend_pullback, breakout, oversold_bounce) with an ATR stop and an R-multiple target; a model (LightGBM by default; `SKOPAQ_SWING_MODEL_KIND=linear` needs no ML library) trained on the setups' simulated outcomes net of delivery charges. Its registry lives in `<SKOPAQ_MODEL_DIR>/swing`; a model becomes a shadow only after passing the gates (enough test samples and gated trades, beats taking every setup, profit factor, expectancy CI above zero). Paper uses the champion, else the shadow; live uses the champion only. Bundles are JSON plus a manifest checked on load (never pickle).
- **Intraday** (`skopaq/adaptive/`, `skopaq/learning/`, RL agents): models score the scalper's and F&O engine's signals in the SignalGate; continual learning retrains shadows from the signal journal. Promotion is always explicit (`skopaq swing-promote`, `skopaq rl-promote`, dashboard Learning page).
- **Learning never changes hard limits** — `SafetyRules`, the loss limits and the kill switch are outside every model.

## INDstocks API

- ALL market data endpoints use `scrip-codes=NSE_2885` format (NOT `symbols=NSE:RELIANCE`)
- Auth header: `Authorization: TOKEN` (no "Bearer" prefix)
- Historical endpoint: input timestamps in **milliseconds**, response `ts` in **seconds**
- Candle objects: `{"ts":, "o":, "h":, "l":, "c":, "v":}`
- Quote fields: `live_price`, `day_open`, `day_high`, `day_low`, `prev_close`
- POST /order returns `data.order_id` and `data.order_status` (not `status`). Acceptance is not a fill: `skopaq/execution/live_orders.py` (`LiveOrderWorker`) confirms every live fill, cancels what an entry has not filled in time, and works protective exits until filled
- Order rows (GET /order-book, GET /order): `id`, `txn_type`, `status`, `requested_qty`, `traded_qty`, `traded_price` (a string, `""` until filled), `security_id`, `product`, `extra_info`, `updated_at`. `name` is not the symbol and rows have no trading symbol (match on `security_id`). Parse rows and statuses with `skopaq.broker.order_status`; never compare raw status strings
- GET /order and GET /order/trades take `{order_id, segment}` as a JSON body on a GET (the client also sends them as query params, and falls back to `/trades/{order_id}`)
- Order and portfolio calls use the strict envelope (`_request_envelope`): a 2xx body with `status: error|failure` raises `BrokerError(kind="error_body")`, and `data: null` is empty only under `status: success`. `BrokerError.kind` says whether an order may exist; `OrderPlacementUncertain` means reconcile against the order book, never re-send
- `/portfolio/positions` needs lowercase `segment` and `product` (the client queries cnc and intraday); holdings rows use `total_qty` / `avg_price`. Read the order book **before** positions and holdings (`read_broker_snapshot`)
- MARKET orders become a LIMIT at the LTP and can rest; tick size comes from the instruments CSV (`resolve_tick_size`)
- Always refer to `docs/indstocks_api.md` for endpoint reference — do not assume

## File Organization

```
skopaq/
├── adaptive/        # Adaptive AI: feature pipeline, models, bundles, RL
├── api/             # FastAPI backend
├── broker/          # INDstocks REST/WS + Binance + paper engine
├── cli/             # Typer CLI (main.py = all commands, display.py = Rich output)
├── db/              # Supabase client + repositories
├── execution/       # Executor, safety checker, order router, live order worker, daemon, position monitor
├── learning/        # Signal-journal datasets, model registry, continual learning, reports
├── risk/            # ATR sizing, regime detection, drawdown tracker, calendar
├── strategy/        # SignalGate, qualification rules, signal journal, no-code strategies
├── swing/           # Swing setups, swing model + registry, SwingEngine (scanner + entries)
├── config.py        # SkopaqConfig (pydantic-settings)
└── constants.py     # SafetyRules (immutable) + daemon variants
```

## Testing Patterns

- **Unit tests** (`tests/unit/`): Mock all external dependencies. No API keys needed.
- **Integration tests** (`tests/integration/`): Real API calls. Require `.env` with valid keys.
- **Fixtures**: Common config mocks in test files. `conftest.py` at `tests/` root.
- **Async tests**: Use `@pytest.mark.asyncio` + `AsyncMock` for async methods.
- **Patch targets**: When a function is locally imported inside a method body (common in `daemon.py`), patch at the **source module**, not the importing module. Example: `patch("skopaq.swing.engine.SwingEngine.from_config")` not `patch("skopaq.execution.daemon.SwingEngine")`.

## Key Conventions

1. **Safety rules are immutable** — `SafetyRules` in `constants.py` cannot be overridden at runtime. The `SafetyChecker` enforces them before every order; the limits on new risk (size, value, lots, loss limits, cool-down) apply to BUYs only, since a SELL can only reduce what is held, and protective exits are MARKET orders, not LIMITs at the entry price (live, the worker re-places a resting exit as a tick-rounded LIMIT). The no-short-sale check subtracts open and just-filled SELL orders (and Skopaq's own unresolved SELLs the book does not list yet), reads the order book before positions and holdings, and refuses a live SELL when the order book cannot be read (override: `SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK`, default false, logged at CRITICAL on every use and alerted at most once per symbol every 10 minutes). The dashboard's Control page (`skopaq/api/dashboard_control.py`) reaches the scheduler, the daemon and the monitor through files in `SKOPAQ_CONTROL_DIR` (`skopaq/execution/control.py`: status, stop/start requests, commands the running monitor claims); without a session, live closes and orders go through the API's own Executor and SafetyChecker. The monitor follows each position's exit plan (`skopaq/execution/exit_plan.py`: the BUY's stop-loss, a target with partial booking, the rest trailing from breakeven; saved per day in `SKOPAQ_EXIT_PLAN_DIR`). The monitor's and CLOSING's exits are `TradingSignal(position_only=True)`: sized by the day's position less Skopaq's own pending/unshown/uncertain SELLs, never older holdings. INDstocks is the only broker; the MCP server's only order tool, `place_order`, goes through the SafetyChecker to the paper engine (`docs/trading/live-trading.md`, Residual limits). The checker's daily/weekly/monthly loss limits are seeded from P&L stored in Supabase (`skopaq/execution/pnl_history.py`), so they hold across processes; the kill switch (`skopaq/execution/kill_switch.py`: `skopaq halt`, `SKOPAQ_TRADING_HALTED`, or the `system_flags` row) rejects every BUY while on.
2. **Paper mode is default** — All CLI commands default to paper trading. Live mode requires explicit `--live` or `SKOPAQ_TRADING_MODE=live` + confirmation prompt.
3. **Upstream modifications are minimal** — Changes to `tradingagents/dataflows` must be documented in `UPSTREAM_CHANGES.md` with backward-compatibility notes.
4. **No secrets in code** — All credentials come from environment variables. Never commit `.env`, token files, or API keys.
5. **Pydantic v2 models** — Broker models in `skopaq/broker/models.py` use Pydantic v2. Use attribute access (`model.field`), not dict access (`model["field"]` or `model.get("field")`).
6. **Rich output** — All CLI display uses Rich tables/panels via `skopaq/cli/display.py` and `skopaq/cli/theme.py`.

## Common Gotchas

- **INDstocks timestamps**: Historical API wants milliseconds, returns seconds. Off by 1000x is a common bug.
- **yfinance suffixes**: Yahoo Finance uses `RELIANCE.NS`. The routing layer adds/strips suffixes automatically, but new data flows must handle this.
- **Local imports in daemon.py**: Many imports are inside method bodies to avoid circular imports. This affects mock patch targets in tests (see Testing Patterns above).
- **Stop event propagation**: The daemon's `stop_event` must be checked before every major operation, not just inside delay waits.

## Deployment

- **Mac mini / any Docker host** — `docker-compose.yml` (api, telegram, scheduler on persistent volumes; one image, `Dockerfile`, linux/arm64 and amd64). Runbook: `docs/deployment/mac-mini.md`; checks: `scripts/macmini/verify.sh`
- **Railway API** — `railway.toml` — FastAPI server (always running)
- **Railway Daemon** — `railway-daemon.toml` — Cron job at 09:15 IST weekdays. Disable it before running the compose scheduler, or two daemons trade the same account
- **Vercel** — `frontend/` — Next.js dashboard
- **Supabase** — PostgreSQL + auth + trade history
- **Cloudflare Tunnel** — inbound HTTPS to the API and dashboard. It does not give a static outbound IP: INDstocks whitelists the host's egress IPv4 (`docs/deployment/mac-mini.md` §3)
