# Auto-deploy to EC2 from GitHub

Every push to `main` that passes CI updates the EC2 host automatically:
`.github/workflows/deploy.yml` copies `scripts/deploy/ec2-deploy.sh` to the host over SSH and runs
it there. The host keeps its `.env`; only the code and the image change.

## Safety rules

- **Never during market hours.** Mon–Fri 08:30–16:00 IST nothing is deployed: restarting the
  scheduler mid-session makes it close its positions. A push in that window is deployed by the
  scheduled run at **16:15 IST**.
- **Never while a session runs.** The host checks the scheduler container for a running `daemon`
  or `monitor` (and for a one-off `docker compose run daemon`) and skips if it finds one, even
  when the run is forced.
- **Only commits that passed CI** are deployed.
- **Rollback.** If a service is not `healthy` within 5 minutes, the host checks out the previous
  commit and brings it back up. The run then fails, so you see it in the Actions tab.
- **Same services as before.** The services running now are the ones rebuilt (none running:
  `api scheduler`, plus `telegram` when `SKOPAQ_TELEGRAM_BOT_TOKEN` is set). Set `DEPLOY_SERVICES`
  in the host's environment to override.

## One-time setup

1. The host (Ubuntu with Docker) is already running the stack: repo cloned at `~/Ai-IND-money-`,
   `.env` filled in, `docker compose up -d --build` run once, the SSH user in the `docker` group.
2. The security group allows SSH (port 22) from GitHub's runners. Their IPs change, so this
   means `0.0.0.0/0`; logins still need the key.
3. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | Value |
   |---|---|
   | `EC2_HOST` | the Elastic IP |
   | `EC2_SSH_KEY` | the whole `.pem` file, including the `BEGIN`/`END` lines |
   | `EC2_USER` | optional, default `ubuntu` |

   Until `EC2_HOST` is set the workflow only logs "skipping deploy".

## Running it

- **Automatic:** merge or push to `main`; after CI, see **Actions → Deploy (EC2)**.
- **By hand:** **Actions → Deploy (EC2) → Run workflow**. Tick *force* to deploy during market
  hours (a running session still blocks it).
- **On the host:**
  `cp scripts/deploy/ec2-deploy.sh /tmp/ && bash /tmp/ec2-deploy.sh <commit-sha> [force]`
  (exit 0 deployed or nothing to do, 3 skipped, other failed).

GitHub disables scheduled workflows in a repository with no activity for 60 days; the
after-CI deploy is not affected.
