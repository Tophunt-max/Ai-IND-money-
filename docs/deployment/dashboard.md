# Web dashboard (Vercel)

`frontend/` is a Next.js dashboard that talks to the API on the EC2 host:

A modern app design in **light and dark** (Settings → Appearance, or `d`): a top bar with
Home · Market · Portfolio · AI insights · Options · Trade desk and a **More** menu (desktop),
bottom tabs with a centre **Trade** button that opens the order ticket (phone). Everywhere:

- **Live prices** — one Server-Sent Events stream (`/market/stream`) carries every symbol on
  screen (≤ 50; 2 s while NSE is open, 15 s otherwise); prices flash on change. When the
  stream is down the app polls `/market/quotes`.
- **Search / command palette** — `Ctrl K` or `/`: stocks, pages and actions. Shortcuts:
  `g h/m/p/a/o/t/l/s` go to a page, `b` new order, `n` notifications, `d` theme, `?` help.
- **Order ticket** — buy / sell sheet from a chart, a watchlist row (B / S), an AI idea or the
  Trade button: quantity, market / limit, stop-loss, target (set on the position's exit plan),
  value, risk and reward : risk. It calls `POST /control/orders` (safety checker; live: type
  LIVE).
- **Notifications** — the bell: the server's feed (`/notifications`), fired price alerts as
  toasts.
- **Installable app** — install button (Settings), offline page, "new version" banner.

The pages:

| Page | What it does | API |
|---|---|---|
| Home | Account value and P&L curve, quick actions, indices, AI swing ideas (the latest scan), watchlist, top movers, open positions, risk capacity, auto-trading session, system health. Sections can be hidden (Settings → Layout) | `/api/dashboard/overview`, `/pnl-history`, `/jobs`, `/market/heatmap`, `/scheduler`, `/api/status` |
| Portfolio | Holdings value, returns and today's change at live prices; holdings, positions, F&O and today's orders; **Analytics**: equity curve with drawdown, daily P&L calendar, best / worst day, winning days, allocation by stock or sector. Read only | `/api/dashboard/portfolio`, `/pnl-history`, `/overview` |
| **Trade desk** (`/control`) | Run the engine: pause/resume new BUYs, auto sessions on/off, **start a session now** (or, live, a monitor), **stop** the running session, live positions (LTP, P&L, stop, target, high) with **Close** / **Close all** and stop/target edits, **manual orders** (live: type LIVE), today's order book with **Cancel**, exit & risk settings. Streams every 2 s (admin actions only; each is logged and sent to Telegram) | `/api/dashboard/control*` |
| Market | Indices, watchlists (up to 10, kept on the device; NIFTY 50 built in), top gainers / losers / most traded, a heatmap preview. A stock: live price and day range, chart (1D–5Y; candles or line; EMA, SMA, Bollinger, VWAP, RSI, MACD, volume, candle patterns; signal, position and alert levels; horizontal and trend-line drawings kept per symbol; full screen), your position, Overview / Technicals / **AI view** (the swing setup and P(win)), Buy / Sell, price alert | `/api/dashboard/market/*`, `/swing/idea`, `/alerts` |
| Heatmap (`/market/heatmap`) | NIFTY 50 by sector coloured by the day's change (tile size: traded value), breadth, best / worst, sector averages | `/api/dashboard/market/heatmap` |
| AI insights (`/ai`) | Today's swing ideas (cards or list, buy with the setup's levels), the active model and P(win) gauge, model versions on unseen dates vs taking every setup, permutation feature importance, results by setup, the 30-day live record of swing signals | `/api/dashboard/swing`, `/jobs`, `/report` |
| Price alerts (`/alerts`) | Alerts above / below a price or a day change; active, fired (re-arm), notifications. They fire once, on the live stream or the Telegram bot's minute check, and go to Telegram | `/api/dashboard/alerts*`, `/notifications*` |
| Swing scanner | One swing scan (`skopaq scan`, a `scan` job): today's setups scored by the swing model, with entry / stop / target and P(win), and the swing model's state. Places nothing | `/api/dashboard/jobs`, `/swing` |
| Options | INDstocks expiries; chain (IV, Delta, OI) with B / S to build legs; open interest and OI change by strike with max pain; IV smile; **payoff calculator** (max profit / loss, break-evens, net premium); a rule-based option-selling idea. Needs the INDstocks token; places nothing | `/api/dashboard/options/expiries`, `/options/chain`, `/options/suggest` |
| Signals & risk | Every journalled signal (QUALIFIED / REJECTED / EXPIRED) with its evidence and rejection reason, risk limits, and reconciliation blockers to resolve | `/api/dashboard/signals`, `/risk`, `/control/reconcile` |
| Trades | Trades from Supabase (current mode, paper, live, all), with totals and a symbol filter | `/api/dashboard/trades` |
| Track record | Journalled signals by strategy, top rejection reasons, closed trades, calibration (`skopaq report`) | `/api/dashboard/report` |
| AI learning | Swing model (champion / shadow / candidates, **Promote**, **Rollback**, **Train swing model** as a `swing_train` job), model registry, strategy builder, Adaptive AI, RL agents, regime drift, signal-tracker insights (needs `DATABASE_URL`) and one symbol's record | `/api/dashboard/swing*`, `/learning/*`, `/jobs` |
| Backtest | RSI mean-reversion backtest (equity curve, trades) and Monte Carlo of its trades; CPU only, no orders | `/api/dashboard/jobs` |
| Scheduler | `skopaq schedule --check` plan, the last 10 days' sessions, logs | `/api/dashboard/scheduler[/log]` |
| Jobs | Every swing scan, swing training, backtest and Monte Carlo started from the dashboard (kinds `scan`, `swing_train`, `backtest`, `montecarlo`; kept in memory) | `/api/dashboard/jobs` |
| Broker | INDstocks token: health, expiry, **set today's token** or delete it (admin; like `skopaq token set`), and an info card on equity and F&O at INDstocks | `/api/dashboard/broker[/indstocks-token]` |
| Settings | Appearance (light / dark / system, density, price flashes, reduce motion), layout (landing page, home sections), chart defaults (type, range, indicators), watchlists, install the app; kill switch, account, login history, links | `/api/dashboard/kill-switch*` |
| Environment (admin) | View and change `SKOPAQ_*` settings: trading mode, live switch, scheduler, keys (see below) | `/api/dashboard/settings/env` |

## Trade desk (admin)

The Trade desk page talks to the trading processes through files in `SKOPAQ_CONTROL_DIR`
(`~/.skopaq/control` on the shared home volume, `skopaq/execution/control.py`):

| Action | How it reaches the engine |
|---|---|
| Pause / Resume | the kill switch (no new BUYs; exits keep running) |
| Auto sessions on/off | `SKOPAQ_SCHEDULER_ENABLED` (a dashboard override); a running session is not stopped |
| Start session now | `start.request`: the scheduler runs today's session at its next poll, 09:00–15:00 IST on a trading day, once its earlier session has ended; refusals go to Telegram. **Start monitor** (live) runs `skopaq monitor` instead, to guard positions without opening new ones |
| Stop | `stop.request`: the daemon (or monitor) sets its stop as on a SIGTERM; the daemon then sells everything it holds (CLOSING) |
| Close / Close all, stop & target edits, manual orders | with a session running, a command its monitor runs at its next check (so exits go through its exit plans and booking); without one, live closes and orders are placed by the API through the same Executor → SafetyChecker → live order worker (SELL locks and the order journal are shared on the host); paper positions exist only inside a session |
| Cancel | live only: `POST /order/cancel` at INDstocks; the running session's own orders are refused (stop it or close the position instead) |

The daemon and the monitor publish `session.json` / `monitor.json` every few seconds; the
page streams them (`GET /api/dashboard/control/stream`, Server-Sent Events over `fetch`
with the bearer token, polling when the stream is down). A live manual BUY made while no
monitor runs has no automatic stop or target until one does: the page says so — use
**Start monitor**. The reverse proxy must not buffer the stream (the API sends
`X-Accel-Buffering: no`; Caddy streams `text/event-stream` as is).

Market data comes from INDstocks (INDmoney) whenever the server has a valid token
(`skopaq/broker/live_quotes.py`):

- **Stock quotes** come from `/market/quotes/full`.
- **Index quotes** come from the index instruments file. NIFTY 50 is `NIDX_40000001`; the
  code that answers is found once and remembered.
- **Charts** come from `/market/historical`: 5-minute candles for 1D, 15-minute for 5D,
  daily up to 1Y, and weekly for 5Y.
- **Refresh:** while NSE is open, the Market page refreshes every 5 s and the 1D chart
  every 10 s.
- **The badge** on each quote and chart says **LIVE · INDstocks** or **Delayed · Yahoo
  Finance**.

Yahoo Finance (`skopaq/broker/yahoo_quotes.py`, which can lag NSE by several minutes) is
used only without a token, for symbols INDstocks cannot resolve, and when INDstocks fails.
Set the token, or turn on TOTP so it renews daily ([Go live](go-live.md) §2). Paper fills
use Yahoo only when INDstocks has no token; live orders never do.

The dashboard is a PWA: on a phone, "Add to Home screen" / "Install app" opens it full screen.
The service worker caches the app shell only, never API responses.

## Login and security

Logins use **Supabase Auth** (`skopaq/api/dashboard_auth.py`, `frontend/src/components/AuthGate.tsx`):

- Email + password, or **Continue with Google**. "Remember me" keeps the session on the
  device (tokens refresh automatically); unticked, closing the tab logs out.
- **Forgot password** emails a link to `/reset-password`.
- **Only listed emails** get in: `SKOPAQ_DASHBOARD_USERS=me@x.com:admin,friend@y.com:viewer`.
  Anyone else (even with a Supabase account) sees "Access denied". The email must be
  confirmed.
- **Roles**: `admin` does everything; `viewer` sees every page but cannot start jobs
  (scans, training, backtests), trade, promote models or use the kill switch (the API
  answers 403).
- The API checks every token with Supabase (cached 60 s), so **Logout all devices**
  (Settings) ends every session within a minute.
- **Login history** (Settings): each new session, and each refused account, is stored in
  `dashboard_logins` (migration `004_dashboard_logins.sql`, applied automatically; readable only with the service key).
- **Failed logins**: the login screen pauses for 5 minutes after 5 wrong passwords;
  the API refuses an IP for 10 minutes after 20 rejected tokens (429). Supabase rate-limits
  sign-ins too.
- `SKOPAQ_API_TOKEN`, if set, still works as an admin bearer token (scripts); it is no longer typed into the dashboard.
- Set `SKOPAQ_CORS_ORIGINS` to the dashboard's URL so no other site can call the API from a
  browser. The reverse proxy exposes only the paths the dashboard uses.

## Setup

1. **Supabase**
   - Tables come from the automatic migrations (`SUPABASE_DB_URL` secret, see
     [Auto-deploy](github-auto-deploy.md#database-migrations)); nothing to paste in the SQL Editor.
   - Authentication → URL Configuration: *Site URL* `https://<project>.vercel.app`, and add
     `https://<project>.vercel.app/**` to *Redirect URLs*.
   - Authentication → Users → **Add user** with your email and a password (tick *Auto confirm*),
     or sign up later with Google. Turn off *Allow new users to sign up* if you only add users
     yourself (Authentication → Sign In / Providers).
   - Google (optional): in Google Cloud Console create an OAuth client (Web application) with
     the redirect URI `https://<project-ref>.supabase.co/auth/v1/callback`; paste its client ID
     and secret into Supabase → Authentication → Providers → Google, and enable it.
2. **`ENV_FILE`** secret, then **Actions → Deploy (EC2)** outside market hours:

   ```
   SKOPAQ_SUPABASE_URL=https://<project-ref>.supabase.co
   SKOPAQ_SUPABASE_ANON_KEY=<anon key>
   SKOPAQ_SUPABASE_SERVICE_KEY=<service_role key>
   SKOPAQ_DASHBOARD_USERS=you@example.com:admin,friend@example.com:viewer
   SKOPAQ_CORS_ORIGINS=https://<project>.vercel.app
   ```

3. **Caddy** on the host (`/etc/caddy/Caddyfile`, then `sudo systemctl reload caddy`):

   ```
   35-154-11-165.sslip.io {
       @dashboard path /health /api/status /api/dashboard/*
       handle @dashboard {
           reverse_proxy 127.0.0.1:8000
       }
       respond 404
   }
   ```

4. **Vercel** (Root Directory `frontend`) environment variables, then redeploy:

   ```
   NEXT_PUBLIC_BACKEND_URL=https://35-154-11-165.sslip.io
   NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
   ```

5. Open the Vercel URL and log in.

## Environment settings (admin)

Settings → **⚙️ Environment** lets an admin change `SKOPAQ_*` settings without editing the
`ENV_FILE` secret and redeploying (`skopaq/env_overrides.py`):

- Saved values go to `~/.skopaq/env_overrides.json` (mode 600) on the shared home volume. They
  **win over `ENV_FILE`** and survive deploys; **Reset** brings the `ENV_FILE` value (or the
  default) back. Each row shows where its value comes from: DASHBOARD, ENV_FILE or DEFAULT.
- When they apply: the API at once; the scheduler on its next poll while no session runs (a
  running session keeps its settings; the change is sent to Telegram); every new daemon,
  `skopaq monitor` or CLI run; the Telegram bot after its restart.
- Values are checked before saving (types, choices, the scheduler's times together). A
  secret (API keys, tokens) is never sent back to the browser, only whether it is set.
- **Switch to LIVE** sets `SKOPAQ_TRADING_MODE`, `SKOPAQ_SCHEDULER_MODE` and
  `SKOPAQ_SCHEDULER_CONFIRM_LIVE`. Anything that turns real-money trading on asks you to type
  `LIVE` (the API answers 409 without `confirm_live`), and is sent to Telegram.
- Every change is appended to `~/.skopaq/env_overrides.log` (who, when, keys; secret values
  are written as `(secret)`) and shown under *Change history*.
- **Locked** (ENV_FILE only): Supabase URL and keys, `SKOPAQ_API_TOKEN`, `SKOPAQ_CORS_ORIGINS`,
  `SKOPAQ_DASHBOARD_USERS`, API host/port, `SKOPAQ_DATABASE_URL`, the state, lock, journal and
  log directories, the heartbeat file,
  `SKOPAQ_ALLOW_SELL_WITHOUT_ORDER_BOOK` and `SKOPAQ_TRADING_HALTED` (use the kill switch).
  A mistake there could lock the dashboard out.
- To undo everything from the host: `docker compose exec api rm ~/.skopaq/env_overrides.json`,
  then restart the services.

## Troubleshooting

| Message | Fix |
|---|---|
| "Login is not set up" (server) | `SKOPAQ_DASHBOARD_USERS` or the Supabase URL / anon key missing from `ENV_FILE` |
| "Login not set up" (page) | `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` missing in Vercel |
| "Access denied: this account is not allowed" | Add the email to `SKOPAQ_DASHBOARD_USERS` and redeploy |
| "Access denied: this email is not confirmed" | Confirm it from the Supabase email, or *Auto confirm* in Authentication → Users |
| Google login returns to the login screen | Vercel URL missing from Supabase *Redirect URLs*, or wrong Google redirect URI |
| Reset link opens localhost | Supabase *Site URL* is still `http://localhost:3000` |
| Login history error | The `SUPABASE_DB_URL` secret is missing, or the last **Database migrations** run failed (Actions tab) |
| "Backend not reachable" | Caddy not running, the path not in the Caddyfile, or `SKOPAQ_CORS_ORIGINS` not your Vercel URL |
| Database error on the dashboard | `SKOPAQ_SUPABASE_URL` / `SKOPAQ_SUPABASE_SERVICE_KEY` wrong |
| Swing scanner says "No swing model" | Train one (**Train swing model** on AI learning, or `skopaq swing-train`); paper can also set `SKOPAQ_SWING_RULES_ONLY=true` |
