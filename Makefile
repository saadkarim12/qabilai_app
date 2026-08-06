.PHONY: help dev build deploy-hetzner

help:
	@echo "Available targets:"
	@echo "  dev             next dev (local)"
	@echo "  build           next build (local)"
	@echo "  deploy-hetzner  Build-on-VM deploy to the Hetzner server (see DEPLOYMENT.md)"

dev:
	npm run dev

build:
	npm run build

# Build-on-VM deploy: rsync repo -> build image on the server (NEXT_PUBLIC_KABIL_API
# baked in) -> start the `fe` service on the backend's kabil_default network so
# Caddy proxies app.<ip>.sslip.io to it. Config + first-time setup live in
# DEPLOYMENT.md. Override the API URL or host via env vars, e.g.
# `NEXT_PUBLIC_KABIL_API=https://api.example.com make deploy-hetzner`.
deploy-hetzner:
	./scripts/deploy-hetzner.sh
