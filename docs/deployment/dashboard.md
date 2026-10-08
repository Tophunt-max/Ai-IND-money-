# Web dashboard (Vercel)

`frontend/` is a Next.js dashboard that talks to the API on the EC2 host:

| Page | What it does | API |
|---|---|---|
| Dashboard | Kill-switch banner, unrealized and realized P&L, open positions at market price, today's auto-trading session, 90-day P&L chart, system status | `/api/dashboard/overview`, `/pnl-history`, `/scheduler`, `/api/status` |
| Market | NIFTY 50 / Bank NIFTY / India VIX, symbol search, quote, price chart (1D–5Y) | `/api/dashboard/market/*` |
| Analyze | Full multi-agent analysis (`skopaq analyze`), or analysis + **paper** trade (`skopaq trade`, refused unless the server is in paper mode), 2–5 min | `/api/dashboard/jobs` |
| Trades | Trades from Supabase (current mode, paper, live, all) | `/api/dashboard/trades` |
| Report | Track record: AI calls vs NIFTY, closed trades, calibration (`skopaq report`) | `/api/dashboard/report` |
| Chat | The AI chat agent (paper trades in paper mode) | `/api/chat/message` |
| Settings | Kill switch (halt / resume), links to Environment, Scheduler and Scanner, install instructions, logout | `/api/dashboard/kill-switch*` |
| Environment (admin) | View and change `SKOPAQ_*` settings: trading mode, live switch, scheduler, keys (see below) | `/api/dashboard/settings/env` |
| Scheduler | `skopaq schedule --check` plan, the last 10 days' session results, session logs | `/api/dashboard/scheduler[/log]` |
| Scanner | One scan of the watchlist (`skopaq scan`); needs an INDstocks token for quotes | `/api/dashboard/jobs` |

Prices, charts and unrealized P&L come from Yahoo Finance (`skopaq/broker/yahoo_quotes.py`)
and can lag NSE by a few minutes. Paper fills use the same price when INDstocks has no
token; live orders never do.

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
- **Roles**: `admin` does everything; `viewer` sees every page but cannot run analyses,
  paper trades, scans, chat or the kill switch (the API answers 403).
- The API checks every token with Supabase (cached 60 s), so **Logout all devices**
  (Settings) ends every session within a minute.
- **Login history** (Settings): each new session, and each refused account, is stored in
  `dashboard_logins` (migration `004_dashboard_logins.sql`, applied automatically; readable only with the service key).
- **Failed logins**: the login screen pauses for 5 minutes after 5 wrong passwords;
  the API refuses an IP for 10 minutes after 20 rejected tokens (429). Supabase rate-limits
  sign-ins too.
- `SKOPAQ_API_TOKEN`, if set, still works as an admin bearer token (scripts, Telegram/OpenClaw
  bridge); it is no longer typed into the dashboard.
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
       @dashboard path /health /api/status /api/dashboard/* /api/chat/*
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
  log directories, the heartbeat file, `SKOPAQ_KITE_ACCESS_TOKEN`,
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
| Analyze fails with "API key not valid" | `SKOPAQ_GOOGLE_API_KEY` wrong |
