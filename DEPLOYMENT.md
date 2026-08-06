# Deployment — Backend (Hetzner)

**Source-of-truth runbook.** Every backend deploy follows this file. The whole
flow is CLI-only: one command rsyncs the repo to the server, rebuilds the Docker
image there, and restarts the stack.

> Frontend deploys are documented separately in the `kabil-frontend` repo's
> `DEPLOYMENT.md`.

---

## What runs where

Single Hetzner VM running a Docker Compose stack (`docker-compose.prod.yml`):

| Container | Role | Exposed to internet? |
|---|---|---|
| `caddy` | TLS termination + reverse proxy (auto Let's Encrypt) | **Yes** — ports 80/443 |
| `api` | FastAPI/uvicorn; runs `alembic upgrade head` on boot | No (Caddy proxies to it) |
| `worker` | Celery worker (`fast,slow` queues) | No |
| `postgres` | pgvector 16 (volume `pgdata`) | No — internal only |
| `redis` | cache + Celery broker/results (volume `redisdata`) | No — internal only |

- **Public API URL:** `https://api.167-233-172-142.sslip.io`
  (derived from the server IP via sslip.io → a real Let's Encrypt cert, no domain).
- Only Caddy faces the internet; Postgres/Redis are reachable only inside the
  Compose network.

## Server facts

| | |
|---|---|
| Provider | Hetzner Cloud (CX22, 2 vCPU / 4 GB, Falkenstein) |
| Host | `root@167.233.172.142` |
| OS | Ubuntu 26.04 LTS |
| SSH key | `~/.ssh/id_ed25519` (local) |
| Remote repo dir | `/opt/kabil-backend` |
| Firewall | inbound 22/80/443 only; all outbound allowed |

---

## Deploy (the normal case)

From the repo root on your Mac:

```bash
make deploy-hetzner
# or directly:
./scripts/deploy-hetzner.sh
```

That script:
1. rsyncs the repo to `/opt/kabil-backend` (excludes `.git`, `.venv`, `.env*`, caches, `tests`)
2. checks `.env.production` exists on the server
3. runs `docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build`
   — rebuilds the image and recreates only changed services; **the api container
   runs `alembic upgrade head` before serving**
4. prunes dangling images and health-checks the api

**First build is slower** (compiles the image from scratch); later deploys reuse
cached layers.

If the host/IP ever changes, override without editing the script:
```bash
DEPLOY_HOST=1.2.3.4 ./scripts/deploy-hetzner.sh
```

---

## First-time setup (once per server)

The server already has Docker + Compose installed. The only thing a fresh server
needs before the first deploy is the secrets file.

1. **Create `.env.production` on the server** (never committed; not synced up):
   ```bash
   scp -i ~/.ssh/id_ed25519 .env.production.example \
       root@167.233.172.142:/opt/kabil-backend/.env.production
   ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 \
       'nano /opt/kabil-backend/.env.production'   # fill in real secrets
   ```
   (`mkdir -p /opt/kabil-backend` first if the dir doesn't exist yet.)

   Required at minimum: `APP_SECRET_KEY` (`openssl rand -hex 32`),
   `POSTGRES_PASSWORD`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `AZURE_BLOB_CONNECTION_STRING`, `CORS_ORIGINS` (the Vercel URL),
   and the WhatsApp/Brevo keys for those features.

2. **Deploy:** `make deploy-hetzner`

3. **Seed the first admin user** (idempotent):
   ```bash
   ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 \
     'cd /opt/kabil-backend && docker compose --env-file .env.production -f docker-compose.prod.yml exec -T api python -m scripts.seed_admin'
   ```
   > Note: `scripts/` is excluded from the Docker image; if `seed_admin` isn't
   > importable in the container, run it as a one-off `docker compose run` with
   > the module piped in, or seed via a short psql insert. (See "Troubleshooting".)

4. **Point the frontend at this API:** in Vercel, set
   `NEXT_PUBLIC_KABIL_API=https://api.167-233-172-142.sslip.io` and redeploy the FE,
   and make sure `CORS_ORIGINS` on the server includes the Vercel origin.

---

## Verify a deploy

```bash
# health (from anywhere, once TLS is issued):
curl https://api.167-233-172-142.sslip.io/health

# container status + logs:
ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 \
  'cd /opt/kabil-backend && docker compose --env-file .env.production -f docker-compose.prod.yml ps'
ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 \
  'cd /opt/kabil-backend && docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 api'
```

The **first** HTTPS request may take ~10–30 s while Caddy provisions the
certificate. After that it's cached in the `caddy_data` volume.

---

## Rollback

Build-on-VM deploys from your local working tree, so "rollback" = deploy an
older commit:

```bash
git stash            # or commit current work first
git checkout <good-commit-sha>
./scripts/deploy-hetzner.sh
git checkout -        # back to where you were
```

Data (Postgres/Redis volumes) is untouched by redeploys. **Migrations do not
auto-downgrade** — if a bad deploy included a schema migration, roll it back
explicitly:
```bash
ssh ... 'cd /opt/kabil-backend && docker compose ... exec -T api alembic downgrade -1'
```

---

## Backups (TODO — see follow-up step)

Postgres data lives in the `pgdata` Docker volume. A nightly `pg_dump` →
Azure Blob cron is planned (`scripts/backup_pg.sh`) to close the "no managed
backups" gap. Until then, take a manual dump before risky changes:
```bash
ssh ... 'cd /opt/kabil-backend && docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres pg_dump -U kabil kabil' > backup_$(date +%F).sql
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `.env.production missing` error on deploy | Create it on the server (First-time setup). |
| TLS cert not issued / HTTPS fails | `docker compose ... logs caddy` — needs ports 80+443 open (firewall) and the sslip.io host resolving to the server IP. |
| api restarting / migration error | `docker compose ... logs api` — usually a bad `DATABASE_URL` or a failed migration. |
| DB connection SSL error | `DATABASE_URL` must end with `?ssl=disable` (built automatically in compose from `POSTGRES_*`). |
| CORS errors in the browser | `CORS_ORIGINS` must exactly match the Vercel origin (scheme + host, no trailing slash). |
| Out of memory during build | The 2 GB swap file covers this; if it persists, build locally + copy the image instead. |

## Moving to a real domain later

1. Point `api.yourdomain.com` (A record) at `167.233.172.142`.
2. Change the hostname in `Caddyfile` to `api.yourdomain.com`.
3. Update `APP_PUBLIC_URL` (server `.env.production`) and
   `NEXT_PUBLIC_KABIL_API` (Vercel) to the new URL; add the FE origin to
   `CORS_ORIGINS`.
4. Re-point WhatsApp/Calendly webhooks to the new API URL.
5. `make deploy-hetzner`.
