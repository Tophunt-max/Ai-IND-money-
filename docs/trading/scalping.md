# Intraday Scalping

The scalper (`skopaq/scalping/`) trades short intraday moves on live ticks, long only, with
the INTRADAY (MIS) product, and sells everything before the close. It runs beside the
daily swing session when `SKOPAQ_SCALP_ENABLED=true`, or alone with `skopaq scalp`.

## How a scalp is taken

1. At the start it loads today's 1-minute candles of every `SKOPAQ_SCALP_SYMBOLS`
   instrument from INDstocks, so EMA, RSI, ATR and VWAP are ready at once, and subscribes
   them to the price WebSocket. When the feed is down it asks for batched REST quotes every
   `SKOPAQ_SCALP_REST_POLL_SECONDS`.
2. On every closed candle between `SKOPAQ_SCALP_ENTRY_START` and `SKOPAQ_SCALP_ENTRY_END`,
   the strategies are asked in the order of `SKOPAQ_SCALP_STRATEGIES`:

    | Strategy | Entry | Stop | Target |
    |---|---|---|---|
    | `vwap_pullback` | Uptrend (EMA 9 > EMA 21, above VWAP); a green candle that dips to VWAP and closes back above. With the LTP feed (no volume) EMA 21 stands in for VWAP | below the dip | `rr` × risk |
    | `ema_rsi` | EMA 9 crosses above EMA 21 on this candle, RSI(14) 50–70, close above EMA 21 | below the 5-candle low | `rr` × risk |
    | `orb` | The first close above the high of the first `SKOPAQ_SCALP_ORB_MINUTES` (range 0.3–2.5 % of price); once a day per symbol | the range's middle | the range's width |
    | `range_reversal` | A green candle tagging the 30-candle low with RSI(14) below 40 | below the low | the range's middle (≥ 1.2 × risk) |

    Stops are kept 0.4–2.5 ATR below the entry.
3. The setup is taken only when the day allows it: fewer than `SKOPAQ_SCALP_MAX_TRADES_PER_DAY`
   scalps, fewer than `SKOPAQ_SCALP_MAX_OPEN` open, the day's loss below
   `SKOPAQ_SCALP_MAX_DAILY_LOSS_INR`, `SKOPAQ_SCALP_COOLDOWN_MINUTES` after a losing scalp,
   the kill switch off, and no CNC (swing) position in that symbol.
4. **Size:** `SKOPAQ_SCALP_RISK_PER_TRADE_PCT` of equity divided by the stop distance,
   capped at `SKOPAQ_SCALP_MAX_POSITION_VALUE_INR` and at the safety rules' share cap.
5. **Charges:** a scalp whose target pays less than `SKOPAQ_SCALP_MIN_REWARD_TO_COST` × the
   round-trip charges (₹10 brokerage per order + GST, STT on the sell, exchange, stamp;
   `skopaq/scalping/costs.py`) is skipped.

## Exits (every second)

- **Stop-loss** and **target**.
- **Breakeven, then trail:** at 1 R of profit the stop moves to entry plus the charges per
  share, and from then on trails one ATR below the high.
- **Time stop:** a scalp not in profit after `SKOPAQ_SCALP_MAX_HOLD_MINUTES` is sold.
- **Flatten:** at `SKOPAQ_SCALP_FLATTEN_AT` (15:10, before the broker's intraday
  square-off), on a stop of the session, or when the session fails.

Every order is a MARKET order through the same Executor → SafetyChecker → order router as
the rest of Skopaq. Live, the order worker confirms each fill and works the exits until
they fill. A BUY the broker does not confirm stops further scalps in that symbol for the
day. The scalper owns its INTRADAY positions: the swing monitor and CLOSING manage CNC
rows only. If the process restarts, the scalper adopts the INTRADAY positions it finds in
its symbols, with a conservative stop and target.

!!! note "Share limit: `SKOPAQ_MAX_SHARES_PER_ORDER` (1000)"
    An equity order may have up to `SKOPAQ_MAX_SHARES_PER_ORDER` shares (default 1000;
    change it on **Control → Exits & risk**). It can never exceed the immutable ceiling
    `SafetyRules.max_shares_per_position` (5000) in `skopaq/constants.py`. The real limits
    on a scalp are its value (`SKOPAQ_SCALP_MAX_POSITION_VALUE_INR`, ₹50,000), the safety
    rules' order value (₹2 lakh in the daemon, ₹5 lakh otherwise) and 15 % of the
    portfolio.

## Dashboard

On the **Control** page:

- The **Scalper** card shows the day's net P&L after charges, scalps against the limit,
  what was skipped and why new entries are off, the result per strategy, open scalps
  (with **Close** and **Close all scalps**) and the day's trades.
- The **Scalping** card turns the scalper on and changes its settings, which apply from
  the next session.

## Commands

```bash
skopaq scalp-backtest RELIANCE --days 5            # strategies on past 1-minute candles
skopaq scalp-backtest RELIANCE --days 5 --max-qty 5   # with the safety share cap
skopaq scalp                                       # paper, now until the flatten time
skopaq scalp --live                                # real INTRADAY orders (asks to confirm)
```

**How the backtest simulates a trade:**

- It fills a setup at the next candle's open.
- A candle that touches both the stop and the target counts as a loss.
- Charges are paid on every trade.

It ignores fill quality beyond the next open. Paper-trade a strategy for at least a week
before going live.

## F&O

The same strategies drive the F&O engine, which buys index options (a CE on bullish
setups, a PE on bearish ones) in whole lots: see [F&O Trading](fno.md).
