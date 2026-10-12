# MCP Server Architecture

The MCP (Model Context Protocol) server is how SkopaqTrader integrates with Claude Code and other AI assistants. It exposes 26 tools via the FastMCP framework over stdio transport. The tools read data, the Adaptive AI's swing ideas and the track record, run safety checks and backtests, and place paper orders; no tool calls an LLM.

## What Is MCP?

MCP is a protocol that allows AI assistants to call external tools. Instead of the AI writing Python code to access your broker, it calls structured MCP tools that handle authentication, error handling, and data formatting.

```
AI Assistant (Claude Code)
       │
       │  MCP Protocol (stdio)
       ▼
  skopaq/mcp_server.py
       │
       ├── Broker API (INDstocks)
       ├── Order router (paper engine) + SafetyChecker
       ├── Swing engine + swing model registry (Adaptive AI)
       ├── Data vendors (tradingagents/dataflows)
       └── Signal journal / trade history
```

## FastMCP Framework

The server uses [FastMCP](https://github.com/jlowin/fastmcp), a Python framework for building MCP servers. Tools are defined as decorated async functions:

```python
from mcp.server import FastMCP

mcp = FastMCP(
    name="SkopaqTrader",
    instructions="SkopaqTrader MCP server for Indian equity trading...",
)

@mcp.tool()
async def get_quote(symbol: str) -> str:
    """Get a real-time market quote for a stock symbol."""
    # ... implementation
    return json.dumps({"symbol": symbol, "ltp": 2485.50, ...})
```

Key design principles:

- **All tools are async** -- They can call async broker APIs without blocking
- **All tools return JSON strings** -- Structured data that Claude can parse
- **Lazy initialization** -- Infrastructure (config, broker clients) is built on first tool call, not at import time

## Transport

The server uses **stdio transport** -- it reads requests from stdin and writes responses to stdout. This is the default transport for Claude Code MCP servers.

```json
// .claude/.mcp.json
{
  "mcpServers": {
    "skopaq": {
      "command": "python3",
      "args": ["-m", "skopaq.mcp_server"]
    }
  }
}
```

Claude Code launches the Python process and communicates with it over pipes. No network ports are needed.

## Lazy Infrastructure

To keep startup fast, the server uses lazy initialization:

```python
_infra_cache: dict = {}

def _get_config():
    if "config" not in _infra_cache:
        from skopaq.config import SkopaqConfig
        _infra_cache["config"] = SkopaqConfig()
    return _infra_cache["config"]
```

At startup (`python -m skopaq.mcp_server`) the server applies the settings saved from the dashboard (`skopaq.env_overrides.apply()`), then waits on stdin. The first tool call that needs them builds:

1. `SkopaqConfig` -- loads `.env` and validates all settings (`_get_config()`)
2. `OrderRouter` + `PaperEngine` -- paper trading infrastructure (`_get_router()`)

The data tools (`gather_*`, `backtest_strategy`, `run_monte_carlo_test`) also call `_setup_dataflow_config()`, which sets the vendored `tradingagents/dataflows` config for `route_to_vendor`: INDstocks only for NSE/BSE prices (crypto, an experimental asset class, uses yfinance). `gather_market_data` computes the indicators from INDstocks daily candles; `gather_news_data`, `gather_fundamentals_data` and `gather_social_data` report that the INDstocks API has no such data. The swing tools build a `SwingEngine` from the config on each call.

Subsequent calls reuse cached objects.

## Data Sources

INDstocks is the only broker. Market data tools open an INDstocks client per call; portfolio and order tools go through the cached `OrderRouter` (paper engine):

```python
@mcp.tool()
async def get_quote(symbol: str) -> str:
    async with INDstocksClient(config, TokenManager()) as client:
        scrip_code = await resolve_scrip_code(client, symbol)
        q = await client.get_quote(scrip_code, symbol=symbol)
    return json.dumps({...})
```

## Tool Categories

The 26 tools are organized into categories:

| Category | Tools | Source |
|----------|-------|--------|
| Market Data | `get_quote`, `get_historical` | INDstocks |
| Portfolio | `get_positions`, `get_holdings`, `get_funds`, `get_orders` | Order router (paper engine) |
| Swing Ideas | `swing_ideas`, `swing_idea` | Swing engine + swing model (Adaptive AI) |
| Safety | `check_safety` | SafetyChecker (paper rules) |
| Data Gathering | `gather_*` (5 tools) | Dataflows |
| Execution & System | `place_order`, `system_status`, `halt_trading`, `resume_trading` | Order router / kill switch / swing registry |
| Options | `get_option_chain`, `suggest_option_trade` | INDstocks |
| Learning | `performance_report`, `backtest_strategy`, `run_monte_carlo_test`, `get_learning_insights`, `get_symbol_stats`, `evolve_strategy` | Signal journal, trade history, backtester |

`place_order` is the only order tool. GTT, bracket, cover, basket, AMO, F&O order and mutual fund tools were removed with Kite Connect.

## Error Handling

All tools catch exceptions and return structured error JSON:

```python
try:
    chain = await load_option_chain(symbol, expiry_index, config=_get_config())
    return json.dumps({...})
except Exception as exc:
    logger.exception("Option chain fetch failed")
    return json.dumps({"error": str(exc)})
```

This ensures Claude always gets a parseable response, even on failure.

## Running Standalone

For debugging, run the server directly:

```bash
python -m skopaq.mcp_server
```

It will start and wait for MCP messages on stdin. You can also run it inside Docker:

```bash
docker run -it --env-file .env samuelvinay91/skopaq:latest mcp
```

## File Reference

| File | Purpose |
|------|---------|
| `skopaq/mcp_server.py` | MCP server with all 26 tool definitions |
| `.claude/.mcp.json` | Claude Code MCP server configuration |
| `skopaq/config.py` | Configuration loaded by `_get_config()` |
| `skopaq/env_overrides.py` | Settings saved from the dashboard, applied at startup |
| `skopaq/swing/engine.py` | Swing engine behind `swing_ideas` / `swing_idea` |
| `skopaq/broker/client.py` | INDstocks broker client |
| `skopaq/broker/fno.py` | F&O underlyings, expiries, lot sizes, contracts |
| `skopaq/options/chain.py` | Option chain fetcher (INDstocks) |
