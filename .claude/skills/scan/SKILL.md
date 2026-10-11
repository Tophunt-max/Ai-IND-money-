---
name: scan
description: Today's swing ideas from the Adaptive AI — daily setups ranked by the swing model (no orders).
argument-hint: "[max_ideas]"
user-invocable: true
allowed-tools: mcp__skopaq__swing_ideas mcp__skopaq__get_quote
---

# Swing scan (Adaptive AI)

Call `mcp__skopaq__swing_ideas` with limit=$ARGUMENTS (default 10). It reads a year of daily candles for each symbol in the configured universe (NIFTY 50 by default), finds the setup on the last closed bar and keeps it when the swing model's probability clears its validated threshold.

**IMPORTANT**: Use MCP tools (`mcp__skopaq__*`) for data. Do NOT write Python/Bash code to call broker APIs directly. Do not add symbols or rank them yourself — the list is the Adaptive AI's.

## Output

| # | Symbol | Setup | P(win) | Entry | Stop | Target |
|---|--------|-------|--------|-------|------|--------|

Optionally call `mcp__skopaq__get_quote` for the top 3 to show the live price against the entry.

- No ideas and `model` is null → no swing model trained yet: suggest `skopaq swing-train`.
- No ideas with a model → nothing cleared the threshold today (or there was no market data). That is a valid answer.

Nothing is bought by this skill. The trading session takes the same ideas through the signal gate, the position sizer and the safety checker.
