# Quick Start

From zero to your first paper session. Every decision comes from the **Adaptive AI**; nothing here places a real order.

## 1. Train the swing model

```bash
skopaq swing-train          # NIFTY 50, 5 years of daily history; registers a candidate or shadow
skopaq swing-status         # did it pass the gates?
```

No model passed? Train on more symbols or years, or let a paper account trade the raw setups to build a record: `SKOPAQ_SWING_RULES_ONLY=true` (ignored live).

## 2. See today's ideas

```bash
skopaq scan --limit 10      # setup, entry / stop / target, P(win) — no orders
```

## 3. Run a paper session

```bash
skopaq daemon --once --paper   # scan → signal gate → executor → monitor → close
skopaq monitor                 # attach to positions that are already open
skopaq report --days 30        # journalled signals and closed trades
```

Intraday books: `skopaq scalp` (scalper) and `skopaq fno` (index options buying), both paper by default.

## Other ways in

| Where | What |
|-------|------|
| Dashboard | Swing scanner, AI learning (train / promote), Trade desk, charts — see [Dashboard](../deployment/dashboard.md) |
| Telegram | `/ideas`, `/analyze TCS`, `/portfolio`, `/halt` — see [Commands](../telegram/commands.md) |
| Claude Code | `/scan`, `/analyze TCS`, `/trade TCS` (paper, after confirmation) — see [Setup](../claude-code/setup.md) |

## Going Live

!!! danger "Real Money"
    Live trading uses real money. Stay on paper until the shadow model has a record you trust.

1. Promote the swing model after its 7 shadow days: `skopaq swing-promote VERSION --by NAME --reason TEXT` (live sessions trade only a champion).
2. `skopaq preflight --live` must pass.
3. Follow [Live Trading](../trading/live-trading.md).
