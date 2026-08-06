"""Text-embedding service.

Single public entry point: ``embed_text``. It

1. Truncates input to ``MAX_EMBEDDING_TOKENS`` so a runaway caller can't spike
   the OpenAI bill.
2. SHA-256-hashes the *truncated* text and checks Redis — re-uploads of an
   identical JD/CV return instantly without an OpenAI call.
3. Otherwise calls ``client.embeddings.create``. The OpenAI SDK's internal
   retry handles transient errors (we pass ``max_retries=OPENAI_MAX_RETRIES``).
4. Caches the resulting vector in Redis with a 30-day TTL.

The input is never logged — it's almost always PII (candidate prose) or
contains confidential JD details.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from typing import Final, cast

import redis.asyncio as aioredis
import tiktoken
from openai import AsyncOpenAI

from src.config import settings

logger = logging.getLogger(__name__)

# --- Constants pinned by the architecture doc -------------------------------

# Dimensionality of vectors returned by ``text-embedding-3-small`` and matched
# by the HNSW index in alembic 0004 (see ``JD_EMBEDDING_DIM`` in job.py).
EMBEDDING_DIM: Final[int] = 1536

# Hard cap on tokens sent per request — doc, "prevents accidental cost spikes".
MAX_EMBEDDING_TOKENS: Final[int] = 8000

# Number of times the OpenAI SDK should retry transient errors internally.
OPENAI_MAX_RETRIES: Final[int] = 3

# Redis TTL for cached vectors — 30 days, per doc.
CACHE_TTL_SECONDS: Final[int] = 30 * 24 * 60 * 60

# Redis key shape. Bump the ``v1`` segment when the model or pre-processing
# changes so we don't serve stale vectors from cache.
CACHE_KEY_PREFIX: Final[str] = "embedding:v1:sha256:"


class OpenAIKeyMissingError(RuntimeError):
    """Raised when ``embed_text`` is called but ``OPENAI_API_KEY`` is not set."""


# --- Lazy factories ---------------------------------------------------------
# Async clients are NOT cached — see ``src/ai/claude.py`` for the full
# rationale. tl;dr: Celery's per-task ``asyncio.run`` would bind the
# httpx/aioredis pool to a loop that closes at the end of the task,
# making subsequent tasks raise "Event loop is closed". Tests patch
# these via ``monkeypatch``.


def _openai_client() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise OpenAIKeyMissingError("OPENAI_API_KEY is not configured; cannot embed text")
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        max_retries=OPENAI_MAX_RETRIES,
    )


def _redis_client() -> aioredis.Redis:
    # redis.asyncio's stubs leave ``from_url`` untyped; cast + ignore so the
    # rest of the module stays under ``mypy --strict``.
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


@lru_cache(maxsize=1)
def _tokenizer() -> tiktoken.Encoding:
    # ``cl100k_base`` is the encoding used by every ``text-embedding-3-*`` model.
    return tiktoken.get_encoding("cl100k_base")


# --- Helpers ----------------------------------------------------------------


def _truncate(text: str) -> str:
    """Return ``text`` truncated to at most ``MAX_EMBEDDING_TOKENS`` tokens.

    The output is rebuilt from the truncated token sequence so the cache key
    reflects exactly what we send to OpenAI.
    """
    enc = _tokenizer()
    tokens = enc.encode(text)
    if len(tokens) <= MAX_EMBEDDING_TOKENS:
        return text
    return enc.decode(tokens[:MAX_EMBEDDING_TOKENS])


def _cache_key(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return CACHE_KEY_PREFIX + digest


# --- Public API -------------------------------------------------------------


async def embed_text(text: str) -> list[float]:
    """Embed ``text`` and return a list of ``EMBEDDING_DIM`` floats.

    Raises:
        ValueError: if ``text`` is empty / whitespace-only.
        OpenAIKeyMissingError: if no OpenAI key is configured.
        RuntimeError: if the response dimensionality is wrong.
    """
    if not text.strip():
        raise ValueError("text must be non-empty")

    truncated = _truncate(text)
    key = _cache_key(truncated)

    redis = _redis_client()
    cached = await redis.get(key)
    if cached is not None:
        logger.info("embedding.cache_hit", extra={"key_prefix": CACHE_KEY_PREFIX})
        vector: list[float] = json.loads(cached)
        return vector

    client = _openai_client()
    response = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=truncated,
    )
    vector = list(response.data[0].embedding)
    if len(vector) != EMBEDDING_DIM:
        raise RuntimeError(f"OpenAI returned a {len(vector)}-dim vector; expected {EMBEDDING_DIM}")

    tokens_sent = len(_tokenizer().encode(truncated))
    logger.info(
        "embedding.openai_call",
        extra={"model": settings.openai_embedding_model, "tokens_sent": tokens_sent},
    )
    await redis.set(key, json.dumps(vector), ex=CACHE_TTL_SECONDS)
    return vector
