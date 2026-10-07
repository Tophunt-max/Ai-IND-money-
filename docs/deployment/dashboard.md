# Web dashboard (Vercel)

`frontend/` is a Next.js dashboard that talks to the API on the EC2 host:

| Page | What it does | API |
|---|---|---|
| Dashboard | Kill-switch banner, realized P&L, open positions, system status | `/api/dashboard/overview`, `/api/status` |
| Trades | Trades from Supabase (current mode, paper, live, all) | `/api/dashboard/trades` |
| Report | Track record: AI calls vs NIFTY, closed trades, calibration (`skopaq report`) | `/api/dashboard/report` |
| Scanner | One scan of the watchlist (`skopaq scan`); needs an INDstocks token for quotes | `/api/dashboard/jobs` |
| Analyze | Full multi-agent analysis of one symbol (`skopaq analyze`), 2–5 min, no order | `/api/dashboard/jobs` |
| Chat | The AI chat agent (paper trades in paper mode) | `/api/chat/message` |
| Settings | Kill switch (halt / resume), connection info, logout | `/api/dashboard/kill-switch*` |

## Security

- The login password is `SKOPAQ_API_TOKEN`. Every `/api/dashboard/*` endpoint refuses to run
  (503) while it is unset, and needs `Authorization: Bearer <token>` (401 otherwise); setting it
  also guards `/api/chat/*`. The browser keeps the token in `localStorage`.
- Set `SKOPAQ_CORS_ORIGINS` to the dashboard's URL so no other site can call the API from a
  browser.
- Analyze and scan never place orders. One job runs at a time; jobs live in the API process
  and are lost when it restarts.
- The reverse proxy exposes only the paths the dashboard uses (below), never the whole API.

## Setup

1. **`ENV_FILE`** secret: add, then run **Actions → Deploy (EC2)** outside market hours:

   ```
   SKOPAQ_API_TOKEN=<long random password>
   SKOPAQ_CORS_ORIGINS=https://<your-project>.vercel.app
   ```

2. **Caddy** on the host (`/etc/caddy/Caddyfile`, then `sudo systemctl reload caddy`):

   ```
   35-154-11-165.sslip.io {
       @dashboard path /health /api/status /api/dashboard/* /api/chat/*
       handle @dashboard {
           reverse_proxy 127.0.0.1:8000
       }
       respond 404
   }
   ```

   Replace the host name with your Elastic IP, dots as dashes, plus `.sslip.io`.

3. **Vercel**: import the repo, Root Directory `frontend`, environment variable
   `NEXT_PUBLIC_BACKEND_URL=https://35-154-11-165.sslip.io` (no trailing `/`). After changing it,
   redeploy.

4. Open the Vercel URL and log in with `SKOPAQ_API_TOKEN`.

## Troubleshooting

| Message | Fix |
|---|---|
| "Dashboard is off on the server" | `SKOPAQ_API_TOKEN` missing from `ENV_FILE`; add it and redeploy |
| "Wrong password" | The token typed differs from `SKOPAQ_API_TOKEN` |
| "Backend not reachable" | Caddy not running, the path not in the Caddyfile, or `SKOPAQ_CORS_ORIGINS` not your Vercel URL |
| Database error on the dashboard | `SKOPAQ_SUPABASE_URL` / `SKOPAQ_SUPABASE_SERVICE_KEY` wrong |
| Analyze fails with "API key not valid" | `SKOPAQ_GOOGLE_API_KEY` wrong |
