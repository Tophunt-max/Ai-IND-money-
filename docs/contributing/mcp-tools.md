# Adding MCP Tools

MCP tools are defined in `skopaq/mcp_server.py` using the `@mcp.tool()` decorator from FastMCP. Each tool is an async function that returns a JSON string.

## Anatomy of a Tool

```python
@mcp.tool()
async def my_tool(symbol: str, days: int = 5) -> str:
    """Short description shown to the AI.

    Longer description with details about what the tool does,
    when to use it, and what it returns.

    Args:
        symbol: Stock symbol (e.g. RELIANCE, TCS).
        days: Number of days of history (default 5).
    """
    config = _get_config()

    # ... implementation ...

    return json.dumps({
        "symbol": symbol,
        "result": "...",
    })
```

### Key rules:

1. **Async function** -- All tools must be `async def`
2. **Returns `str`** -- Always return `json.dumps(...)`, never raw dicts
3. **Docstring is the description** -- The AI reads this to decide when to use the tool
4. **Type hints on all args** -- FastMCP generates the tool schema from type hints
5. **Default values** -- Provide sensible defaults for optional parameters

## Step-by-Step: Adding a New Tool

### Step 1: Define the Function

Add your tool in the appropriate section of `skopaq/mcp_server.py`:

```python
# ── My New Section ──────────────────────────────────────────────────────────

@mcp.tool()
async def get_option_greeks(
    symbol: str = "NIFTY",
    strike: float = 0,
    option_type: str = "CE",
) -> str:
    """Get IV and Greeks (delta, gamma, theta, vega) for one option contract.

    Args:
        symbol: Underlying symbol (NIFTY, BANKNIFTY, or stock).
        strike: Strike price.
        option_type: CE (call) or PE (put).
    """
    try:
        from skopaq.options.chain import load_option_chain

        # Opens its own INDstocksClient (needs a valid INDstocks token)
        chain = await load_option_chain(symbol, 0, config=_get_config())
        legs = chain.calls if option_type.upper() == "CE" else chain.puts
        contract = next((c for c in legs if c.strike == strike), None)
        if contract is None:
            return json.dumps({"error": f"No {option_type} {strike} in {symbol} chain"})

        return json.dumps({
            "symbol": symbol,
            "strike": strike,
            "type": option_type,
            "iv": contract.iv,
            "delta": contract.delta,
            "gamma": contract.gamma,
            "theta": contract.theta,
            "vega": contract.vega,
        })

    except Exception as exc:
        logger.exception("Greeks calculation failed")
        return json.dumps({"error": str(exc)})
```

### Step 2: Handle Errors Gracefully

Always catch exceptions and return structured error JSON:

```python
try:
    result = await some_operation()
    return json.dumps({"success": True, "data": result})
except Exception as exc:
    logger.exception("Operation failed")
    return json.dumps({"error": str(exc)})
```

This ensures the AI always gets a parseable response.

### Step 3: Use Lazy Infrastructure

Access shared infrastructure via the lazy helpers:

```python
config = _get_config()    # SkopaqConfig (cached)
router = _get_router()    # OrderRouter + PaperEngine (cached)
```

Market data comes from INDstocks: open a client per call (`async with INDstocksClient(config, TokenManager()) as client:`, as `get_quote` does) or use a helper that does it for you, like `load_option_chain` in `skopaq/options/chain.py`.

Do not import and instantiate these at module level -- it would slow down server startup.

### Step 4: Write Tests

Add a test in `tests/unit/` that mocks external dependencies:

```python
# tests/unit/test_mcp_greeks.py
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skopaq.options.chain import OptionChainData, OptionContract

@pytest.mark.asyncio
async def test_get_option_greeks():
    from skopaq import mcp_server

    call = OptionContract(
        tradingsymbol="NIFTY24000CE", security_id="1", exchange="NFO",
        strike=24000, option_type="CE", expiry=date(2026, 4, 9), lot_size=75, delta=0.42,
    )
    chain = OptionChainData(symbol="NIFTY", spot_price=23900, expiry=call.expiry, calls=[call])

    with patch.object(mcp_server, "_get_config", return_value=MagicMock()), \
         patch("skopaq.options.chain.load_option_chain", AsyncMock(return_value=chain)):
        data = json.loads(await mcp_server.get_option_greeks("NIFTY", 24000, "CE"))
    assert data["delta"] == 0.42
```

### Step 5: Update Tests and Permissions

After adding your tool:

1. Update `tests/unit/chat/test_mcp_server.py` -- add tool name to the expected set
2. Add to `.claude/settings.json` permissions if it should be auto-allowed
3. Run tests: `python3 -m pytest tests/unit/ -x -q`

### Step 6: Add to Skills (Optional)

If your tool should be available via a slash command, add it to an existing skill's `allowed-tools` or create a new skill:

```yaml
# .claude/skills/greeks/SKILL.md
---
name: greeks
description: Calculate option Greeks for a contract
allowed-tools: mcp__skopaq__get_option_greeks mcp__skopaq__get_option_chain
---
```

## Design Guidelines

### Docstring Quality

The docstring is critical -- it is the only thing the AI sees when deciding which tool to use:

```python
# Good: specific, mentions when to use it
"""Calculate implied volatility for an option contract.

Use this to compare IV across strikes and identify overpriced/underpriced options.
Returns IV as a percentage along with the historical IV rank.

Args:
    symbol: Underlying symbol.
"""

# Bad: vague, no context
"""Get some option data."""
```

### Return Structure

Always return a flat JSON object with clear field names:

```python
# Good
return json.dumps({
    "symbol": "NIFTY",
    "iv": 15.3,
    "iv_rank": 45,
    "iv_percentile": 62,
})

# Bad: nested, unclear
return json.dumps({
    "data": {"s": "NIFTY", "vals": [15.3, 45, 62]}
})
```

### Broker-Dependent Tools

INDstocks calls fail when the token is missing or expired. Catch the exception and return it as JSON (as `get_option_chain` does) so the AI can tell the user to refresh the token (`skopaq token set`).

### Size Limits

Truncate large responses to avoid overwhelming the AI's context window:

```python
# Limit text fields
return json.dumps({
    "news": news_text[:3000],  # Cap at 3000 chars
    "candles": candles[-20:],   # Last 20 only
})
```

## Tool Naming Conventions

| Pattern | Example | When |
|---------|---------|------|
| `get_*` | `get_quote`, `get_funds` | Read-only data retrieval |
| `place_*` | `place_order` | Actions that create something |
| `gather_*` | `gather_market_data` | Fetch raw data for analysis |
| `check_*` | `check_safety` | Validation tools |
| `suggest_*` | `suggest_option_trade` | AI recommendations |

## File Reference

| File | Purpose |
|------|---------|
| `skopaq/mcp_server.py` | All tool definitions (add your tool here) |
| `skopaq/config.py` | Configuration (if your tool needs new config) |
| `.claude/.mcp.json` | MCP server registration for Claude Code |
| `.claude/skills/*/SKILL.md` | Skill files that reference tools |
