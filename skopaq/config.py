"""SkopaqTrader configuration — loaded from environment variables and .env file."""

from __future__ import annotations

import logging
import math
from typing import Any, Literal

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

    # ── LLM API Keys ───────────────────────────────────────────────────
    google_api_key: SecretStr = SecretStr("")  # Gemini Flash (scanner)
    anthropic_api_key: SecretStr = SecretStr("")  # Claude Sonnet (analysis)
    perplexity_api_key: SecretStr = SecretStr("")  # Sonar (news)
    xai_api_key: SecretStr = SecretStr("")  # Grok (sentiment)
    openrouter_api_key: SecretStr = SecretStr("")  # OpenRouter (Grok + Perplexity)
    typesafe_api_key: SecretStr = SecretStr("")  # TypeSafe Jev (post screening, decisions)

    # ── Custom OpenAI-compatible LLM endpoint (skopaq/llm/model_tier.py) ──
    # Any gateway speaking the OpenAI Chat Completions API (e.g. CodeCraft API). With base
    # URL, key and model set, every agent role uses it first; the providers above stay as
    # fallbacks when it is not configured.
    custom_llm_base_url: str = ""
    custom_llm_api_key: SecretStr = SecretStr("")
    custom_llm_model: str = ""  # analysts, researchers, trader, debaters, sell analyst
    custom_llm_judge_model: str = ""  # research / portfolio manager, chat; "" = custom_llm_model

    # ── TypeSafe Jev (calibrated decisions; skopaq/llm/jev.py) ─────────
    jev_enabled: bool = False  # Jev confidence on entries + exit gate
    jev_model: str = "jev-1.13.0"  # pinned: thresholds are tuned per version
    jev_min_confidence: float = 0.6  # act on a Jev answer at or above this
    jev_min_catalyst_score: float = 0.0  # scanner: drop below this (0-3); 0 ranks only
    jev_timeout_seconds: float = 5.0
    # TypeSafe-compatible API root; "" = api.typesafe.ai. OpenRouter serves Jev
    # at https://openrouter.ai/api (OpenRouter key, SKOPAQ_JEV_MODEL=jev-1.13;
    # not confirmed to be the same build as jev-1.13.0).
    jev_base_url: str = ""

    # ── Cloudflare Tunnel ───────────────────────────────────────────────
    cf_tunnel_id: str = ""

    # ── Scanner ────────────────────────────────────────────────────────
    scanner_enabled: bool = False
    scanner_cycle_seconds: int = 30
    scanner_max_candidates: int = 5

    # ── API Server ──────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── API access ───────────────────────────────────────────────────────
    # When set, /api/chat/* require Bearer auth (and it works as an admin dashboard login)
    api_token: SecretStr = SecretStr("")
    cors_origins: str = "*"  # comma-separated browser origins; "" = none
    # Web dashboard logins (Supabase Auth): "me@x.com:admin,friend@y.com:viewer"
    dashboard_users: str = ""

    # ── Reflection / Memory ─────────────────────────────────────────────
    reflection_enabled: bool = True
    reflection_max_memory_entries: int = 50

    # ── Upstream Agent Tuning ─────────────────────────────────────────
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    selected_analysts: str = "market,social,news,fundamentals"
    google_thinking_level: str = ""

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
    monitor_ai_interval_cycles: int = 6  # AI every 6 polls (~60s)
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

    # ── Daemon (autonomous session) ──────────────────────────────────
    daemon_max_trades_per_session: int = 3  # Max BUY orders per day
    daemon_max_candidates_to_analyze: int = 5  # Top N scanner picks to analyze
    daemon_pre_open_minutes: int = 5  # Start N minutes before 9:15
    daemon_scan_delay_after_open_seconds: int = 60  # Wait for prices to settle
    daemon_min_profit_threshold_pct: float = 0.5  # Min P&L% for AI sell
    daemon_min_profit_threshold_inr: float = (
        150.0  # Min absolute profit (INR, covers ~₹120 brokerage)
    )
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
    scheduler_settle_at: str = "18:30"  # IST, `skopaq settle` backstop; "" = off
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

    # ── Semantic Cache (Redis LangCache) ────────────────────────────────
    langcache_enabled: bool = False
    langcache_api_key: SecretStr = SecretStr("")
    langcache_server_url: str = ""
    langcache_cache_id: str = ""
    langcache_threshold: float = 0.90  # Cosine similarity threshold (0–1)

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

    # ── Ollama (Local LLM Fallback) ──────────────────────────────────────────
    ollama_enabled: bool = False        # Opt-in: set True to use local models
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""              # Auto-detect if empty

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
