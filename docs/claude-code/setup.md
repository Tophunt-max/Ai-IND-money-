# Claude Code Setup

SkopaqTrader integrates with Claude Code via a Model Context Protocol (MCP) server. Once connected, its 26 tools (broker data, portfolio, the Adaptive AI's swing ideas, safety checks, backtests, paper orders) are available inside your Claude Code session. The decisions come from the Adaptive AI; Claude Code reads and reports them.

## Prerequisites

- Python 3.12+ installed
- SkopaqTrader cloned and dependencies installed (`pip install -e .`)
- A `.env` file (copied from `.env.example`) with a valid INDstocks token, or one set with `skopaq token set`
- For the swing ideas: a trained swing model (`skopaq swing-train`, see [Swing Trading](../trading/swing-trading.md))

## Register the MCP Server

**Option 1 — Project-level config** (recommended):

Create or edit `.claude/.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "skopaq": {
      "command": "python3",
      "args": ["-m", "skopaq.mcp_server"]
    }
  }
}
```

**Option 2 — Global config** (available in all projects):

Edit `~/.claude.json` and add the same block under `"mcpServers"`.

!!! tip "Use absolute Python path"
    If Claude Code cannot find `python3`, use the full path:
    ```json
    "command": "/usr/local/bin/python3"
    ```
    Find yours with `which python3`.

## Verify the Connection

After saving the config, restart Claude Code and ask:

> What is the current status of the SkopaqTrader system?

Claude calls `mcp__skopaq__system_status` and shows version, trading mode, asset class, token health, the swing model (champion and shadow versions), paper capital and the kill-switch state.

## How It Works

The MCP server (`skopaq/mcp_server.py`) runs as a child process using **stdio transport**. Claude Code launches it automatically and communicates over stdin/stdout. No network ports are needed.

```
Claude Code  ──stdin/stdout──  python -m skopaq.mcp_server
                                   │
                                   ├── INDstocks (quotes, candles, option chain)
                                   ├── Order router (paper engine) + SafetyChecker
                                   ├── Swing engine + swing model registry (Adaptive AI)
                                   └── Signal journal / trade history (reports, learning)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Tools not appearing | Restart Claude Code after editing `.mcp.json` |
| `ModuleNotFoundError` | Run `pip install -e .` in the project root |
| Broker errors | Ensure a valid INDstocks token (`skopaq token status`, or `SKOPAQ_INDSTOCKS_TOKEN` in `.env`) |
| Swing tools return `model: null` | No swing model trained yet: run `skopaq swing-train` |
| MCP server crashes | Run `python -m skopaq.mcp_server` standalone to see errors |

!!! warning "Environment Variables"
    The MCP server reads from your shell environment (and the settings saved from the dashboard). If you use `direnv` or a `.env` loader, make sure variables are exported before Claude Code launches.

## Auto-Allowed vs Permission-Required Tools

`.claude/settings.json` auto-allows the read-only tools plus `halt_trading`; every other tool asks for permission:

| Auto-Allowed | Requires Permission |
|---|---|
| `get_quote`, `get_historical` | `place_order` |
| `get_positions`, `get_holdings`, `get_funds`, `get_orders` | `resume_trading` |
| `system_status`, `check_safety`, `performance_report` | `get_option_chain`, `suggest_option_trade` |
| `gather_*` data tools | `backtest_strategy`, `run_monte_carlo_test` |
| `swing_ideas`, `swing_idea` | `get_learning_insights`, `get_symbol_stats`, `evolve_strategy` |
| `halt_trading` | |

## Next Steps

- Browse all [MCP Tools](mcp-tools.md)
- Try the [Custom Skills](skills.md) (slash commands)
