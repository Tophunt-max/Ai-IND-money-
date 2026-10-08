"""Shared test fixtures."""

import os

import pytest

# Tests never reach production state or people, even when run with a real .env loaded
# (docker compose env_file, --env-file, or tradingagents' load_dotenv of ./.env): a halt
# written to Supabase system_flags stops every BUY everywhere, and notify() sends Telegram
# messages. Overwrite, never setdefault; set to "" rather than pop, because load_dotenv
# (override=False) and pydantic's env_file only fill variables that are unset.
# Tests that need a value set it with monkeypatch.
os.environ["SKOPAQ_TRADING_MODE"] = "paper"
for _var in (
    "SKOPAQ_SUPABASE_URL",
    "SKOPAQ_SUPABASE_ANON_KEY",
    "SKOPAQ_SUPABASE_SERVICE_KEY",
    "SKOPAQ_TELEGRAM_BOT_TOKEN",
    "SKOPAQ_TELEGRAM_CHAT_ID",
    "SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS",
    "SKOPAQ_UPSTASH_REDIS_URL",
    "SKOPAQ_UPSTASH_REDIS_TOKEN",
    "SKOPAQ_REDIS_URL",
    "REDIS_URL",
    "SKOPAQ_DATABASE_URL",
    "DATABASE_URL",
    "SKOPAQ_API_TOKEN",
    "SKOPAQ_INDSTOCKS_TOKEN",
):
    os.environ[_var] = ""


# The kill switch reads a halt file from the home directory; point it at a
# path that does not exist so a real `skopaq halt` never leaks into tests.
os.environ["SKOPAQ_HALT_FILE"] = os.path.join(
    os.path.dirname(__file__), ".no-such-dir", "HALT-for-tests"
)
os.environ.pop("SKOPAQ_TRADING_HALTED", None)
# Settings saved from the dashboard (~/.skopaq/env_overrides.json) never reach tests either.
# Exit plans a test makes through a real SkopaqConfig go to a throwaway directory
import tempfile as _tempfile  # noqa: E402

os.environ["SKOPAQ_EXIT_PLAN_DIR"] = _tempfile.mkdtemp(prefix="skopaq-exit-plans-")
os.environ["SKOPAQ_CONTROL_DIR"] = _tempfile.mkdtemp(prefix="skopaq-control-")
os.environ["SKOPAQ_ENV_OVERRIDES_FILE"] = os.path.join(
    os.path.dirname(__file__), ".no-such-dir", "env-overrides-for-tests.json"
)

@pytest.fixture(autouse=True)
def _fresh_kill_switch():
    """Each test sees the kill switch uncached."""
    from skopaq.execution import kill_switch

    kill_switch._cache = None
    yield
    kill_switch._cache = None


@pytest.fixture(autouse=True)
def _isolated_order_state(tmp_path, monkeypatch):
    """Live order state stays in the test: the order journal and SELL locks under tmp_path
    (never ~/.skopaq), and a fresh process-wide alerter (no dedup memory from another test)."""
    from skopaq.execution import order_alerts

    monkeypatch.setenv("SKOPAQ_ORDER_JOURNAL_DIR", str(tmp_path / "orders"))
    monkeypatch.setenv("SKOPAQ_ORDER_LOCK_DIR", str(tmp_path / "locks"))
    order_alerts.reset_alerter()
    yield
    order_alerts.reset_alerter()


@pytest.fixture(autouse=True)
def _fresh_order_status_notes():
    """order_status logs an unrecognised status once per order; no test inherits another's."""
    from skopaq.broker import order_status

    order_status._noted.clear()
    yield
    order_status._noted.clear()
