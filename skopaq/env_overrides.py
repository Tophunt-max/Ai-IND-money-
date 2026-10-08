"""Environment overrides edited from the web dashboard (Settings → Environment).

An admin can set ``SKOPAQ_*`` settings from the dashboard without editing the ``ENV_FILE``
GitHub secret and redeploying. They are kept in a JSON file on the shared home volume
(``~/.skopaq/env_overrides.json``, mode 600; ``SKOPAQ_ENV_OVERRIDES_FILE`` moves it), so
every container sees them and a deploy does not overwrite them. An override **wins over**
the server's environment (``.env`` / ``ENV_FILE``); removing it brings the server's value
back.

Every process applies the file into ``os.environ`` when it starts (``apply()``: the
``skopaq`` CLI, so api, scheduler, daemon, monitor; the Telegram bot; the MCP server), and
``SkopaqConfig()`` then reads it like any other variable. The api applies a change at once,
the scheduler re-reads the file between sessions (``scheduler.reload_settings``), each
daemon session and ``skopaq monitor`` read it when they start, and the Telegram bot on its
next restart.

Not every setting may be changed this way. Logins and API access (Supabase, the API token,
CORS, dashboard users), the bind address and the state directories are **locked**: a
mistake there would lock the dashboard out or split the shared state. So is
``SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK`` (dangerous) and ``SKOPAQ_TRADING_HALTED`` (use the
kill switch). Those stay in ``ENV_FILE``.

Secret values (API keys, tokens) are never sent back to the browser: only whether they are
set. Each change is appended to ``env_overrides.log`` beside the file (who, when, which keys;
values of non-secret keys only).
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import typing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import SecretStr, TypeAdapter, ValidationError

logger = logging.getLogger(__name__)

PREFIX = "SKOPAQ_"
FILE_ENV = "SKOPAQ_ENV_OVERRIDES_FILE"
_DEFAULT_FILE = "~/.skopaq/env_overrides.json"
MAX_VALUE_LEN = 4096
HISTORY_LIMIT = 50

# SkopaqConfig fields the dashboard may not change (see the module docstring).
LOCKED: dict[str, str] = {
    "supabase_url": "dashboard login",
    "supabase_anon_key": "dashboard login",
    "supabase_service_key": "dashboard login and database",
    "api_token": "API access",
    "cors_origins": "API access from the browser",
    "dashboard_users": "dashboard logins and roles",
    "api_host": "API bind address",
    "api_port": "API bind address",
    "database_url": "database",
    "order_lock_dir": "shared state directory",
    "order_journal_dir": "shared state directory",
    "exit_plan_dir": "shared state directory",
    "scheduler_state_dir": "shared state directory",
    "daemon_session_log_dir": "shared state directory",
    "heartbeat_file": "container health checks",
    "allow_sell_without_order_book": "dangerous: may sell the same shares twice",
    "trading_halted": "use the kill switch instead",
}

# Read straight from os.environ by the code, not SkopaqConfig fields.
_EXTRA: dict[str, str] = {
    "telegram_chat_id": "Telegram chat that receives alerts and trade notifications",
}

# Plain str fields in SkopaqConfig that hold a bool or a choice (scheduler: a typo must
# stop only the scheduler, so they are parsed there).
_STR_BOOLS = {"scheduler_enabled", "scheduler_confirm_live"}
_STR_CHOICES = {"scheduler_mode": ("paper", "live")}
_SCHEDULER_KEYS_PREFIX = "scheduler_"
_SCHEDULER_RELATED = {"nse_holidays", "monitor_eod_exit_minutes_before_close"}

_HELP: dict[str, str] = {
    "trading_mode": "paper = simulated orders, live = real money on INDstocks",
    "initial_paper_capital": "Paper account capital (INR)",
    "scheduler_enabled": "false: the scheduler stays up but starts no sessions",
    "scheduler_mode": "Mode of the daily auto-trading session (live also needs confirm live)",
    "scheduler_confirm_live": "true is required for live sessions (real money)",
    "scheduler_start": "IST HH:MM, first daemon launch",
    "scheduler_last_start": "IST HH:MM, end of the catch-up window",
    "scheduler_deadline": "IST HH:MM, a session still running is stopped",
    "scheduler_settle_at": "IST HH:MM, settle past decisions; empty = off",
    "scheduler_preflight": "IST HH:MM, token check before the session; empty = off",
    "nse_holidays": "Extra NSE closures, comma-separated YYYY-MM-DD",
    "indstocks_token": "INDstocks API token (or `skopaq token set`)",
    "telegram_bot_token": "Telegram bot token from @BotFather",
    "telegram_allowed_chat_ids": "Comma-separated chat IDs allowed to use the bot",
    "daemon_max_trades_per_session": "Max BUY orders per daily session",
    "daemon_max_candidates_to_analyze": "Top N scanner picks analysed per session",
    "scanner_max_candidates": "Candidates the scanner returns",
    "risk_per_trade_pct": "Fraction of equity risked per trade (0.01 = 1%)",
    "min_confidence_pct": "Reject signals below this confidence; 0 = off",
    "max_sector_concentration_pct": "Max fraction of the portfolio in one sector",
    "monitor_hard_stop_pct": "Hard stop-loss (0.04 = 4%)",
    "monitor_trailing_stop_enabled": "Trailing stop on open positions",
    "monitor_trailing_stop_pct": "Trailing stop (0.02 = 2%); also the rest after a target",
    "monitor_target_mode": "Target per position: rr (risk:reward), pct, inr (₹ profit), off",
    "monitor_target_rr": "rr mode: target = entry + this × (entry − stop); 2 = 1:2",
    "monitor_target_pct": "pct mode: target this far above entry (0.03 = 3%)",
    "monitor_target_inr": "inr mode: target = this much profit on the whole position (₹)",
    "monitor_partial_booking_pct": ("Share sold at the target (0.5 = half, 1 = all); the rest "
                                    "trails from breakeven"),
    "custom_llm_base_url": "OpenAI-compatible AI gateway, e.g. https://codecraftapi.com/v1",
    "custom_llm_api_key": "API key of that gateway",
    "custom_llm_model": "Model for analysts, debaters and trader (as the gateway names it)",
    "custom_llm_judge_model": "Model for the judges and chat; empty = the model above",
    "google_api_key": "Gemini (most agents)",
    "anthropic_api_key": "Claude (judges)",
    "openrouter_api_key": "OpenRouter (Grok, Perplexity)",
}

_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Trading mode", ("trading_mode", "initial_paper_capital", "asset_class")),
    ("Scheduler", ("scheduler_", "nse_holidays")),
    ("Broker (INDstocks)", ("indstocks_", "order_")),
    ("Telegram", ("telegram_",)),
    ("Daemon, scanner & monitor", ("daemon_", "scanner_", "monitor_")),
    ("Risk & sizing", ("position_sizing", "risk_per_trade", "atr_", "min_confidence",
                       "confidence_sizing", "max_sector", "regime_")),
    ("AI models & keys", ("custom_llm_", "google_", "anthropic_", "perplexity_", "xai_", "openrouter_",
                          "typesafe_", "jev_", "ollama_", "langcache_", "max_debate",
                          "max_risk", "selected_analysts", "reflection_")),
)

_lock = threading.Lock()  # os.environ updates
_write_lock = threading.Lock()  # read-modify-write of the file (one api process)
_base: dict[str, Optional[str]] = {}  # env var → its value before any override (None: unset)
_applied: dict[str, str] = {}  # what apply() last put into os.environ


# ── Registry ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Spec:
    """One editable (or locked) setting."""

    name: str  # SkopaqConfig field name (lower case), or an _EXTRA name
    kind: Literal["bool", "int", "float", "choice", "text"]
    secret: bool = False
    choices: tuple[str, ...] = ()
    default: str = ""
    group: str = "Other"
    help: str = ""
    locked: str = ""  # why it is locked; "" = editable
    annotation: Any = None  # the field's type (None for _EXTRA)

    @property
    def env(self) -> str:
        return PREFIX + self.name.upper()

    def public(self) -> dict[str, Any]:
        return {"key": self.env, "kind": self.kind, "secret": self.secret,
                "choices": list(self.choices), "default": "" if self.secret else self.default,
                "group": self.group, "help": self.help, "locked": self.locked}


def _group(name: str) -> str:
    for title, prefixes in _GROUPS:
        if any(name == p or name.startswith(p) for p in prefixes):
            return title
    return "Other"


def _kind(name: str, annotation: Any) -> tuple[str, tuple[str, ...]]:
    if name in _STR_BOOLS:
        return "bool", ()
    if name in _STR_CHOICES:
        return "choice", _STR_CHOICES[name]
    if typing.get_origin(annotation) is Literal:
        return "choice", tuple(str(a) for a in typing.get_args(annotation))
    if annotation is bool:
        return "bool", ()
    if annotation is int:
        return "int", ()
    if annotation is float:
        return "float", ()
    return "text", ()


@lru_cache(maxsize=1)
def registry() -> dict[str, Spec]:
    """Every SKOPAQ_* setting the dashboard shows, by env var name."""
    from skopaq.config import SkopaqConfig

    specs: dict[str, Spec] = {}
    for name, info in SkopaqConfig.model_fields.items():
        annotation = info.annotation
        secret = annotation is SecretStr
        kind, choices = _kind(name, annotation)
        default = info.default
        if isinstance(default, SecretStr):
            default = ""
        elif isinstance(default, bool):
            default = "true" if default else "false"
        spec = Spec(name=name, kind=kind, secret=secret, choices=choices,
                    default="" if default is None else str(default), group=_group(name),
                    help=_HELP.get(name, ""), locked=LOCKED.get(name, ""),
                    annotation=annotation)
        specs[spec.env] = spec
    for name, text in _EXTRA.items():
        spec = Spec(name=name, kind="text", group=_group(name), help=text)
        specs[spec.env] = spec
    return specs


def editable(key: str) -> Spec:
    """The spec of an editable *key* (``SKOPAQ_...``); ValueError otherwise."""
    spec = registry().get((key or "").strip().upper())
    if spec is None:
        raise ValueError(f"{key!r} is not a SkopaqTrader setting")
    if spec.locked:
        raise ValueError(f"{spec.env} cannot be changed from the dashboard ({spec.locked}); "
                         "set it in ENV_FILE")
    return spec


# ── File ──────────────────────────────────────────────────────────────────────


def overrides_file() -> Path:
    return Path(os.environ.get(FILE_ENV) or _DEFAULT_FILE).expanduser()


def history_file() -> Path:
    return overrides_file().with_suffix(".log")


def load() -> dict[str, str]:
    """The saved overrides (env var → value); {} when there are none or the file is bad."""
    path = overrides_file()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        logger.error("Cannot read %s: dashboard settings not applied", path, exc_info=True)
        return {}
    try:
        values = json.loads(raw).get("values", {})
        if not isinstance(values, dict):
            raise ValueError("'values' is not an object")
    except (ValueError, AttributeError) as exc:
        logger.error("%s is not valid (%s): dashboard settings not applied", path, exc)
        return {}
    specs = registry()
    out: dict[str, str] = {}
    for key, value in values.items():
        spec = specs.get(str(key).upper())
        if spec is None or spec.locked or not isinstance(value, str):
            logger.warning("%s: ignoring %s (not an editable setting)", path, key)
            continue
        out[spec.env] = value
    return out


def _write(values: dict[str, str], by: str) -> None:
    path = overrides_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"values": values, "updated_at": _now(), "updated_by": by},
                      indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env_overrides.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(body + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _audit(entry: dict[str, Any]) -> None:
    path = history_file()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        logger.error("Cannot append to %s", path, exc_info=True)


def history(limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    """The latest changes, newest first."""
    try:
        lines = history_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Apply ─────────────────────────────────────────────────────────────────────


def apply(values: Optional[dict[str, str]] = None) -> bool:
    """Put the overrides into ``os.environ`` (they win over the server's environment);
    overrides removed since the last call get their original value back. Returns whether
    anything changed. Never raises: a bad file is logged and ignored."""
    global _applied
    try:
        with _lock:
            if values is None:
                values = load()
            for key in set(_applied) - set(values):
                original = _base.pop(key, None)
                if original is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original
            for key, value in values.items():
                if key not in _base:
                    _base[key] = os.environ.get(key)
                os.environ[key] = value
            changed = values != _applied
            if changed and values:
                logger.info("Dashboard settings applied: %s", ", ".join(sorted(values)))
            _applied = dict(values)
            return changed
    except Exception:
        logger.exception("Could not apply the dashboard settings")
        return False


def server_value(key: str) -> Optional[str]:
    """*key*'s value without the dashboard override: the process env or ``.env``."""
    if key in _base:
        if _base[key] is not None:
            return _base[key]
    elif key in os.environ:
        return os.environ[key]
    return _dotenv().get(key)


def _dotenv() -> dict[str, str]:
    try:
        from dotenv import dotenv_values

        return {k.upper(): v for k, v in dotenv_values(".env").items() if v is not None}
    except Exception:
        return {}


# ── Validation ────────────────────────────────────────────────────────────────


def normalize(spec: Spec, value: Any) -> str:
    """*value* checked against *spec*'s type, as stored ("true"/"false" for bools)."""
    from skopaq.execution.scheduler import _parse_bool

    text = "" if value is None else str(value)
    if len(text) > MAX_VALUE_LEN:
        raise ValueError(f"{spec.env}: at most {MAX_VALUE_LEN} characters")
    if any(c in text for c in "\r\n\x00"):
        raise ValueError(f"{spec.env}: must be a single line")
    text = text.strip()
    if spec.kind == "bool":
        parsed = _parse_bool(text)
        if parsed is None:
            raise ValueError(f"{spec.env}: expected true or false, got {text!r}")
        return "true" if parsed else "false"
    if spec.kind == "choice":
        lowered = text.lower()
        if lowered not in spec.choices:
            raise ValueError(f"{spec.env}: expected one of {', '.join(spec.choices)}, "
                             f"got {text!r}")
        return lowered
    if spec.kind in ("int", "float"):
        try:
            number = TypeAdapter(spec.annotation).validate_python(text)
        except ValidationError:
            raise ValueError(f"{spec.env}: expected a {'whole ' if spec.kind == 'int' else ''}"
                             f"number, got {text!r}") from None
        if isinstance(number, float) and not math.isfinite(number):
            raise ValueError(f"{spec.env}: expected a finite number, got {text!r}")
        return text
    return text


def candidate_config(values: dict[str, str], removed: list[str] = ()):
    """SkopaqConfig as it would be with *values* as the overrides and *removed* dropped
    (their server value, else the default). Init kwargs beat the environment."""
    from skopaq.config import SkopaqConfig

    specs = registry()
    kwargs: dict[str, Any] = {}
    for key in removed:
        spec = specs.get(key)
        if spec is not None and spec.annotation is not None:
            server = server_value(key)
            kwargs[spec.name] = server if server is not None else (
                SkopaqConfig.model_fields[spec.name].default)
    for key, value in values.items():
        spec = specs.get(key)
        if spec is not None and spec.annotation is not None:
            kwargs[spec.name] = value
    try:
        return SkopaqConfig(**kwargs)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err.get("loc", ()))
        raise ValueError(f"Invalid setting {PREFIX}{where.upper()}: {err.get('msg')}") from None


def check(config, changed: set[str]) -> None:
    """The scheduler's settings are checked together (START < LAST_START < DEADLINE, ...)
    when one of them changes; ValueError otherwise."""
    specs = registry()
    if any(specs[k].name.startswith(_SCHEDULER_KEYS_PREFIX)
           or specs[k].name in _SCHEDULER_RELATED for k in changed if k in specs):
        from skopaq.execution.scheduler import ScheduleSettings

        ScheduleSettings.from_config(config)


def _live_flags(config) -> dict[str, bool]:
    from skopaq.execution.scheduler import _parse_bool

    return {
        "SKOPAQ_TRADING_MODE=live": config.trading_mode == "live",
        "SKOPAQ_SCHEDULER_MODE=live": (config.scheduler_mode or "").strip().lower() == "live",
        "SKOPAQ_SCHEDULER_CONFIRM_LIVE=true": bool(_parse_bool(config.scheduler_confirm_live)),
    }


def live_switches(before, after) -> list[str]:
    """Real-money switches that are off in config *before* and on in *after*."""
    old, new = _live_flags(before), _live_flags(after)
    return [name for name, on in new.items() if on and not old[name]]


# ── Read / change ─────────────────────────────────────────────────────────────


def describe() -> list[dict[str, Any]]:
    """Every setting with its current value and where it comes from (secrets: only
    whether they are set)."""
    from skopaq.config import SkopaqConfig

    apply()  # pick up a change another process made
    overrides = load()
    config = SkopaqConfig()
    out = []
    for key, spec in registry().items():
        if spec.annotation is not None:
            value = getattr(config, spec.name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            elif isinstance(value, bool):
                value = "true" if value else "false"
            value = "" if value is None else str(value)
        else:
            value = os.environ.get(key, "")
        source = ("dashboard" if key in overrides
                  else "server" if server_value(key) is not None else "default")
        item = spec.public()
        item.update({"source": source, "is_set": value != "",
                     "value": None if spec.secret else value,
                     "server_set": server_value(key) is not None})
        out.append(item)
    return out


@dataclass
class Change:
    set: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    live: list[str] = field(default_factory=list)


def change(set_values: dict[str, Any], remove: list[str], *, by: str,
           confirm_live: bool = False) -> Change:
    """Save overrides: *set_values* added or replaced, *remove* dropped. Validates
    everything first (ValueError: nothing saved). Turning live trading on needs
    *confirm_live* (PermissionError otherwise). Applies the result to this process."""
    normalized: dict[str, str] = {}
    for key, value in (set_values or {}).items():
        spec = editable(key)
        normalized[spec.env] = normalize(spec, value)
    dropped = [editable(k).env for k in (remove or [])]
    clash = set(normalized) & set(dropped)
    if clash:
        raise ValueError(f"Both set and removed: {', '.join(sorted(clash))}")
    if not normalized and not dropped:
        raise ValueError("Nothing to change")

    with _write_lock:
        current = load()
        new = {k: v for k, v in current.items() if k not in dropped}
        new.update(normalized)
        set_keys = sorted(k for k, v in normalized.items() if current.get(k) != v)
        removed = sorted(k for k in dropped if k in current)
        if not set_keys and not removed:
            return Change()
        from skopaq.config import SkopaqConfig

        apply(current)  # this process's env matches the file before comparing
        after = candidate_config(new, removed)
        check(after, set(set_keys) | set(removed))
        live = live_switches(SkopaqConfig(), after)
        if live and not confirm_live:
            raise PermissionError("Turning on live trading (real money) needs confirmation: "
                                  + ", ".join(live))
        _write(new, by)
        specs = registry()
        _audit({
            "at": _now(), "by": by, "removed": removed, "live": live,
            "set": {k: ("(secret)" if specs[k].secret else new[k]) for k in set_keys},
        })
        apply(new)
    level = logging.WARNING if live else logging.INFO
    logger.log(level, "Dashboard settings changed by %s: set %s, removed %s%s", by,
               ", ".join(set_keys) or "-", ", ".join(removed) or "-",
               f" — LIVE TRADING ON ({', '.join(live)})" if live else "")
    return Change(set=set_keys, removed=removed, live=live)


def reset_for_tests() -> None:
    """Forget what was applied (tests only; does not touch os.environ)."""
    global _applied
    with _lock:
        _base.clear()
        _applied = {}
    registry.cache_clear()
