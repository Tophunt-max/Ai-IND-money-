# AWS (EC2)

The whole always-on stack (api, scheduler, optional telegram) on one EC2 instance with Docker
Compose, a fixed Elastic IP for the INDstocks whitelist, and auto-deploy from GitHub.

| | |
|---|---|
| Instance | `t4g.small` (2 vCPU Arm, 2 GiB) + 2 GiB swap, Ubuntu 24.04, Mumbai `ap-south-1` |
| Cost | about USD 14/month (instance ~8, Elastic IP ~3.6, 30 GiB gp3 ~2.5): USD 200 of new-account credit covers the 6-month free plan |
| Kit | [`deploy/aws/`](https://github.com/Tophunt-max/Ai-IND-money-/tree/main/deploy/aws): `cloudformation.yaml`, `user-data.sh`, `env.example`, `check.sh` |

## 0. Before AWS

1. **Gemini key** (required): [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. **Supabase** (recommended: kill switch, P&L history, memory): new project in *South Asia
   (Mumbai)*; in the SQL Editor run `supabase/migrations/001_initial.sql`, `002_agent_memories.sql`,
   `003_system_flags.sql` in order; note the project URL, `anon` and `service_role` keys
   (Project Settings → API).
3. **Telegram** (optional): `/newbot` with @BotFather; keep the token.

## 1. AWS account

1. Root user: **Security credentials → Assign MFA device**.
2. Region (top right): **Asia Pacific (Mumbai) ap-south-1**. Check it every time.
3. **EC2 → Key pairs → Create key pair**: name `ai-ind-key`, RSA, `.pem`. Keep the file: it is
   the only way in, and the GitHub deploy uses it.

## 2a. Create the server: CloudFormation (recommended)

1. Download [`deploy/aws/cloudformation.yaml`](https://raw.githubusercontent.com/Tophunt-max/Ai-IND-money-/main/deploy/aws/cloudformation.yaml)
   (right-click → Save as).
2. **CloudFormation → Create stack → With new resources → Upload a template file** → the file →
   Next.
3. Stack name `ai-ind`. Parameters: **KeyName** `ai-ind-key`; **AlertEmail** your email (alerts
   only on money actually billed after credits); leave the rest. Next → Next → tick nothing extra
   → **Submit**.
4. Wait for `CREATE_COMPLETE` (~3 min). **Outputs** shows the **ElasticIp**: note it.

The stack creates: a security group with SSH only (port 8000 stays closed: the API listens on
127.0.0.1), the instance with an encrypted 30 GiB gp3 disk, IMDSv2 and `standard` CPU credits (no
surprise burst charges), the Elastic IP, and the optional budget. First boot runs
`deploy/aws/user-data.sh` from GitHub.

## 2b. Create the server: console (instead of 2a)

**EC2 → Launch instance**: name `ai-ind-backend`; Ubuntu Server 24.04 LTS, **64-bit (Arm)**;
`t4g.small`; key pair `ai-ind-key`; security group with **SSH from 0.0.0.0/0** only; 30 GiB gp3;
**Advanced details → Credit specification: Standard**, and paste all of
[`deploy/aws/user-data.sh`](https://raw.githubusercontent.com/Tophunt-max/Ai-IND-money-/main/deploy/aws/user-data.sh)
into **User data**. Launch. Then **Elastic IPs → Allocate → Associate** it with the instance, and
create a budget (**Budgets → Zero spend budget**).

## 3. First boot

**EC2 → Instances → the instance → Connect → EC2 Instance Connect → Connect**, then:

```bash
cat ~/setup-done.txt              # appears after 10-20 min (the image is pre-built)
tail -f /var/log/ai-ind-setup.log # progress meanwhile (Ctrl+C to leave)
```

`setup-done.txt` says `image build: ok`. If it says `FAILED`, run
`cd ~/Ai-IND-money- && docker compose build api` once more. If `docker` says permission denied,
reconnect.

## 4. Configure and start

```bash
cd ~/Ai-IND-money-
nano .env            # the starter from deploy/aws/env.example; Ctrl+O Enter, Ctrl+X
```

Fill in `SKOPAQ_GOOGLE_API_KEY`, the three Supabase values, `SKOPAQ_EXPECTED_EGRESS_IP` (the
Elastic IP) and, if used, the Telegram token. Keep both modes `paper`.

```bash
docker compose up -d api scheduler   # add telegram once its token is set
bash deploy/aws/check.sh             # every line PASS (WARNs are advice)
```

Smoke tests:

```bash
docker compose exec api skopaq analyze RELIANCE        # 2-5 min, no order
docker compose exec scheduler skopaq schedule --check  # today's plan
docker compose run --rm daemon --dry-run               # scanner only
```

Telegram: send the bot `/start`, put the chat ID it replies with in `SKOPAQ_TELEGRAM_CHAT_ID` and
`SKOPAQ_TELEGRAM_ALLOWED_CHAT_IDS`, then `docker compose up -d telegram`.

From now on the scheduler runs one paper session every NSE trading day at 09:15 IST.

## 5. Auto-deploy from GitHub

Add the secrets `EC2_HOST` (Elastic IP) and `EC2_SSH_KEY` (whole `.pem`): see
[Auto-deploy to EC2](github-auto-deploy.md). Pushes to `main` that pass CI then deploy
themselves, never during market hours or a running session.

## 6. Day to day

| | |
|---|---|
| Status | `docker compose ps` · `bash deploy/aws/check.sh` |
| Logs | `docker compose logs -f --tail 100 scheduler` |
| Paper results | `docker compose exec api skopaq report --days 30` |
| Kill switch | `docker compose exec api skopaq halt "reason"` · `... skopaq resume` |
| Manual update (outside 08:30-16:00 IST) | `git pull && docker compose up -d --build` |
| Credit left | Billing → Credits; ~USD 3.5 a week |

Keep a copy of `.env` off the server.

## 7. Going live (after 1-2 weeks of paper)

1. `skopaq report` looks right on paper.
2. The Elastic IP is whitelisted at INDstocks, and `check.sh` shows `egress IP` PASS.
3. `docker compose exec api skopaq token set <token>`, then `skopaq token status`.
4. In `.env`: `SKOPAQ_SCHEDULER_MODE=live` and `SKOPAQ_SCHEDULER_CONFIRM_LIVE=true`; outside
   market hours `docker compose up -d scheduler`. See [Live trading](../trading/live-trading.md).

## 8. When the free plan ends, or to remove everything

- Before month 6: upgrade the account to the paid plan (the same instance keeps running at about
  USD 14/month), or move to another host: the stack is just `git clone`, `.env` and
  `docker compose up -d`.
- To remove it all: **CloudFormation → the stack → Delete** (2a), or terminate the instance and
  **release the Elastic IP** (2b). An Elastic IP left unattached is still billed.
