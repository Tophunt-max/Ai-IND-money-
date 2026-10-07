# AWS deploy kit

Runbook: [docs/deployment/aws.md](../../docs/deployment/aws.md).

| File | What it is |
|---|---|
| `cloudformation.yaml` | One stack: EC2 (Ubuntu 24.04, `t4g.small` by default), Elastic IP, SSH-only security group, optional budget alert. Upload it in CloudFormation. |
| `user-data.sh` | First-boot setup (swap, IST, Docker, clone, starter `.env`, image pre-build). The stack runs it; for a console launch paste it into *User data*. |
| `env.example` | Starter `.env` with only the keys this host needs. |
| `check.sh` | Readiness checks on the host: `bash deploy/aws/check.sh`. |

Updates after setup: `.github/workflows/deploy.yml`
([docs/deployment/github-auto-deploy.md](../../docs/deployment/github-auto-deploy.md)).
