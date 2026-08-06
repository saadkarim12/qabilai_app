#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Build-on-VM deploy for the Kabil backend on the Hetzner server.
#
# What it does (idempotent, safe to re-run):
#   1. rsyncs this repo to the server (excluding secrets, caches, .git)
#   2. verifies .env.production exists on the server
#   3. `docker compose up -d --build` — rebuilds the image + recreates changed
#      services; the api container runs `alembic upgrade head` on boot
#   4. prunes dangling images and checks API health
#
# Usage:
#   ./scripts/deploy-hetzner.sh
#
# Override any of these via env vars if the host/IP ever changes:
#   DEPLOY_HOST=1.2.3.4 DEPLOY_USER=root ./scripts/deploy-hetzner.sh
#
# See DEPLOYMENT.md for first-time setup (creating .env.production on the server).
# ---------------------------------------------------------------------------
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-167.233.172.142}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_KEY="${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/kabil-backend}"
COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml"

SSH=(ssh -i "$DEPLOY_KEY" "${DEPLOY_USER}@${DEPLOY_HOST}")
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Target: ${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_DIR}"

echo "==> Ensuring rsync on server"
"${SSH[@]}" "command -v rsync >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq rsync)"
"${SSH[@]}" "mkdir -p ${REMOTE_DIR}"

echo "==> Syncing code"
rsync -az --delete -e "ssh -i ${DEPLOY_KEY}" \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude '.env.production' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.mypy_cache' \
  --exclude '.ruff_cache' \
  --exclude '*.log' \
  --exclude 'tests' \
  ./ "${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_DIR}/"

echo "==> Checking .env.production exists on server"
if ! "${SSH[@]}" "test -f ${REMOTE_DIR}/.env.production"; then
  echo "ERROR: ${REMOTE_DIR}/.env.production is missing on the server."
  echo "       Create it first (see DEPLOYMENT.md 'First-time setup')."
  exit 1
fi

echo "==> Building + starting stack (api migrates on boot; first build is slower)"
"${SSH[@]}" "cd ${REMOTE_DIR} && ${COMPOSE} up -d --build"

echo "==> Recreating Caddy to apply the Caddyfile"
# The Caddyfile is bind-mounted as a single file; rsync replaces it with a new
# inode, so the running container keeps serving the OLD content. `caddy reload`
# would re-read that stale in-container file. Force-recreating the container
# re-establishes the mount against the current file (~1s; certs persist in the
# caddy_data volume, so no re-issuance).
"${SSH[@]}" "cd ${REMOTE_DIR} && ${COMPOSE} up -d --force-recreate caddy"

echo "==> Pruning dangling images"
"${SSH[@]}" "docker image prune -f >/dev/null 2>&1 || true"

echo "==> Waiting for API health"
sleep 8
if "${SSH[@]}" "cd ${REMOTE_DIR} && ${COMPOSE} exec -T api curl -fsS http://localhost:8000/health >/dev/null 2>&1"; then
  echo "==> API healthy ✅"
else
  echo "==> API not healthy yet. Check logs:"
  echo "    ssh -i ${DEPLOY_KEY} ${DEPLOY_USER}@${DEPLOY_HOST} 'cd ${REMOTE_DIR} && ${COMPOSE} logs --tail=50 api'"
fi

echo "==> Done → https://api.167-233-172-142.sslip.io"
