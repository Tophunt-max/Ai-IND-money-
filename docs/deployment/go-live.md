# Go live on AWS (INDstocks)

This is the checklist for running real-money auto-trading on the EC2 host. The stack is the
API, Telegram and the scheduler (Docker Compose) behind Caddy, with the Vercel dashboard in
front. Do the steps in order. `skopaq preflight --live` (or **Control → Live readiness**)
checks most of them for you.

## 1. Static IP (SEBI / NSE)

The broker refuses API orders, modifies and cancels from an IP that is not whitelisted.
Read-only calls (quotes, history, order book, funds) work from any IP.

1. **EC2 → Elastic IPs → Allocate** and associate it with the instance. Without an Elastic
   IP, the public IPv4 changes on every stop/start.
2. On **indstocks.com/app/api-trading/access-tokens → Static IP**, put the Elastic IP in the
   **Primary** slot. The Secondary slot is optional: an IPv6, or a spare host.
   - A slot can be changed only **once a calendar week** (NSE circular NSE/INVG/67858).
   - A slot cannot be emptied.
3. Put the same IP(s) in the server's `.env` (or the `ENV_FILE` secret):

    ```
    SKOPAQ_INDSTOCKS_STATIC_IPS=13.234.x.x
    ```

    With this set, a live session refuses to start (PRE_OPEN fails, with a Telegram alert)
    when the host's egress IP is not in the list. The readiness check shows the egress IP
    it found.

If the instance has IPv6, the broker may see its IPv6 address instead of the IPv4 one.
Either whitelist that address too, or turn IPv6 egress off.

## 2. Daily token without the dashboard (TOTP)

INDstocks tokens last 24 hours. With TOTP the scheduler makes the day's token itself at
the pre-flight (08:45 IST). The daemon's PRE_OPEN also makes one if it still has none. You
no longer paste a token every morning.

1. On the Access Tokens page: **Setup TOTP**. Scan the QR code into an authenticator app,
   and also copy the **key shown under the QR code**: it is shown only once. Confirm with
   one code. The page then shows your **Client ID**.
2. Add these to the server's `.env` / `ENV_FILE` only. The dashboard refuses to store
   them.

    ```
    SKOPAQ_INDSTOCKS_CLIENT_ID=<Client ID>
    SKOPAQ_INDSTOCKS_MPIN=<your MPIN>
    SKOPAQ_INDSTOCKS_TOTP_SECRET=<the base32 key>
    ```

3. Test it outside market hours:

    ```bash
    docker compose exec scheduler skopaq token auto
    ```

    It makes a token only when the stored one would not last until 15:45 IST. Add `--force`
    to make one anyway, which invalidates the previous TOTP token. **Broker →
    Automatic daily token** on the dashboard does the same.

How it stays safe:

- Only one token is live at a time. Generation runs under a file lock on the shared
  volume, never twice within 65 s, and only when needed.
- After 2 failures in 15 minutes it pauses for 15 minutes, so a wrong MPIN or secret never
  reaches the broker's lockout (5 wrong codes).
- The MPIN, the secret and the tokens are never logged.

## 3. Clock (NTP)

TOTP codes fail when the clock is about a minute off, and the market-hours rules run on
the host clock. Ubuntu on EC2 syncs with the Amazon Time Sync Service through chrony:

```bash
chronyc tracking            # "System time" should be within milliseconds
```

The readiness check compares the host clock with the broker's `Date` header. Above 5 s
it warns, above 30 s it fails.

## 4. Account

The readiness check reads `GET /user/profile` and checks:

- **NSE onboarded.**
- **NSE F&O onboarded** when `SKOPAQ_FNO_ENABLED=true` (activate F&O in the INDmoney app;
  it needs an income proof).
- **DDPI active.** Without DDPI, a delivery (CNC) SELL of earlier days' shares needs a CDSL
  TPIN that the API cannot give, so swing exits of holdings may be refused. Intraday,
  scalping and F&O are not affected.
- **Funds** for equity and, with F&O on, for option buying.

## 5. Orders and SEBI's retail-algo rules

| Rule | What Skopaq does |
|---|---|
| Every API order carries an exchange algo id | `algo_id` is `99999` on NSE and `9999999999999999` on BSE, the generic ids INDstocks documents. If the broker or the exchange registers your strategy, put its ids in `SKOPAQ_INDSTOCKS_ALGO_ID_NSE` / `_BSE` (digits only) |
| Above 10 orders per second, a retail algo must be registered | Order calls (place, modify, cancel) are held to 8 per second per process. The safety rules allow 5 orders a minute in the daemon (20 elsewhere) |
| Static IP for API orders | Step 1 |
| Broker rate limits: orders 10/s, data (instruments, history, option chain) 5/s, quotes 5/s, other reads 15/s | A limiter per category keeps each under its limit. A 429 on a read is retried twice after a pause; an order is never re-sent |
| 3 WebSocket connections per account | A session shares one price feed between the monitor, the scalper and the F&O engine. `skopaq ticks` while a session runs takes one more |

## 6. Run the checks

```bash
docker compose exec api skopaq preflight --live
```

Every line should be ✔ or !. Any ✘ means orders would be refused or unsafe. With
`SKOPAQ_SCHEDULER_MODE=live`, the scheduler runs the same check at the pre-flight and
sends what fails to Telegram. The **Control** page shows it under **Live readiness**,
refreshed every 5 minutes or with **Check**.

## 7. First live days

1. Keep `SKOPAQ_SCHEDULER_MODE=paper` until the paper sessions look right, for at least a
   week.
2. Then **Settings → Environment → Switch to LIVE**, which needs `LIVE` typed to confirm.
3. Start small:
   - `SKOPAQ_DAEMON_MAX_TRADES_PER_SESSION=1`;
   - scalper and F&O engine off, or `SKOPAQ_FNO_MAX_LOTS=1`.
4. Watch the first session on **Control**:
   - positions;
   - **Today's orders**: each order's status, and the broker's message if it was refused;
   - Telegram alerts.

   **Pause** stops new BUYs at once. **Stop** sells everything.
5. Deploys never run during market hours (`github-auto-deploy.md`). After a deploy the
   scheduler restarts only outside sessions.
