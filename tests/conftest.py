"""Shared pytest configuration.

Sets safe defaults in os.environ so importing src.config / src.main works
without a real .env file. Runs before any test module is imported.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "APP_SECRET_KEY",
    "test-secret-must-be-at-least-32-bytes-long-yes",
)
# Point at the dev database started by docker-compose. Integration tests
# touch only TEMP tables / read-only system views, so the dev database is
# safe to share until a dedicated test DB is introduced in a later step.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://kabil:kabil@localhost:5433/kabil",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
