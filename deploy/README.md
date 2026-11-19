# RongHub demo deployment

Public demo: https://ronghub.asksquirrel.ai. Customer integration is pending; this environment uses synthetic demo data. The management mini program is excluded.

## Delivery

Push to `main` triggers `.github/workflows/cd.yml`: PostgreSQL-backed API tests, generated contract verification, TypeScript checks and frontend build must pass before deployment. API and web images are published to GHCR with immutable full-commit tags. Actions copies deployment files over SSH and connects only RongHub's edge to the existing `matrix-network` Traefik network. No host ports are published.

Repository Variables: `SSH_HOST`, `SSH_USER`, `SSH_FINGERPRINT`. Secret: `SSH_PRIVATE_KEY`. The job uses its temporary `GITHUB_TOKEN` for GHCR, with an isolated temporary Docker login configuration. Never put the SSH private key in Variables.

## Server operations

Files: `~/ronghub`; private settings: `deploy/.env.cloud` (mode 600). Initial deployment generates separate owner/application database credentials and demo passwords, applies migrations and seeds an empty database once. The three accounts are `regulator`, `association`, `operator_a` (also `operator_b` for isolation demonstrations). Obtain the password privately from the server configuration; do not publish it in Actions logs or commit it.

```sh
cd ~/ronghub
python3 scripts/stack_ops.py --env deploy/.env.cloud --compose deploy/traefik.compose.yml status
python3 scripts/stack_ops.py --env deploy/.env.cloud --compose deploy/traefik.compose.yml backup .backups/manual-YYYYMMDD-HHMM
```

Before later deployments, a coordinated database and attachment backup is created under `.backups/`. Protect and copy these archives off the server. Restore requires a verified empty target; use the same `--env` and `--compose` options with `restore PATH`. Never delete live volumes to force a restore. Schema-changing failures require explicit recovery; the deployer preserves data and does not blindly roll images back across migrations. Retain the previous `.current_tag` and backup for investigation.

TLS terminates at the shared Traefik, using resolver `cloudflare`; the edge preserves HTTPS headers and the application sets Secure cookies even in demo mode. Domain DNS points to the same server as Mercury. Deployment never starts another Traefik or changes Mercury's services.

Inspect this project's containers with the command above. Change credentials through an intentional rotation procedure; editing existing PostgreSQL environment passwords does not rotate database roles. Scheduled/offsite backups and production operational hardening remain separate work before real customer data is accepted.
