"""Unit tests for the embedding service.

OpenAI and Redis are both mocked — no network. The fakes capture inputs so
we can assert on truncation behavior and cache short-circuiting.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.ai import embeddings


class _FakeEmbeddings:
    """Stand-in for ``AsyncOpenAI.embeddings``."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls: list[dict[str, Any]] = []

    async def create(self, *, model: str, input: str) -> SimpleNamespace:
        self.calls.append({"model": model, "input": input})
        return SimpleNamespace(data=[SimpleNamespace(embedding=self._vector)])


class _FakeOpenAIClient:
    def __init__(self, vector: list[float]) -> None:
        self.embeddings = _FakeEmbeddings(vector)


class _FakeRedis:
    """In-memory async stand-in for the subset of redis.asyncio we use."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: bytes | str, ex: int | None = None) -> None:
        v = value if isinstance(value, bytes) else value.encode("utf-8")
        self.store[key] = v
        self.set_calls.append((key, v, ex))


@pytest.fixture
def fake_vector() -> list[float]:
    return [0.01 * i for i in range(embeddings.EMBEDDING_DIM)]


@pytest.fixture
def fake_clients(
    monkeypatch: pytest.MonkeyPatch, fake_vector: list[float]
) -> tuple[_FakeOpenAIClient, _FakeRedis]:
    """Patch the module-level OpenAI and Redis singletons with fakes."""
    openai_client = _FakeOpenAIClient(fake_vector)
    redis_client = _FakeRedis()
    monkeypatch.setattr(embeddings, "_openai_client", lambda: openai_client)
    monkeypatch.setattr(embeddings, "_redis_client", lambda: redis_client)
    return openai_client, redis_client


async def test_returns_1536_dim_vector(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis], fake_vector: list[float]
) -> None:
    openai_client, _ = fake_clients
    result = await embeddings.embed_text("hello world")
    assert len(result) == embeddings.EMBEDDING_DIM
    assert result == fake_vector
    assert len(openai_client.embeddings.calls) == 1


async def test_short_input_is_not_modified(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis],
) -> None:
    openai_client, _ = fake_clients
    text = "A short job description for a backend role."
    await embeddings.embed_text(text)
    assert openai_client.embeddings.calls[0]["input"] == text


async def test_cache_hit_short_circuits_openai(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis], fake_vector: list[float]
) -> None:
    openai_client, redis_client = fake_clients
    text = "Senior backend engineer at a fast-growing startup."

    # First call: misses cache, hits OpenAI, writes cache.
    first = await embeddings.embed_text(text)
    assert len(openai_client.embeddings.calls) == 1
    assert len(redis_client.set_calls) == 1

    # Second call with identical text: hits cache, never touches OpenAI.
    second = await embeddings.embed_text(text)
    assert second == first == fake_vector
    assert len(openai_client.embeddings.calls) == 1, "OpenAI must not be called on cache hit"


async def test_cache_set_uses_thirty_day_ttl(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis],
) -> None:
    _, redis_client = fake_clients
    await embeddings.embed_text("anything")
    assert redis_client.set_calls
    _key, _value, ex = redis_client.set_calls[0]
    assert ex == embeddings.CACHE_TTL_SECONDS
    assert ex == 30 * 24 * 60 * 60


async def test_input_above_max_tokens_is_truncated(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis],
) -> None:
    openai_client, _ = fake_clients
    enc = embeddings._tokenizer()

    # "hello" tokenizes to a single token; concatenating ``MAX+500`` of them
    # gives a deterministically oversized input.
    oversized = " ".join(["hello"] * (embeddings.MAX_EMBEDDING_TOKENS + 500))
    assert len(enc.encode(oversized)) > embeddings.MAX_EMBEDDING_TOKENS

    await embeddings.embed_text(oversized)
    sent = openai_client.embeddings.calls[0]["input"]
    assert len(enc.encode(sent)) <= embeddings.MAX_EMBEDDING_TOKENS


async def test_empty_input_raises_value_error(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis],
) -> None:
    with pytest.raises(ValueError):
        await embeddings.embed_text("")
    with pytest.raises(ValueError):
        await embeddings.embed_text("   \n\t ")


async def test_wrong_dim_response_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_vector = [0.0] * 512  # not EMBEDDING_DIM
    monkeypatch.setattr(embeddings, "_openai_client", lambda: _FakeOpenAIClient(bad_vector))
    monkeypatch.setattr(embeddings, "_redis_client", lambda: _FakeRedis())
    with pytest.raises(RuntimeError, match="512-dim"):
        await embeddings.embed_text("hello")


async def test_cache_key_format(
    fake_clients: tuple[_FakeOpenAIClient, _FakeRedis],
) -> None:
    _, redis_client = fake_clients
    await embeddings.embed_text("specific text")
    [(key, _value, _ttl)] = redis_client.set_calls
    assert key.startswith("embedding:v1:sha256:")
    # SHA-256 produces a 64-char hex digest.
    suffix = key.removeprefix("embedding:v1:sha256:")
    assert len(suffix) == 64
    int(suffix, 16)  # must parse as hex


async def test_openai_key_missing_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling embed_text without an API key must raise OpenAIKeyMissingError,
    not crash inside the OpenAI SDK with a generic message."""
    monkeypatch.setattr(embeddings.settings, "openai_api_key", None)
    monkeypatch.setattr(embeddings, "_redis_client", lambda: _FakeRedis())
    with pytest.raises(embeddings.OpenAIKeyMissingError):
        await embeddings.embed_text("hello")
