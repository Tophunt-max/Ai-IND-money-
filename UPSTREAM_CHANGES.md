# Upstream Changes Log

Documents what is left of [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
under `tradingagents/`, and every modification to it.

**Upstream base:** v0.5.2 — commit `5eb5085` (2026-09-29).

## What is vendored

Only the **market-data layer**: `tradingagents/dataflows/` (vendor routing, yfinance,
Alpha Vantage, SEC EDGAR, FRED, Polymarket, StockTwits, Reddit, crypto vendors) and a
trimmed `tradingagents/default_config.py` (data keys only).

**Removed (Adaptive-AI-only, 2026-10):** the LLM framework — `agents/`, `graph/`,
`llm_clients/`, `memory/`, `reporting.py`, `backtest.py`, `portfolio.py` — together
with the vendored upstream `cli/` and `main.py`, and with them the former modifications
for per-role LLMs, crypto analyst agents, the Portfolio Manager's confidence field,
parallel analysts and the Jev endpoint. SkopaqTrader's decisions come from its Adaptive
AI (`skopaq/adaptive`, `skopaq/swing`). The CI job that ran upstream's test suite was
removed too: upstream's tests target the framework that is no longer here.

Changes inside upstream files are marked with a `Skopaq:` comment. To compare
with pristine upstream:

```bash
git clone https://github.com/TauricResearch/TradingAgents /tmp/ta
git -C /tmp/ta checkout 5eb5085
diff -ru -x __pycache__ /tmp/ta/tradingagents/dataflows tradingagents/dataflows
```

## Modifications

### 1. INDstocks data vendor

**Files:** `dataflows/vendors/indstocks.py` (new), `dataflows/router.py`

- New vendor for NSE OHLCV from the INDstocks broker API, returning the same
  CSV shape as yfinance. Strips `.NS`/`.BO` suffixes, bridges the async
  client to sync, includes the `end_date` candle (like the yfinance vendor),
  and raises `NoMarketDataError` on an empty result so the router can try
  the next configured vendor.
- Registered first in `VENDOR_LIST` and in `VENDOR_METHODS["get_stock_data"]`.

**At the v0.5.2 base, `VENDOR_LIST` is re-added by Skopaq.** Upstream deleted
the list when it moved vendor preference into the per-method dicts. Skopaq
brings it back with `indstocks` first, because
`tests/unit/dataflows/test_indstocks_vendor.py` asserts both the membership
and the ordering, and because the ordering is the readable statement of which
vendor wins. The fallback chain itself is `VENDOR_METHODS`, whose insertion
order upstream still honours.

**Backward compatible:** yes — only used when a config names `indstocks`
(Skopaq uses `"core_stock_apis": "indstocks,yfinance"`).

### 2. yfinance symbol suffix

**Files:** `dataflows/router.py`, `default_config.py`

- `route_to_vendor` appends `config["yfinance_symbol_suffix"]` (e.g. `.NS`)
  to a bare symbol before calling a yfinance function
  (`_apply_yfinance_suffix`). New config key, default `""`.

**Why:** Skopaq code calls `route_to_vendor` directly with bare NSE symbols
(ATR sizing, MCP data tools, backtests, swing training). A symbol that already
has a suffix (`RELIANCE.NS`) is left alone.
**Backward compatible:** yes — the empty default changes nothing.

### 3. Crypto data vendors

**New files:** `dataflows/vendors/crypto_onchain.py`, `dataflows/vendors/crypto_defi.py`,
`dataflows/vendors/crypto_funding.py` (Blockchair / Blockchain.info, DeFiLlama /
CoinGecko, Binance Futures). They accept yfinance-style pairs (`BTC-USD`), Binance pairs
(`BTCUSDT`) and bare coins.

**Backward compatible:** yes — new files only.

### 4. Trimmed `default_config.py`

Only the data-layer keys remain (`data_cache_dir`, news limits, `data_vendors`,
`tool_vendors`, `yfinance_symbol_suffix`); the LLM, debate and results keys went with
the framework.

## Syncing a newer upstream

1. Extract the new tag and diff its `tradingagents/dataflows` against v0.5.2 to see
   what upstream changed in the data layer.
2. Import the new `dataflows/` verbatim in one commit, then re-apply the modifications
   above in a second (three-way: `git merge-file -p new old local`; check for
   `<<<<<<<` markers rather than trusting the exit code).
3. Run `python3 -m pytest tests/unit/` — `tests/unit/dataflows/` covers the vendors and
   the router, and the swing / ATR / MCP tests exercise `route_to_vendor`.
4. Update this file.
