# Auto-deploy to EC2 from GitHub

Every push to `main` that passes CI updates the EC2 host automatically:
`.github/workflows/deploy.yml` copies `scripts/deploy/ec2-deploy.sh` to the host over SSH and runs
it there. The host's `.env` can come from the `ENV_FILE` secret (below) or be kept on the host.

## Safety rules

- **Never during market hours.** Mon–Fri 08:30–16:00 IST nothing is deployed: restarting the
  scheduler mid-session makes it close its positions. A push in that window is deployed by the
  scheduled run at **16:15 IST**.
- **Never while a session runs.** The host checks the scheduler container for a running `daemon`
  or `monitor` (and for a one-off `docker compose run daemon`) and skips if it finds one, even
  when the run is forced.
- **Only commits that passed CI** are deployed.
- **Rollback.** If a service is not `healthy` within 5 minutes, the host checks out the previous
  commit (and puts back the previous `.env` if it was replaced) and brings it back up. The run
  then fails, so you see it in the Actions tab. The `.env` that failed is kept as
  `.env.bak.failed`.
- **Services.** `api` and `scheduler`, plus `telegram` when `.env` has `SKOPAQ_TELEGRAM_BOT_TOKEN`
  (without a token telegram is stopped: it exits at start). Set `DEPLOY_SERVICES` in the host's
  environment to override.

## One-time setup

1. The host (Ubuntu with Docker) has the repo cloned at `~/Ai-IND-money-` and the SSH user in the
   `docker` group. The first run builds the image (10–20 minutes on a 2 GiB instance).
2. The security group allows SSH (port 22) from GitHub's runners. Their IPs change, so this
   means `0.0.0.0/0`; logins still need the key.
3. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | Value |
   |---|---|
   | `EC2_HOST` | the Elastic IP |
   | `EC2_SSH_KEY` | the whole `.pem` file, including the `BEGIN`/`END` lines |
   | `ENV_FILE` | optional: the whole `.env` for the host, one `KEY=value` per line |
   | `EC2_USER` | optional, default `ubuntu` |

   Until `EC2_HOST` is set the workflow only logs "skipping deploy". Without `ENV_FILE` the host
   must already have a `.env`.

## `.env` from the `ENV_FILE` secret

On every run the workflow sends the secret to the host over SSH (`/tmp/ai-ind.env`, mode 600,
deleted when the run ends; it is never written to the runner's disk or printed). If it differs
from the host's `.env`, the host:

1. keeps the old file as `.env.bak` and installs the new one as `.env` (mode 600, Windows line
   endings removed);
2. recreates the containers even when the commit is unchanged (they read `.env` only when
   created);
3. rolls back to `.env.bak` if a service does not come up healthy.

To change a key: edit the `ENV_FILE` secret, then **Actions → Deploy (EC2) → Run workflow**
outside market hours (or wait for the 16:15 IST run). A key added on the host by hand is
overwritten by the next run while `ENV_FILE` is set, so keep the secret as the only copy you edit.
Write `$$` for a literal `$` in a value.

## Running it

- **Automatic:** merge or push to `main`; after CI, see **Actions → Deploy (EC2)**.
- **By hand:** **Actions → Deploy (EC2) → Run workflow**. Tick *force* to deploy during market
  hours (a running session still blocks it).
- **On the host:**
  `cp scripts/deploy/ec2-deploy.sh /tmp/ && bash /tmp/ec2-deploy.sh <commit-sha> [force]`
  (exit 0 deployed or nothing to do, 3 skipped, other failed).

GitHub disables scheduled workflows in a repository with no activity for 60 days; the
after-CI deploy is not affected.
