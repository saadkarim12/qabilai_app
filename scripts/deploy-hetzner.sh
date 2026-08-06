#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Build-on-VM deploy for the Kabil frontend (Next.js) on the Hetzner server.
#
# What it does (idempotent):
#   1. rsyncs this repo to the server (excluding node_modules, .next, .git, .env*)
#   2. builds the image on the VM with NEXT_PUBLIC_KABIL_API baked in and starts
#      the `fe` service, joining the backend's kabil_default network so Caddy
#      can reverse-proxy app.<ip>.sslip.io -> fe:3000
#
# Usage:
#   ./scripts/deploy-hetzner.sh
#
# Override via env vars:
#   NEXT_PUBLIC_KABIL_API=https://api.example.com ./scripts/deploy-hetzner.sh
#   DEPLOY_HOST=1.2.3.4 ./scripts/deploy-hetzner.sh
#
# If `next build` OOMs on the 4 GB VM, build locally instead and ship the image:
#   docker build --build-arg NEXT_PUBLIC_KABIL_API=<url> -t kabil-frontend:latest .
#   docker save kabil-frontend:latest | ssh -i <key> root@<host> 'docker load'
#   ssh ... 'cd /opt/kabil-frontend && docker compose -f docker-compose.prod.yml up -d'
# See DEPLOYMENT.md.
# ---------------------------------------------------------------------------
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-167.233.172.142}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_KEY="${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/kabil-frontend}"
NEXT_PUBLIC_KABIL_API="${NEXT_PUBLIC_KABIL_API:-https://api.167-233-172-142.sslip.io}"
COMPOSE="docker compose -f docker-compose.prod.yml"

SSH=(ssh -i "$DEPLOY_KEY" "${DEPLOY_USER}@${DEPLOY_HOST}")
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Target: ${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_DIR}"
echo "==> API URL baked into build: ${NEXT_PUBLIC_KABIL_API}"

echo "==> Ensuring rsync on server"
"${SSH[@]}" "command -v rsync >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq rsync)"
"${SSH[@]}" "mkdir -p ${REMOTE_DIR}"

echo "==> Syncing code"
rsync -az --delete -e "ssh -i ${DEPLOY_KEY}" \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '.next' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '*.log' \
  ./ "${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_DIR}/"

echo "==> Building + starting fe (first build is slow; leans on swap)"
"${SSH[@]}" "cd ${REMOTE_DIR} && NEXT_PUBLIC_KABIL_API='${NEXT_PUBLIC_KABIL_API}' ${COMPOSE} up -d --build"

echo "==> Pruning dangling images"
"${SSH[@]}" "docker image prune -f >/dev/null 2>&1 || true"

echo "==> Waiting for FE health"
sleep 8
if "${SSH[@]}" "cd ${REMOTE_DIR} && ${COMPOSE} exec -T fe node -e \"require('http').get('http://localhost:3000/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))\" >/dev/null 2>&1"; then
  echo "==> FE healthy ✅"
else
  echo "==> FE not healthy yet. Check logs:"
  echo "    ssh -i ${DEPLOY_KEY} ${DEPLOY_USER}@${DEPLOY_HOST} 'cd ${REMOTE_DIR} && ${COMPOSE} logs --tail=50 fe'"
fi

echo "==> Done → https://app.167-233-172-142.sslip.io"
