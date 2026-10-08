# GTT Orders

The Kite Connect GTT tools (`place_gtt_order`, `list_gtt_orders`, `setup_swing_trade`) were removed together with the Kite integration. SkopaqTrader currently places no GTT, OCO, bracket or cover orders.

## Planned: INDstocks Smart Orders

INDstocks offers **smart orders** (`/smart/order`): a parent order plus a GTT child with stop-loss and target legs, for both equity and derivatives. The parent gets an `EQ-` or `DRV-` order id, the GTT child a `GTT-` id. Support for smart orders is planned for the next phase; there is no command or MCP tool for them yet.

## Until Then

Open positions are protected by the position monitor (`skopaq monitor`, also run inside `skopaq daemon`): stop-loss, trailing stop and end-of-day exits. See [Live Trading](live-trading.md).
