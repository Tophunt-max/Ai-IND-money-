# F&O Trading (Options Buying)

The F&O engine (`skopaq/scalping/fno_engine.py`, rules in `skopaq/scalping/fno_rules.py`)
trades index options intraday on INDstocks. It buys a CE when the underlying sets up
bullish and a PE when it sets up bearish, and sells everything before the close. It runs
beside the daily session when `SKOPAQ_FNO_ENABLED=true` (off by default), or alone with
`skopaq fno`.

!!! danger "Buying only"
    Skopaq never writes options and never shorts futures. The safety checker lets an F&O
    SELL only close a long position of the same contract (`segment=DERIVATIVE`, matched
    by the contract's security id). The paper engine refuses such SELLs too.

## How a trade is taken

1. **Underlyings.** Each name in `SKOPAQ_FNO_UNDERLYINGS` (NIFTY, BANKNIFTY, FINNIFTY,
   MIDCPNIFTY, SENSEX, BANKEX or an F&O stock) is resolved to its market-data code:
   `NIDX_<id>` or `BIDX_<id>` for an index, `NSE_<id>` for a stock. The engine then loads
   today's 1-minute candles of each from INDstocks and subscribes them to the price feed.
   When the feed is down it falls back to batched REST quotes.
2. **Direction.** On every closed candle in `SKOPAQ_FNO_ENTRY_START`–`SKOPAQ_FNO_ENTRY_END`,
   the engine runs the [scalping strategies](scalping.md) (`SKOPAQ_FNO_STRATEGIES`) on the
   underlying's candles.
   - A setup on the candles is **bullish** → buy a CE.
   - A setup on the *inverted* candles (each price `p` becomes `K / p`) is **bearish**
     → buy a PE (`SKOPAQ_FNO_ALLOW_BEARISH`).
   - The underlying's stop and target come from the setup (`SKOPAQ_FNO_RR`).
3. **Contract.** The engine reads the live option chain of the nearest expiry
   (`SKOPAQ_FNO_EXPIRY_INDEX`). On its expiry day it uses the next expiry instead
   (`SKOPAQ_FNO_AVOID_EXPIRY_DAY`). From that chain it takes the strike
   `SKOPAQ_FNO_STRIKE_OFFSET` steps from the money: 0 is ATM, -1 is one step in the money,
   +1 is one step out. The contract must have a price and a bid-ask spread within
   `SKOPAQ_FNO_MAX_SPREAD_PCT`.
4. **Size, in whole lots.** `SKOPAQ_FNO_RISK_PER_TRADE_INR` is divided by the premium at
   risk per lot (`SKOPAQ_FNO_PREMIUM_STOP_PCT` of the premium × the lot size). The result
   is then capped by:
   - `SKOPAQ_FNO_MAX_LOTS`;
   - the safety rules' `max_lots_per_position` (5 lots; equity has its own share cap);
   - `SKOPAQ_FNO_MAX_PREMIUM_INR` of premium;
   - the safety rules' position percentage (one lot is always allowed).

   If not even one lot fits, the setup is skipped and counted under **Skipped**.
5. **Charges.** A trade is skipped when its expected profit is less than
   `SKOPAQ_FNO_MIN_REWARD_TO_COST` × the round-trip charges. The expected profit is the
   underlying's move to the target × the option's delta. The charges are ₹10 brokerage
   per order plus GST, 0.1 % STT on the premium sold, and the exchange and stamp charges.
6. **Day limits.** A setup is taken only if all of these allow it:
   - `SKOPAQ_FNO_MAX_TRADES_PER_DAY`;
   - `SKOPAQ_FNO_MAX_OPEN`;
   - `SKOPAQ_FNO_MAX_DAILY_LOSS_INR`;
   - a cool-down of `SKOPAQ_FNO_COOLDOWN_MINUTES` after a loss;
   - the kill switch;
   - no more than one position per underlying.

## Exits (every second)

- **Underlying stop / target.** The underlying reaches the setup's stop or target. For a PE
  that means rising to its stop or falling to its target.
- **Premium stop.** The premium falls `SKOPAQ_FNO_PREMIUM_STOP_PCT` below the entry
  (25 % by default).
- **Breakeven, then trail.** Once the premium has gained as much as it risked (1 R), its
  stop moves to the entry plus charges. From then on it trails `SKOPAQ_FNO_TRAIL_PCT`
  below the premium's high.
- **Time stop.** A position not in profit after `SKOPAQ_FNO_MAX_HOLD_MINUTES` is sold.
- **Flatten.** Everything is sold at `SKOPAQ_FNO_FLATTEN_AT` (15:10), on a stop of the
  session, or when the session fails.

## Orders

Every order goes through the same Executor → SafetyChecker → order router as the rest of
Skopaq. It is a MARKET order with:

- `segment=DERIVATIVE` and product `INTRADAY`;
- the contract's `security_id` and `lot_size`;
- a quantity in units, always a whole number of lots.

The order path treats F&O differently from equity:

- **Pricing and sizing.** The Executor never prices a contract from Yahoo and never
  ATR-sizes it.
- **Funds.** The safety checker counts lots, not shares. It checks the option-buying
  balance (`option_buy_available`) or the futures margin.
- **Live SELL checks.** The no-short-sale check reads the F&O positions and has no
  holdings. It ignores order-book rows of the equity segment.
- **Re-pricing an exit.** An exit is re-priced from the `NFO_<id>` / `BFO_<id>` LTP on the
  ₹0.05 tick.
- **Trade rows.** Trade rows store product `MIS` and the contract (`segment`,
  `security_id`, `lot_size`, `lots`) under `model_signals.fno`.

The swing monitor and CLOSING never touch F&O positions. When the engine restarts, it
adopts INTRADAY F&O positions of its underlyings and manages them with the premium stop.

## Futures

Set `SKOPAQ_FNO_INSTRUMENT=futures` to buy the near-month future on bullish setups (long
only, no bearish trades).

!!! warning "Index futures are refused by the safety rules"
    One NIFTY lot is worth about ₹18 lakh. That is far above `max_order_value_inr`
    (₹5 lakh, ₹2 lakh for the daemon) and `max_position_pct` (15 %) in
    `skopaq/constants.py`. Those rules are immutable on purpose: only a human edits them.
    So an index future BUY is refused. A stock future with a small lot value can pass.
    Options buying is the supported path.

## Dashboard

On the **Control** page:

- **The F&O card** shows:
  - the day's net P&L after charges;
  - trades against the limit;
  - what was skipped and why;
  - the underlyings' prices;
  - the open contracts, with the premium stop and the underlying's stop and target, plus
    **Close** and **Close all F&O**;
  - the day's trades.

  With no session running, live, **Close all F&O at the broker** sells the long F&O
  positions through the same checks.
- **The F&O (options buying) card** turns the engine on and changes its settings. Changes
  apply from the next session.

## Commands

```bash
skopaq fno                                   # paper, now until the flatten time
skopaq fno --underlyings NIFTY,BANKNIFTY     # other underlyings
skopaq fno --live                            # real orders (asks to confirm)
```

The dashboard's **Options** page shows the chain the engine picks from.

Paper-trade for at least a week before going live. Live F&O also needs F&O enabled on the
INDstocks account and the static IP whitelisted for orders.

!!! note "Not verified against the broker yet"
    These parts follow the INDstocks docs but have not been checked against a live
    account:

    - the `NIDX_` / `BIDX_` codes for index history and quotes;
    - whether orders take the cash exchange (`NSE`) with `segment=DERIVATIVE`;
    - the segment text in order-book rows.

    Watch the first paper session's logs (and `skopaq ticks`) before going live.
