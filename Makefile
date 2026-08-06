.PHONY: help install dev worker test test-unit lint typecheck check migrate seed calendly-setup up down logs clean deploy deploy-hetzner

PYTHON := python3.12
VENV := .venv
VENV_BIN := $(VENV)/bin

help:
	@echo "Available targets:"
	@echo "  install     Create .venv and install runtime + dev deps"
	@echo "  dev         Run uvicorn with --reload"
	@echo "  worker      Run the Celery worker locally (fast+slow queues)"
	@echo "  test        Run pytest (all tests, requires DB up for integration)"
	@echo "  test-unit   Run pytest, skipping integration tests"
	@echo "  lint        Run ruff lint + format check"
	@echo "  typecheck   Run mypy --strict against src/"
	@echo "  check       Run lint + typecheck + test"
	@echo "  migrate     Apply Alembic migrations"
	@echo "  seed        Run the admin seed script (idempotent)"
	@echo "  calendly-setup  Discover Calendly URIs for interview scheduling (read-only)"
	@echo "  up          docker compose up -d --build"
	@echo "  down        docker compose down"
	@echo "  logs        Tail the API container logs"
	@echo "  deploy      Deploy api + worker to Railway (api migrates on boot)"
	@echo "  deploy-hetzner  Build-on-VM deploy to the Hetzner server (see DEPLOYMENT.md)"
	@echo "  clean       Remove caches and .venv"

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -e ".[dev]"

dev:
	$(VENV_BIN)/uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Run the Celery worker against the local broker. DB_USE_NULL_POOL=true is
# required: each task bridges sync->async via asyncio.run (a fresh event loop
# per task), and a pooled asyncpg connection is invalid across loops. Consumes
# both queues so inbound WhatsApp tasks (slow queue) are picked up.
worker:
	DB_USE_NULL_POOL=true $(VENV_BIN)/celery -A src.workers.celery_app worker --loglevel=info -Q fast,slow

test:
	$(VENV_BIN)/pytest

test-unit:
	$(VENV_BIN)/pytest -m "not integration"

lint:
	$(VENV_BIN)/ruff check src tests
	$(VENV_BIN)/ruff format --check src tests

typecheck:
	$(VENV_BIN)/mypy src

check: lint typecheck test

migrate:
	$(VENV_BIN)/alembic upgrade head

seed:
	$(VENV_BIN)/python -m scripts.seed_admin

calendly-setup:
	$(VENV_BIN)/python -m scripts.calendly_setup discover

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# Redeploy both Railway services from local code. api is deployed first so its
# entrypoint runs `alembic upgrade head` before the worker picks up new code.
# Requires the Railway CLI and a prior `railway login` (creds persist in ~/.railway).
deploy:
	railway up --service api --ci
	railway up --service worker --ci

# Build-on-VM deploy to the Hetzner server: rsync repo -> build image on the
# box -> docker compose up (api runs `alembic upgrade head` on boot). All config
# and first-time setup live in DEPLOYMENT.md. Override host via env vars, e.g.
# `DEPLOY_HOST=1.2.3.4 make deploy-hetzner`.
deploy-hetzner:
	./scripts/deploy-hetzner.sh
