# Deployment — Frontend (Hetzner)

**Source-of-truth runbook.** Every frontend deploy follows this file. CLI-only:
one command rsyncs the repo to the VM, builds the Next.js image there, and starts
the `fe` container behind the shared Caddy proxy.

> The backend has its own `DEPLOYMENT.md` in the `kabil-backend` repo.

---

## What runs where

The frontend is a **Next.js 16 (App Router) SSR** app. It runs as its own Docker
Compose project (`docker-compose.prod.yml`, project `kabil-fe`) on the **same
Hetzner VM as the backend**, joining the backend's `kabil_default` network so the
backend's **Caddy** reverse-proxies to it — no second server, no Vercel.

| | |
|---|---|
| Public URL | `https://app.167-233-172-142.sslip.io` |
| Backend API | `https://api.167-233-172-142.sslip.io` |
| Container | `fe` (Next.js standalone, port 3000, internal only) |
| Host | `root@167.233.172.142`, key `~/.ssh/id_ed25519` |
| Remote dir | `/opt/kabil-frontend` |

Caddy (in the backend project) has a site block for `app.…sslip.io → fe:3000`
with its own auto Let's Encrypt cert.

## Key concept: `NEXT_PUBLIC_KABIL_API` is baked at BUILD time

Next.js inlines `NEXT_PUBLIC_*` into the client bundle during `next build`. So the
API URL is a **build argument**, not a runtime env var. The deploy passes it in;
changing it means a **rebuild** (redeploy), not just a restart. Default:
`https://api.167-233-172-142.sslip.io`.

---

## Deploy (the normal case)

```bash
make deploy-hetzner
# or:
./scripts/deploy-hetzner.sh
```

That script rsyncs the repo to `/opt/kabil-frontend`, builds the image on the VM
with the API URL baked in, and starts/updates the `fe` service. First build is
slow (installs deps + `next build`); later deploys reuse cached layers.

Override the API URL (e.g. after moving to a real domain):
```bash
NEXT_PUBLIC_KABIL_API=https://api.yourdomain.com make deploy-hetzner
```

## Prerequisite: the backend must be deployed first

The `fe` service attaches to the **external** `kabil_default` network, which the
backend compose project creates. Deploy the backend once before the first FE
deploy (it already is).

---

## If `next build` OOMs on the 4 GB VM

The build caps Node's heap (`--max-old-space-size=1536`) and leans on the 2 GB
swap, which is normally enough. If it still OOMs, **build locally and ship the
image** (no registry needed):

```bash
docker build --build-arg NEXT_PUBLIC_KABIL_API=https://api.167-233-172-142.sslip.io \
  -t kabil-frontend:latest .
docker save kabil-frontend:latest | ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 'docker load'
ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 \
  'cd /opt/kabil-frontend && docker compose -f docker-compose.prod.yml up -d'
```

---

## Verify

```bash
curl -I https://app.167-233-172-142.sslip.io        # expect 200 / redirect to login
ssh -i ~/.ssh/id_ed25519 root@167.233.172.142 \
  'cd /opt/kabil-frontend && docker compose -f docker-compose.prod.yml logs --tail=50 fe'
```

Then open `https://app.167-233-172-142.sslip.io` in a browser and log in — the
browser calls the backend directly, so the backend's `CORS_ORIGINS` must include
this app origin (it does; see backend DEPLOYMENT.md).

## Rollback

Build-on-VM builds from your local tree, so rollback = deploy an older commit:
```bash
git checkout <good-sha> && ./scripts/deploy-hetzner.sh && git checkout -
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `network kabil_default not found` | Backend isn't deployed / its stack is down. Deploy backend first. |
| CORS errors in browser console | Backend `CORS_ORIGINS` must include `https://app.167-233-172-142.sslip.io` exactly. |
| API calls go to `localhost:8000` | The build didn't get `NEXT_PUBLIC_KABIL_API`; redeploy (it's baked at build). |
| `app.…` has no cert / 525 | Caddy needs the `app.…` site block + ports 80/443 open; check backend `Caddyfile`. |
| Build OOM | Use the "build locally and ship the image" path above. |

## Moving to a real domain later

1. Point `app.yourdomain.com` at `167.233.172.142`.
2. Add/replace the `app.…` host in the backend `Caddyfile`.
3. Redeploy FE with `NEXT_PUBLIC_KABIL_API=https://api.yourdomain.com`.
4. Add `https://app.yourdomain.com` to the backend `CORS_ORIGINS`.
