# Swing Trading

The GTT-based swing workflow (`setup_swing_trade`, `place_gtt_order`) relied on Kite Connect and was removed. Swing trades with automatic target and stop-loss legs will come back on INDstocks smart orders in the next phase (see [GTT Orders](gtt-orders.md)).

## What Still Works

| Step | How |
|------|-----|
| Find candidates | `skopaq scan`, MCP `scan_market` (Telegram sends an automatic scan at 09:25 IST) |
| Analyse a stock | `skopaq analyze RELIANCE`, Telegram `/analyze RELIANCE`, MCP `analyze_stock` |
| Place an order | MCP `place_order` (paper engine, every order passes the `SafetyChecker`) |
| Automated trading and exits | `skopaq daemon` (scan, analyse, trade) and `skopaq monitor` (stop-loss, trailing stop, end-of-day exits) |
| Learn from the outcome | MCP `save_trade_reflection` |

## Position Sizing

SkopaqTrader uses the 1% risk rule:

```
Available capital:    Rs 10,00,000
Risk per trade:       1% = Rs 10,000
Stop distance:        Rs 2,400 - Rs 2,350 = Rs 50
Position size:        Rs 10,000 / Rs 50 = 200 shares
Order value:          200 x Rs 2,400 = Rs 4,80,000
```

!!! warning "Safety limits apply"
    The `SafetyChecker` enforces max position size (15% of capital) and max order value (Rs 5,00,000). If your calculated size exceeds these limits, it will be capped.

## Risk Management

1. **Never risk more than 1-2% per trade** -- Position size accordingly
2. **Avoid earnings** -- Do not swing trade through earnings announcements
3. **Maximum 3-5 concurrent swings** -- Enforced by `max_open_positions` safety rule
