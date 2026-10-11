---
name: analyze
description: Show what the Adaptive AI sees in a stock — its swing setup on the last closed daily bar, the swing model's probability, and the supporting market data. The Adaptive AI decides; this skill only reports.
argument-hint: <SYMBOL>
user-invocable: true
allowed-tools: mcp__skopaq__swing_idea mcp__skopaq__get_quote mcp__skopaq__gather_market_data mcp__skopaq__check_safety mcp__skopaq__system_status
---

# Adaptive AI view of a stock

**Symbol: $ARGUMENTS**

SkopaqTrader has one decision-maker: the **Adaptive AI** (models trained on our own market data). Report its output; do **not** invent your own BUY/SELL verdict, confidence or price levels.

**IMPORTANT**: Use MCP tools (`mcp__skopaq__*`) for data. Do NOT write Python/Bash code to call broker APIs directly.

## Steps

1. `mcp__skopaq__swing_idea` (symbol=$ARGUMENTS) — the setup (trend_pullback / breakout / oversold_bounce) on the last closed daily bar, its entry, ATR stop and R-multiple target, and the swing model's P(win) with its threshold.
2. `mcp__skopaq__get_quote` (symbol=$ARGUMENTS) — the live price, to show how far it is from the setup's entry.
3. `mcp__skopaq__gather_market_data` (symbol=$ARGUMENTS) — indicators for context (RSI, moving averages, ATR).

## Report

| Field | Value |
|-------|-------|
| **Setup** | name, or "none today" |
| **Model** | version · P(win) vs threshold → qualifies / does not |
| **Entry / Stop / Target** | ₹ from `swing_idea` (never your own) |
| **Live price** | ₹ and % from entry |

Then 3–5 bullet points of context from the indicators (facts only).

- `idea` is null → no setup fired today: say so; nothing to trade.
- `model` is null → no swing model is trained yet: suggest `skopaq swing-train` (or the dashboard AI learning page). Without a model the swing book makes no entries.
- If the user asks whether it is safe to trade, call `mcp__skopaq__check_safety` with the setup's levels.
