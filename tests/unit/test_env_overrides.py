"""Dashboard environment overrides (skopaq/env_overrides.py): validation, locked keys, the
live-trading confirmation, applying to os.environ, secrets and the change log."""

from __future__ import annotations

import json
import os
import stat

import pytest

from skopaq import env_overrides as eo
from skopaq.config import SkopaqConfig

BY = "dashboard:boss@example.com"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh overrides file; paper mode on the server; os.environ restored afterwards."""
    monkeypatch.chdir(tmp_path)  # no .env from the repo
    path = tmp_path / "home" / "env_overrides.json"
    monkeypatch.setenv(eo.FILE_ENV, str(path))
    monkeypatch.setenv("SKOPAQ_TRADING_MODE", "paper")
    for key in ("SKOPAQ_SCHEDULER_MODE", "SKOPAQ_SCHEDULER_CONFIRM_LIVE",
                "SKOPAQ_SCANNER_MAX_CANDIDATES", "SKOPAQ_SCHEDULER_START",
                "SKOPAQ_SCHEDULER_ENABLED", "SKOPAQ_GOOGLE_API_KEY", "SKOPAQ_TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    eo.reset_for_tests()
    yield path
    eo.apply({})  # put back what the overrides replaced, before monkeypatch undoes its own
    eo.reset_for_tests()


def _saved(path) -> dict:
    return json.loads(path.read_text())["values"]


# ── Keys ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["SKOPAQ_SUPABASE_URL", "SKOPAQ_API_TOKEN", "SKOPAQ_CORS_ORIGINS",
                                 "SKOPAQ_DASHBOARD_USERS", "SKOPAQ_TRADING_HALTED",
                                 "SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK",
                                 "SKOPAQ_SCHEDULER_STATE_DIR"])
def test_locked_keys_are_refused(store, key):
    with pytest.raises(ValueError, match="cannot be changed from the dashboard"):
        eo.change({key: "x"}, [], by=BY)
    assert not store.exists()


def test_unknown_keys_are_refused(store):
    with pytest.raises(ValueError, match="not a SkopaqTrader setting"):
        eo.change({"PATH": "/tmp"}, [], by=BY)
    with pytest.raises(ValueError, match="not a SkopaqTrader setting"):
        eo.change({"SKOPAQ_NO_SUCH_THING": "1"}, [], by=BY)


def test_env_only_key_is_editable(store):
    eo.change({"SKOPAQ_TELEGRAM_CHAT_ID": "12345"}, [], by=BY)
    assert os.environ["SKOPAQ_TELEGRAM_CHAT_ID"] == "12345"


# ── Validation ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key,value,match", [
    ("SKOPAQ_SCANNER_MAX_CANDIDATES", "abc", "whole number"),
    ("SKOPAQ_SCANNER_MAX_CANDIDATES", "2.5", "whole number"),
    ("SKOPAQ_RISK_PER_TRADE_PCT", "nan", "finite"),
    ("SKOPAQ_TRADING_MODE", "real", "expected one of paper, live"),
    ("SKOPAQ_SCHEDULER_MODE", "maybe", "expected one of paper, live"),
    ("SKOPAQ_SCHEDULER_ENABLED", "perhaps", "expected true or false"),
    ("SKOPAQ_SCHEDULER_START", "9:15", "HH:MM"),
    ("SKOPAQ_SCHEDULER_START", "12:00", "START < SKOPAQ_SCHEDULER_LAST_START"),
    ("SKOPAQ_NSE_HOLIDAYS", "tomorrow", "YYYY-MM-DD"),
    ("SKOPAQ_SELECTED_ANALYSTS", "a\nb", "single line"),
])
def test_bad_values_save_nothing(store, key, value, match):
    with pytest.raises(ValueError, match=match):
        eo.change({key: value}, [], by=BY)
    assert not store.exists()


def test_values_are_normalized(store):
    eo.change({"SKOPAQ_SCHEDULER_ENABLED": " Yes ", "SKOPAQ_SCANNER_MAX_CANDIDATES": " 7 "},
              [], by=BY)
    assert _saved(store) == {"SKOPAQ_SCHEDULER_ENABLED": "true",
                             "SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}
    assert SkopaqConfig().scanner_max_candidates == 7


def test_one_bad_value_keeps_the_good_ones_out_too(store):
    with pytest.raises(ValueError):
        eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "7",
                   "SKOPAQ_SCHEDULER_MODE": "nope"}, [], by=BY)
    assert not store.exists()
    assert "SKOPAQ_SCANNER_MAX_CANDIDATES" not in os.environ


def test_set_and_remove_the_same_key_is_refused(store):
    with pytest.raises(ValueError, match="Both set and removed"):
        eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}, ["SKOPAQ_SCANNER_MAX_CANDIDATES"],
                  by=BY)


def test_nothing_to_change(store):
    with pytest.raises(ValueError, match="Nothing to change"):
        eo.change({}, [], by=BY)
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}, [], by=BY)
    again = eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "7"}, [], by=BY)
    assert again.set == [] and again.removed == []


# ── Live trading needs confirmation ───────────────────────────────────────────


@pytest.mark.parametrize("values", [
    {"SKOPAQ_TRADING_MODE": "live"},
    {"SKOPAQ_SCHEDULER_MODE": "LIVE"},
    {"SKOPAQ_SCHEDULER_CONFIRM_LIVE": "true"},
])
def test_turning_live_on_needs_confirmation(store, values):
    with pytest.raises(PermissionError, match="needs confirmation"):
        eo.change(values, [], by=BY)
    assert not store.exists()
    assert SkopaqConfig().trading_mode == "paper"


def test_confirmed_live_is_saved_applied_and_logged(store):
    result = eo.change({"SKOPAQ_TRADING_MODE": "live", "SKOPAQ_SCHEDULER_MODE": "live",
                        "SKOPAQ_SCHEDULER_CONFIRM_LIVE": "on"}, [], by=BY, confirm_live=True)
    assert result.live == ["SKOPAQ_TRADING_MODE=live", "SKOPAQ_SCHEDULER_MODE=live",
                           "SKOPAQ_SCHEDULER_CONFIRM_LIVE=true"]
    config = SkopaqConfig()
    assert config.trading_mode == "live" and config.scheduler_confirm_live == "true"
    entry = eo.history()[0]
    assert entry["by"] == BY and entry["live"] == result.live


def test_already_live_needs_no_confirmation(store):
    eo.change({"SKOPAQ_TRADING_MODE": "live"}, [], by=BY, confirm_live=True)
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "4"}, [], by=BY)  # unrelated change: no 409
    eo.change({"SKOPAQ_TRADING_MODE": "paper"}, [], by=BY)  # back to paper: no confirmation


def test_a_reset_that_turns_live_on_needs_confirmation(store, monkeypatch):
    monkeypatch.setenv("SKOPAQ_TRADING_MODE", "live")  # the server (ENV_FILE) is live
    eo.change({"SKOPAQ_TRADING_MODE": "paper"}, [], by=BY)
    assert SkopaqConfig().trading_mode == "paper"
    with pytest.raises(PermissionError):
        eo.change({}, ["SKOPAQ_TRADING_MODE"], by=BY)
    eo.change({}, ["SKOPAQ_TRADING_MODE"], by=BY, confirm_live=True)
    assert SkopaqConfig().trading_mode == "live"


# ── Applying ──────────────────────────────────────────────────────────────────


def test_override_wins_and_reset_restores_the_server_value(store, monkeypatch):
    monkeypatch.setenv("SKOPAQ_SCANNER_MAX_CANDIDATES", "5")
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "9"}, [], by=BY)
    assert os.environ["SKOPAQ_SCANNER_MAX_CANDIDATES"] == "9"
    assert eo.server_value("SKOPAQ_SCANNER_MAX_CANDIDATES") == "5"
    result = eo.change({}, ["SKOPAQ_SCANNER_MAX_CANDIDATES"], by=BY)
    assert result.removed == ["SKOPAQ_SCANNER_MAX_CANDIDATES"]
    assert os.environ["SKOPAQ_SCANNER_MAX_CANDIDATES"] == "5"


def test_reset_of_a_key_the_server_does_not_set_unsets_it(store):
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "9"}, [], by=BY)
    eo.change({}, ["SKOPAQ_SCANNER_MAX_CANDIDATES"], by=BY)
    assert "SKOPAQ_SCANNER_MAX_CANDIDATES" not in os.environ


def test_apply_reads_the_file_and_reports_changes(store):
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"values": {"SKOPAQ_SCANNER_MAX_CANDIDATES": "6"}}))
    assert eo.apply() is True
    assert os.environ["SKOPAQ_SCANNER_MAX_CANDIDATES"] == "6"
    assert eo.apply() is False  # unchanged
    store.write_text(json.dumps({"values": {}}))
    assert eo.apply() is True
    assert "SKOPAQ_SCANNER_MAX_CANDIDATES" not in os.environ


def test_a_tampered_file_cannot_set_locked_or_foreign_keys(store, monkeypatch):
    monkeypatch.setenv("SKOPAQ_API_TOKEN", "real")
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"values": {"SKOPAQ_API_TOKEN": "evil", "PATH": "/evil",
                                            "SKOPAQ_SCANNER_MAX_CANDIDATES": "6"}}))
    eo.apply()
    assert os.environ["SKOPAQ_API_TOKEN"] == "real"
    assert os.environ["PATH"] != "/evil"
    assert os.environ["SKOPAQ_SCANNER_MAX_CANDIDATES"] == "6"


@pytest.mark.parametrize("body", ["not json", "[]", '{"values": [1]}'])
def test_a_corrupt_file_is_ignored(store, body):
    store.parent.mkdir(parents=True)
    store.write_text(body)
    assert eo.load() == {}
    eo.apply()  # never raises


# ── Secrets, file and log ─────────────────────────────────────────────────────


def test_secrets_are_never_described_or_logged(store):
    eo.change({"SKOPAQ_GOOGLE_API_KEY": "AIza-secret"}, [], by=BY)
    item = {s["key"]: s for s in eo.describe()}["SKOPAQ_GOOGLE_API_KEY"]
    assert item["secret"] is True and item["value"] is None
    assert item["is_set"] is True and item["source"] == "dashboard"
    assert "AIza-secret" not in eo.history_file().read_text()
    assert eo.history()[0]["set"] == {"SKOPAQ_GOOGLE_API_KEY": "(secret)"}
    assert SkopaqConfig().google_api_key.get_secret_value() == "AIza-secret"


def test_describe_sources_and_locks(store):
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "8"}, [], by=BY)
    items = {s["key"]: s for s in eo.describe()}
    assert items["SKOPAQ_SCANNER_MAX_CANDIDATES"]["source"] == "dashboard"
    assert items["SKOPAQ_SCANNER_MAX_CANDIDATES"]["value"] == "8"
    assert items["SKOPAQ_TRADING_MODE"]["source"] == "server"
    assert items["SKOPAQ_TRADING_MODE"]["choices"] == ["paper", "live"]
    assert items["SKOPAQ_ATR_PERIOD"]["source"] == "default"
    assert items["SKOPAQ_SUPABASE_URL"]["locked"]
    assert items["SKOPAQ_SCHEDULER_CONFIRM_LIVE"]["kind"] == "bool"


def test_file_is_private(store):
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "8"}, [], by=BY)
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(eo.history_file().stat().st_mode) == 0o600
    assert json.loads(store.read_text())["updated_by"] == BY


def test_history_is_newest_first(store):
    eo.change({"SKOPAQ_SCANNER_MAX_CANDIDATES": "8"}, [], by=BY)
    eo.change({}, ["SKOPAQ_SCANNER_MAX_CANDIDATES"], by=BY)
    first, second = eo.history()[:2]
    assert first["removed"] == ["SKOPAQ_SCANNER_MAX_CANDIDATES"]
    assert second["set"] == {"SKOPAQ_SCANNER_MAX_CANDIDATES": "8"}
