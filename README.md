# Kabil.ai Backend

AI-driven hiring automation backend. See [`docs/KABIL_AI_ARCHITECTURE.md`](docs/KABIL_AI_ARCHITECTURE.md) for the full architecture and step-by-step implementation plan.

## Requirements

- Python 3.12 (pinned; CI and Docker enforce it)
- Docker + Docker Compose (for local Postgres + Redis)

## Quickstart — local dev

```bash
# 1. Create a virtualenv + install deps
make install

# 2. Copy env template and edit if needed
cp .env.example .env

# 3. Bring up Postgres + Redis (and the API container)
make up

# 4. (Optional) Run the API with hot reload outside Docker
make dev

# 5. Health check
curl http://localhost:8000/health
# => {"status":"ok","version":"0.1.0"}
```

## Bootstrap — create the first admin user

After migrations are applied, seed the single HR admin:

```bash
make seed
# or directly:
.venv/bin/python -m scripts.seed_admin
```

The script:

- Refuses (politely, exit 0) if any user already exists.
- Prompts for **email**, **full name**, and **password** (masked).
- Enforces a 12-character minimum on the password.

After it completes you can log in via `POST /auth/login` with those
credentials and exercise `/auth/me` / `/auth/logout`.

## Common make targets

| Target | Purpose |
|---|---|
| `make install` | Create `.venv` and install runtime + dev deps |
| `make dev` | Run FastAPI with uvicorn `--reload` |
| `make test` | Run the test suite |
| `make lint` | Run ruff (lint + format check) |
| `make typecheck` | Run `mypy --strict` against `src/` |
| `make migrate` | Apply Alembic migrations |
| `make seed` | Create the first admin user (interactive, idempotent) |
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |
| `make logs` | Tail the API container logs |
| `make clean` | Remove caches and `.venv` |

## Project layout

See section 6 of the architecture doc. In short:

```
src/
├── main.py            # FastAPI entrypoint
├── config.py          # Pydantic Settings
├── api/               # Routes, deps, exception handlers
├── services/          # Business logic
├── db/                # SQLAlchemy models, session, types
├── schemas/           # Pydantic I/O schemas
├── workers/           # Celery app + tasks
├── ai/                # Claude / OpenAI / prompt templates
├── agents/            # LangGraph agents
├── integrations/      # External APIs (Azure, Meta, Google, SMTP)
├── enums/             # Enums + constants (thresholds, etc.)
└── utils/             # Cross-cutting helpers
tests/
├── unit/
└── integration/
```

## Documentation

- [`docs/KABIL_AI_ARCHITECTURE.md`](docs/KABIL_AI_ARCHITECTURE.md) — system design + implementation plan
- [`docs/KABIL_AI_AZURE_PORTAL_SETUP.md`](docs/KABIL_AI_AZURE_PORTAL_SETUP.md) — Azure Blob setup walkthrough
- [`docs/frontend/`](docs/frontend/README.md) — **frontend integration guide** (API reference, enums, workflows, async/polling, TypeScript types)
- [`docs/postman/`](docs/postman) — Postman collection + environment

Interactive API docs are served by the running backend at `/docs` (Swagger UI)
and `/openapi.json`.
