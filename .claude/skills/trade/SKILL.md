---
name: trade
description: Paper-trade the Adaptive AI's swing setup for a stock — checks the setup, validates safety, and executes in paper mode only after the user confirms.
argument-hint: <SYMBOL>
user-invocable: true
allowed-tools: mcp__skopaq__swing_idea mcp__skopaq__get_quote mcp__skopaq__get_funds mcp__skopaq__check_safety mcp__skopaq__place_order
---

# Paper trade an Adaptive AI setup

**Symbol: $ARGUMENTS**

The Adaptive AI decides; you never make up a signal or levels.

### 1. The setup

Call `mcp__skopaq__swing_idea` (symbol=$ARGUMENTS).

- `idea` is null → no setup today: stop and say so.
- `model` is set and `prob` < `threshold` → the model does not take it: stop and say so.
- `model` is null → no trained swing model: say the live system would not trade it; continue only if the user explicitly asks for a paper test.

### 2. Size and safety

1. `mcp__skopaq__get_funds` — available capital.
2. Quantity = floor(1% of capital / (entry − stop)), at least 1.
3. `mcp__skopaq__check_safety` with symbol, quantity, the setup's entry as price, side BUY. If it fails, explain and stop.

### 3. Confirmation (MANDATORY)

```
TRADE PLAN: BUY <SYMBOL> (<setup>, P(win) <prob>)
  Entry / Stop / Target:  ₹… / ₹… / ₹…
  Quantity:               … shares · risk ₹…
  Mode:                   PAPER · Safety: PASSED
```

Ask: "Execute this in paper mode? (y/n)"

### 4. Execute (only after "yes")

`mcp__skopaq__place_order` with the confirmed parameters. The MCP server's orders always go to the paper engine.

**NEVER execute without explicit user confirmation.**
