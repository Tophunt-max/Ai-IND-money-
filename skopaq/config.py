"""SkopaqTrader configuration — loaded from environment variables and .env file."""

from __future__ import annotations

import logging
import math
from typing import Any, Literal, Optional

from pydantic import SecretStr, ValidationError, ValidatorFunctionWrapHandler, field_validator
from pydantic_core.core_schema import ValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Live order settings typed as numbers/bools. Every service builds a SkopaqConfig, so a
# typo in one of them must not stop api, telegram and the scheduler: an unparseable (or
# non-finite) value is logged and replaced by the field's default (off, for the bools).
_LENIENT_FIELDS = (
    "order_fill_timeout_seconds",
    "order_fill_poll_interval_seconds",
    "order_cancel_confirm_timeout_seconds",
    "order_exit_attempt_timeout_seconds",
    "order_exit_max_attempts",
    "order_exit_reprice_buffer_pct",
    "order_reconcile_timeout_seconds",
    "order_shutdown_margin_seconds",
    "order_sell_fill_lag_window_seconds",
    "allow_sell_without_order_book",
    "indstocks_order_remarks_enabled",
    "monitor_resync_cycles",
    # Exit plans: a typo falls back to the default instead of stopping every service
    "monitor_target_mode",
    "monitor_target_rr",
    "monitor_target_pct",
    "monitor_target_inr",
    "monitor_partial_booking_pct",
    "monitor_tick_poll_seconds",
    "ws_price_feed_enabled",
    "ws_order_feed_enabled",
    "ws_tick_max_age_seconds",
    "scalp_enabled",
    "scalp_candle_seconds",
    "scalp_risk_per_trade_pct",
    "scalp_max_position_value_inr",
    "scalp_max_trades_per_day",
    "scalp_max_open",
    "scalp_max_daily_loss_inr",
    "scalp_cooldown_minutes",
    "scalp_max_hold_minutes",
    "scalp_min_reward_to_cost",
    "scalp_rest_poll_seconds",
    "scalp_rr",
    "scalp_orb_minutes",
    "scalp_min_net_rr",
    "scalp_slippage_pct",
    "scalp_htf_seconds",
    "scalp_require_htf",
    "scalp_require_regime",
    "scalp_qualification_enabled",
    "scalp_max_spread_pct",
    "scalp_max_participation",
    "scalp_model_gate_enabled",
    "fno_model_gate_enabled",
    "fno_min_net_rr",
    "fno_slippage_pct",
    "fno_qualification_enabled",
    "block_model_outliers",
    "adaptive_auto_retrain",
    "adaptive_retrain_hours",
    "adaptive_days",
    "adaptive_train_days",
    "adaptive_retrain_days",
    "adaptive_horizon_bars",
    "adaptive_di_threshold",
    "adaptive_continual_learning",
    "rl_continual_learning",
    "rl_enabled",
    "rl_allow_live",
    "rl_paper_use_shadow",
    "rl_timesteps",
    "rl_days",
    "rl_stop_atr",
    "entry_lock_wait_seconds",
    "max_consecutive_losses",
    "max_shares_per_order",
    "max_lots_per_order",
    "fno_enabled",
    "fno_allow_bearish",
    "fno_avoid_expiry_day",
    "fno_expiry_index",
    "fno_strike_offset",
    "fno_max_spread_pct",
    "fno_risk_per_trade_inr",
    "fno_max_lots",
    "fno_max_premium_inr",
    "fno_premium_stop_pct",
    "fno_trail_pct",
    "fno_max_trades_per_day",
    "fno_max_open",
    "fno_max_daily_loss_inr",
    "fno_cooldown_minutes",
    "fno_max_hold_minutes",
    "fno_min_reward_to_cost",
    "fno_candle_seconds",
    "fno_rest_poll_seconds",
    "fno_rr",
    "fno_orb_minutes",
)


class SkopaqConfig(BaseSettings):
    """Central configuration for SkopaqTrader.

    All values are read from environment variables prefixed with ``SKOPAQ_``.
    A ``.env`` file in the project root is loaded automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SKOPAQ_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Supabase ────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: SecretStr = SecretStr("")

    # ── Upstash Redis ───────────────────────────────────────────────────
    upstash_redis_url: str = ""
    upstash_redis_token: SecretStr = SecretStr("")

    # ── INDstocks Broker ────────────────────────────────────────────────
    indstocks_token: SecretStr = SecretStr("")
    indstocks_base_url: str = "https://api.indstocks.com"
    indstocks_ws_price_url: str = "wss://ws-prices.indstocks.com/api/v1/ws/prices"
    indstocks_ws_order_url: str = "wss://ws-order-updates.indstocks.com/api/v1/ws/trades"
    # Exchange algo ids sent on every order (INDstocks: 99999 for NSE, sixteen 9s for BSE).
    # Replace them only with the ids the broker or the exchange registered for your algo
    indstocks_algo_id_nse: str = "99999"
    indstocks_algo_id_bse: str = "9999999999999999"
    # The static IPs whitelisted on the INDstocks Access Tokens page (comma-separated,
    # IPv4/IPv6). Orders from any other IP are refused by the broker; with this set, a live
    # session refuses to start from another egress IP and the readiness check says so
    indstocks_static_ips: str = ""
    # Automatic daily token (POST /generate/token): the Client ID shown after TOTP setup,
    # the account MPIN and the authenticator's base32 secret. All three set: the scheduler
    # makes the day's token before the session (skopaq token auto). .env only
    indstocks_client_id: str = ""
    indstocks_mpin: SecretStr = SecretStr("")
    indstocks_totp_secret: SecretStr = SecretStr("")
    # Live prices over the price WebSocket for the position monitor (REST is the fallback)
    ws_price_feed_enabled: bool = True
    # Order updates WebSocket: only `skopaq ticks --orders` reads it (REST stays the
    # source of truth for fills)
    ws_order_feed_enabled: bool = False
    # A tick older than this is not used (the monitor asks REST instead)
    ws_tick_max_age_seconds: float = 5.0

    # ── Trading Mode ────────────────────────────────────────────────────
    trading_mode: Literal["paper", "live"] = "paper"
    initial_paper_capital: float = 1_000_000.0  # INR
    # Kill switch set at deploy level; also `skopaq halt` (skopaq/execution/kill_switch.py)
    trading_halted: bool = False

    # ── Live order confirmation (skopaq/execution/live_orders.py) ───────
    # Clamped to the [ranges] where they are used, with a WARNING naming the var; an
    # unparseable value is logged and replaced by the default (_LENIENT_FIELDS)
    order_fill_timeout_seconds: float = 30.0  # entries: wait, then cancel the rest [5, 120]
    order_fill_poll_interval_seconds: float = 1.0  # status polls (order history: 15 req/s) [0.5, 5]
    order_cancel_confirm_timeout_seconds: float = 10.0  # retry the cancel, re-read [3, 30]
    order_exit_attempt_timeout_seconds: float = 10.0  # protective exits, per attempt [3, 30]
    order_exit_max_attempts: int = 3  # cancel + re-place a resting exit at most N times [1, 5]
    # re-placed LIMIT = LTP × (1 − buf% × (attempt − 1)), at most 5% [0.1, 2]
    order_exit_reprice_buffer_pct: float = 0.5
    order_reconcile_timeout_seconds: float = 15.0  # find an uncertain placement in the book [5, 30]
    order_shutdown_margin_seconds: float = 60.0  # stop order work before kill-after [20, 120]
    # count filled SELLs that positions don't show yet for this long [60, 1800]
    order_sell_fill_lag_window_seconds: float = 600.0
    order_extra_terminal_statuses: str = ""  # comma-separated broker statuses to treat as final
    allow_sell_without_order_book: bool = False  # DANGER: SELL even if the order book can't be read
    indstocks_order_remarks_enabled: bool = False  # tag orders with `remarks` (docs: unreleased)
    order_lock_dir: str = "~/.skopaq/locks"  # per-symbol SELL locks (shared home volume)
    order_journal_dir: str = "~/.skopaq/orders"  # per-day order journal (shared home volume)
    # Sequential entries: one BUY at a time per account (an flock in order_lock_dir across
    # processes on this host, and a lock inside each process), held from the safety check
    # until the broker's answer is final. A second entry waits this long, then is refused
    entry_lock_wait_seconds: float = 20.0
    # Cross-host account leases in Upstash Redis (entries and daemon sessions); enabled
    # without a reachable Redis, entries are refused
    account_lease_enabled: bool = False
    # Consecutive losing closed trades after which new BUYs stop for the day; it can
    # only lower the immutable ceiling SafetyRules.max_consecutive_losses (4)
    max_consecutive_losses: int = 4

    # ── Cloudflare Tunnel ───────────────────────────────────────────────
    cf_tunnel_id: str = ""

    # ── API Server ──────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── API access ───────────────────────────────────────────────────────
    # An admin bearer token for the API and the dashboard (scripts, the MCP server)
    api_token: SecretStr = SecretStr("")
    cors_origins: str = "*"  # comma-separated browser origins; "" = none (live: no "*")
    # Proxies in front of the API whose X-Forwarded-For entry is trusted (0 = none)
    trusted_proxy_hops: int = 1
    # Login lockout and duplicate-order keys shared by every API replica (Upstash Redis)
    api_shared_state_enabled: bool = False
    # /docs, /redoc, /openapi.json: unset = on in paper mode, off in live
    api_docs_enabled: Optional[bool] = None
    # Web dashboard logins (Supabase Auth): "me@x.com:admin,friend@y.com:viewer"
    dashboard_users: str = ""

    # ── Risk-Adjusted Position Sizing ─────────────────────────────────
    position_sizing_enabled: bool = True
    risk_per_trade_pct: float = 0.01  # 1% of equity per trade
    atr_multiplier: float = 2.0  # Stop distance in ATR units
    atr_period: int = 14  # ATR lookback period

    # ── Confidence Gating ─────────────────────────────────────────────
    min_confidence_pct: int = 0  # 0 = disabled; e.g. 40 to reject <40%
    confidence_sizing_enabled: bool = True  # Scale position size by confidence

    # ── Sector Concentration ──────────────────────────────────────────
    max_sector_concentration_pct: float = 0.40  # Max 40% in any one sector

    # ── Position Monitor ─────────────────────────────────────────────
    monitor_poll_interval_seconds: int = 10
    monitor_hard_stop_pct: float = 0.04  # 4% hard stop (safety tier)
    monitor_eod_exit_minutes_before_close: int = 10  # sell at 15:20 IST
    monitor_trailing_stop_enabled: bool = False
    monitor_trailing_stop_pct: float = 0.02  # 2% trail from high-water
    monitor_resync_cycles: int = 3  # live: re-read broker book/positions every N polls [1, 60]
    # With the price feed: check stops and targets this often (seconds). The AI tier and
    # the broker resync keep their pace (in seconds) and REST quotes are never asked for
    # more often than monitor_poll_interval_seconds
    monitor_tick_poll_seconds: float = 1.0
    # Exit plan of each position (skopaq/execution/exit_plan.py): a target, the BUY's
    # stop-loss, partial booking at the target, then a trailing stop from breakeven.
    # rr: entry + rr × (entry − stop); pct: entry × (1 + pct); inr: profit per position
    monitor_target_mode: Literal["off", "rr", "pct", "inr"] = "rr"
    monitor_target_rr: float = 2.0       # risk:reward 1:2
    monitor_target_pct: float = 0.03     # 3 % above entry
    monitor_target_inr: float = 1000.0   # ₹1,000 profit on the whole position
    # Share of the position sold at the target (0.5 = half); 1 = all of it. The rest gets
    # a stop at breakeven and trails monitor_trailing_stop_pct below the high
    monitor_partial_booking_pct: float = 0.5
    # Plans (with their high-water mark and what was booked) survive a monitor restart
    exit_plan_dir: str = "~/.skopaq/exit_plans"
    # ── Scalper (skopaq/scalping/): intraday scalps on live ticks, INTRADAY product ──
    # Runs inside the daemon session when enabled (or alone: skopaq scalp)
    scalp_enabled: bool = False
    scalp_symbols: str = ("RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN,AXISBANK,KOTAKBANK,LT,"
                          "BHARTIARTL")
    scalp_strategies: str = "vwap_pullback,ema_rsi,orb,range_reversal"  # priority order
    scalp_candle_seconds: int = 60
    scalp_risk_per_trade_pct: float = 0.0025   # 0.25 % of equity at risk per scalp
    scalp_max_position_value_inr: float = 50_000.0
    scalp_max_trades_per_day: int = 10
    scalp_max_open: int = 2
    scalp_max_daily_loss_inr: float = 2_000.0  # no new scalps once the day lost this
    scalp_cooldown_minutes: float = 5.0        # after a losing scalp
    scalp_entry_start: str = "09:30"
    scalp_entry_end: str = "14:45"
    scalp_flatten_at: str = "15:10"            # before the broker's intraday square-off
    scalp_max_hold_minutes: float = 30.0       # time stop without profit
    scalp_rr: float = 1.5                      # target = rr × risk (ORB / range: their own)
    scalp_min_reward_to_cost: float = 2.0      # target profit ≥ this × round-trip charges
    scalp_orb_minutes: int = 15
    scalp_rest_poll_seconds: float = 3.0       # batched REST quotes when the feed is down
    # Signal qualification (skopaq/strategy/signal.py) before the risk gateway: data
    # quality always; net reward/risk after charges and assumed slippage; the regime and
    # higher-timeframe filters are off until a walk-forward backtest supports them
    scalp_qualification_enabled: bool = True
    scalp_min_net_rr: float = 1.0              # net reward / net risk, after costs + slippage
    scalp_slippage_pct: float = 0.0002         # assumed adverse slippage per side (0.02 %)
    scalp_htf_seconds: int = 300               # higher timeframe recorded as evidence (5 min)
    scalp_require_htf: bool = False            # require the higher timeframe to agree
    scalp_require_regime: bool = False         # reject when the candle regime is unreliable
    scalp_max_spread_pct: float = 0.003        # bid/ask spread over mid (REST quotes)
    scalp_max_participation: float = 0.10      # order qty / average 1-min bar volume
    # The champion model decides entries (P(net win) ≥ its threshold); off: recorded only
    scalp_model_gate_enabled: bool = False
    fno_model_gate_enabled: bool = False
    fno_min_net_rr: float = 1.0                # F&O qualification, on the premium
    fno_slippage_pct: float = 0.001            # assumed adverse premium slippage per side
    fno_qualification_enabled: bool = True
    # ── Swing book (skopaq/swing/): daily setups + the swing model (Adaptive AI) ──
    # Every swing BUY is qualified on daily candles before the executor
    swing_qualification_enabled: bool = True
    swing_min_net_rr: float = 1.0
    swing_slippage_pct: float = 0.001
    swing_notional_inr: float = 100_000.0       # sizes the charge estimate only
    swing_symbols: str = ""                     # empty: NIFTY 50
    swing_target_rr: float = 2.0                # the exit plan's target, in R
    swing_model_kind: str = "lightgbm"
    swing_train_days: int = 1825                # daily history per training run
    # Paper only: rank the setups without a swing model (no model = no swing trades)
    swing_rules_only: bool = False
    # Refuse entries while the champion adaptive model says the market is unlike its
    # training data (Dissimilarity Index above its threshold)
    block_model_outliers: bool = False

    # ── Adaptive AI (FreqAI-style, skopaq/adaptive/) ─────────────────────────
    # Retrain automatically at the end of a session when older than the interval; a new
    # model only ever becomes a candidate / shadow (promotion stays an admin's decision)
    adaptive_auto_retrain: bool = False
    adaptive_retrain_hours: float = 24.0
    adaptive_symbols: str = ""                  # empty: scalp_symbols
    adaptive_correlated: str = ""               # e.g. "HDFCBANK,ICICIBANK" (their features)
    adaptive_days: int = 30                     # history fetched per retrain
    adaptive_train_days: int = 20               # sliding training window
    adaptive_retrain_days: int = 5              # block length of the rolling backtest
    adaptive_model_kind: str = "lightgbm"       # lightgbm | xgboost | catboost | logistic
    adaptive_target: str = "forward"            # forward | setup
    adaptive_horizon_bars: int = 15
    adaptive_di_threshold: float = 1.5
    # Your strategy (features + targets): "path/to/file.py:ClassName"; empty = the default
    # one. It runs code, so it is set in .env only, never from the dashboard
    adaptive_strategy: str = ""
    # Data pipeline (skopaq/adaptive/pipeline.py)
    adaptive_scaler: str = "minmax"            # minmax | standard | robust | none
    adaptive_pca: bool = False
    adaptive_svm: bool = False
    adaptive_dbscan: bool = False
    adaptive_noise_std: float = 0.0
    adaptive_weight_factor: float = 0.0        # 0 = equal weights; e.g. 0.9 favours recent rows
    adaptive_keep_models: int = 10             # bundles kept (champion / shadow always kept)
    # Continual learning: continue the previous model (same strategy and model kind)
    # instead of training from scratch; still a candidate behind the gates
    adaptive_continual_learning: bool = False

    # ── Reinforcement learning (PPO / A2C / DQN, skopaq/adaptive/rl_*.py) ────
    # The policy trades as the scalper's "rl_policy" strategy (add it to
    # SKOPAQ_SCALP_STRATEGIES) behind every gate. Paper: the RL champion, else its shadow
    # policy. Live: the admin-approved champion only, and only with rl_allow_live
    rl_enabled: bool = False
    rl_allow_live: bool = False                # .env only (locked against overrides)
    rl_paper_use_shadow: bool = True
    rl_algo: str = "ppo"                       # ppo | a2c | dqn
    rl_timesteps: int = 60_000
    rl_days: int = 60                          # history per training run
    rl_symbols: str = ""                       # empty: adaptive_symbols, else scalp_symbols
    rl_stop_atr: float = 1.5                   # the entry's stop distance (bounded 0.4–2.5 ATR)
    rl_continual_learning: bool = False        # continue the previous policy's weights
    signal_journal_dir: str = "~/.skopaq/signals"  # every candidate + decision (learning data)
    model_dir: str = "~/.skopaq/models"        # candidate / shadow / champion models
    # Mirror signal / order / model journals to Supabase (migration 005), best effort
    audit_mirror_enabled: bool = True

    # ── Order size limits (dashboard: Control → Exits & risk) ──────────────
    # Per order, every strategy (swing, scalper, F&O engine, manual). Clamped to the
    # immutable ceilings in skopaq/constants.py (5000 shares, 20 lots)
    max_shares_per_order: int = 1000           # equity shares
    max_lots_per_order: int = 5                # F&O lots

    # ── F&O engine (skopaq/scalping/fno_engine.py): index options BUYING, INTRADAY ──
    # Bullish setups buy a CE (or the near future), bearish ones a PE. Never a SELL to
    # open: no option writing, no short futures. Runs inside the daemon session when
    # enabled (or alone: skopaq fno)
    fno_enabled: bool = False
    fno_underlyings: str = "NIFTY"             # NIFTY, BANKNIFTY, FINNIFTY, SENSEX, stocks
    fno_instrument: str = "options"            # options | futures (long only)
    fno_strategies: str = "vwap_pullback,ema_rsi,orb,range_reversal"
    fno_allow_bearish: bool = True             # buy PEs on bearish setups
    fno_expiry_index: int = 0                  # 0 = nearest expiry
    fno_avoid_expiry_day: bool = True          # on expiry day trade the next expiry
    fno_strike_offset: int = 0                 # 0 ATM, -1 one strike ITM, +1 one OTM
    fno_max_spread_pct: float = 0.03           # skip a contract with a wider bid-ask
    fno_risk_per_trade_inr: float = 3_000.0    # ₹ lost if the premium stop is hit
    fno_max_lots: int = 1                      # lots per trade (safety cap: 5)
    fno_max_premium_inr: float = 25_000.0      # ₹ premium per trade
    fno_premium_stop_pct: float = 0.25         # premium stop: 25 % below the entry
    fno_trail_pct: float = 0.15                # after +1 R: trail 15 % below the high
    fno_max_trades_per_day: int = 4
    fno_max_open: int = 1
    fno_max_daily_loss_inr: float = 6_000.0    # no new F&O trades once the day lost this
    fno_cooldown_minutes: float = 10.0         # after a losing trade
    fno_entry_start: str = "09:30"
    fno_entry_end: str = "14:30"
    fno_flatten_at: str = "15:10"              # before the broker's intraday square-off
    fno_max_hold_minutes: float = 30.0         # time stop without profit
    fno_min_reward_to_cost: float = 3.0        # expected profit ≥ this × round-trip charges
    fno_candle_seconds: int = 60
    fno_rest_poll_seconds: float = 3.0
    fno_rr: float = 2.0                        # underlying target = rr × risk
    fno_orb_minutes: int = 15

    # Dashboard control: status, stop/start requests and commands (shared home volume)
    control_dir: str = "~/.skopaq/control"
    alerts_file: str = "~/.skopaq/alerts.json"  # price alerts + the dashboard notification feed

    # ── Daemon (autonomous session) ──────────────────────────────────
    daemon_max_trades_per_session: int = 3  # Max BUY orders per day
    daemon_max_candidates_to_analyze: int = 5  # Top N swing ideas to try
    daemon_pre_open_minutes: int = 5  # Start N minutes before 9:15
    daemon_scan_delay_after_open_seconds: int = 60  # Wait for prices to settle
    daemon_session_log_dir: str = "logs/daemon"  # Session log directory
    daemon_heartbeat_interval_seconds: int = 300  # Heartbeat log interval (5 min)

    # ── Scheduler (always-on host: docker compose `scheduler`) ─────────
    # Plain str, not bool/int/Literal: every service builds a SkopaqConfig, so a typo here
    # would stop api and telegram too. ScheduleSettings.from_config parses them, and a bad
    # value stops only the scheduler (a bad CONFIRM_LIVE counts as not confirmed).
    scheduler_enabled: str = "true"  # false: stay up and healthy, start no sessions (cutover)
    scheduler_mode: str = "paper"  # paper | live (live also needs scheduler_confirm_live)
    scheduler_confirm_live: str = "false"  # true: like --confirm-live
    scheduler_start: str = "09:15"  # IST, first daemon launch (scan follows the scan delay)
    scheduler_last_start: str = "11:30"  # IST, end of the catch-up window if the host was down
    scheduler_deadline: str = "15:45"  # IST, SIGTERM a session still running (EOD exit is 15:20)
    # IST, alert if the INDstocks token is missing or expires before the session ends; "" = off
    scheduler_preflight: str = "08:45"
    scheduler_poll_seconds: str = "30"
    scheduler_kill_after_seconds: str = "300"  # SIGKILL this long after the deadline/stop SIGTERM
    scheduler_state_dir: str = "~/scheduler"  # at-most-once markers (on the home volume)
    # Optional dead-man's switch: GET URL on success, URL/fail on failure
    scheduler_ping_url: str = ""
    heartbeat_file: str = ""  # touched by long-running services for container health checks
    nse_holidays: str = ""  # extra NSE closures, comma-separated YYYY-MM-DD

    # ── Regime Detection ──────────────────────────────────────────────
    regime_detection_enabled: bool = False  # Off until tested with live data

    # ── Asset Class ──────────────────────────────────────────────────────
    asset_class: Literal["equity", "crypto"] = "equity"
    crypto_quote_currency: str = "USDT"
    binance_base_url: str = "https://api.binance.com"

    # ── Crypto Exchange (Live Trading) ──────────────────────────────────────
    binance_api_key: SecretStr = SecretStr("")
    binance_api_secret: SecretStr = SecretStr("")
    binance_testnet: bool = True  # Default to testnet for safety

    # ── Multi-Exchange Support ──────────────────────────────────────────────
    preferred_exchange: str = "binance"  # binance, coinbase, kraken (future)

    # ── Blockchain / On-Chain ────────────────────────────────────────────────
    whale_alert_threshold_usd: int = 100000  # $100K min for whale alerts
    gas_alert_enabled: bool = False
    gas_alert_threshold_gwei: float = 100.0  # Alert when gas exceeds this

    # ── WebSocket ───────────────────────────────────────────────────────────
    ws_reconnect_enabled: bool = True
    ws_reconnect_delay_seconds: float = 5.0

    # ── Telegram Bot ────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = SecretStr("")
    # Comma-separated chat IDs allowed to use the bot, besides
    # SKOPAQ_TELEGRAM_CHAT_ID; none = nobody (/start replies with the chat ID).
    telegram_allowed_chat_ids: str = ""

    # ── Database (Fly.io Postgres) ──────────────────────────────────────────
    database_url: str = ""  # Set by Fly.io attachment or manually

    # ── Logging ─────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @field_validator(*_LENIENT_FIELDS, mode="wrap")
    @classmethod
    def _default_if_malformed(cls, value: Any, handler: ValidatorFunctionWrapHandler,
                              info: ValidationInfo) -> Any:
        """A malformed live order setting becomes its default, with a WARNING."""
        default = cls.model_fields[info.field_name].default
        try:
            parsed = handler(value)
        except ValidationError:
            parsed = None
        else:
            if not (isinstance(parsed, float) and not math.isfinite(parsed)):
                return parsed
        logger.warning("SKOPAQ_%s=%r is not a valid value; using the default %r",
                       info.field_name.upper(), value, default)
        return default
