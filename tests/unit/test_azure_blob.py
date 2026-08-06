"""Unit tests for ``src.integrations.azure_blob``.

The Azure SDK and Redis are both mocked; no network. Fakes capture inputs
so we can assert on path construction, sha256 dedup, SAS parameters, and
cache behaviour.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions

from src.config import settings
from src.integrations import azure_blob

# --- Fixtures / fakes ------------------------------------------------------


class _FakeBlobClient:
    """Stand-in for ``BlobClient`` returned by ``get_blob_client()``."""

    def __init__(self, *, container: str, blob: str, raise_on_delete: bool = False) -> None:
        self._container = container
        self._blob = blob
        self.url = f"https://kabilfaketest.blob.core.windows.net/{container}/{blob}"
        self.upload_calls: list[dict[str, Any]] = []
        self.delete_calls = 0
        self._raise_on_delete = raise_on_delete

    async def upload_blob(self, data: bytes, *, overwrite: bool, content_settings: Any) -> None:
        self.upload_calls.append(
            {
                "data": data,
                "overwrite": overwrite,
                "content_settings": content_settings,
            }
        )

    async def delete_blob(self) -> None:
        self.delete_calls += 1
        if self._raise_on_delete:
            raise ResourceNotFoundError("Blob not found")


class _FakeBlobServiceClient:
    def __init__(self, *, delete_raises: bool = False) -> None:
        self.delete_raises = delete_raises
        self.blob_clients: dict[tuple[str, str], _FakeBlobClient] = {}

    def get_blob_client(self, *, container: str, blob: str) -> _FakeBlobClient:
        key = (container, blob)
        if key not in self.blob_clients:
            self.blob_clients[key] = _FakeBlobClient(
                container=container, blob=blob, raise_on_delete=self.delete_raises
            )
        return self.blob_clients[key]


class _FakeRedis:
    """Minimal async stand-in for the redis methods used by azure_blob."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ex))


_FAKE_CONN_STRING = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=kabilfaketest;"
    "AccountKey=abc123base64paddedvalue==;"
    "EndpointSuffix=core.windows.net"
)


@pytest.fixture
def configured_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin connection string + container to known fake values."""
    monkeypatch.setattr(settings, "azure_blob_connection_string", _FAKE_CONN_STRING)
    monkeypatch.setattr(settings, "azure_blob_container", "kabil-cvs")
    # ``_account_credentials`` is still ``@lru_cache``d (pure CPU parse),
    # so we clear it to pick up the new conn string. The async-client
    # factories are no longer cached.
    azure_blob._account_credentials.cache_clear()


@pytest.fixture
def fake_service(
    monkeypatch: pytest.MonkeyPatch, configured_settings: None
) -> _FakeBlobServiceClient:
    service = _FakeBlobServiceClient()
    monkeypatch.setattr(azure_blob, "_blob_service_client", lambda: service)
    return service


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch, configured_settings: None) -> _FakeRedis:
    redis = _FakeRedis()
    monkeypatch.setattr(azure_blob, "_redis_client", lambda: redis)
    return redis


# --- PDF validation --------------------------------------------------------


def test_validate_pdf_accepts_real_magic_bytes() -> None:
    azure_blob._validate_pdf(b"%PDF-1.4\nrest of file")  # no exception


def test_validate_pdf_rejects_non_pdf() -> None:
    with pytest.raises(azure_blob.PdfMagicBytesError):
        azure_blob._validate_pdf(b"\x00\x00\x00\x00not a pdf")


def test_validate_pdf_rejects_empty_bytes() -> None:
    with pytest.raises(azure_blob.PdfMagicBytesError):
        azure_blob._validate_pdf(b"")


def test_validate_pdf_rejects_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drop the cap to 1 MB so the test stays small and fast.
    monkeypatch.setattr(settings, "cv_max_file_size_mb", 1)
    oversize = b"%PDF-" + b"\x00" * (azure_blob.BYTES_PER_MB + 1)
    with pytest.raises(azure_blob.FileTooLargeError):
        azure_blob._validate_pdf(oversize)


def test_validate_pdf_size_check_runs_before_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Oversized + invalid magic — size error should win because it's cheaper
    # and tells the caller something more useful.
    monkeypatch.setattr(settings, "cv_max_file_size_mb", 1)
    bad = b"\x00\x00" * (azure_blob.BYTES_PER_MB)
    with pytest.raises(azure_blob.FileTooLargeError):
        azure_blob._validate_pdf(bad)


# --- upload_pdf ------------------------------------------------------------


async def test_upload_pdf_returns_uploaded_blob_with_correct_path_and_sha(
    fake_service: _FakeBlobServiceClient, fake_redis: _FakeRedis
) -> None:
    candidate_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    pdf = b"%PDF-1.4\n%hello world"

    result = await azure_blob.upload_pdf(pdf, candidate_id=candidate_id)

    expected_sha = hashlib.sha256(pdf).hexdigest()
    assert result.sha256 == expected_sha
    assert result.path == f"cvs/{candidate_id}/{expected_sha}.pdf"
    assert result.url.endswith(result.path)
    # No SAS appended to the raw URL.
    assert "?" not in result.url


async def test_upload_pdf_writes_overwrite_true_and_content_type(
    fake_service: _FakeBlobServiceClient, fake_redis: _FakeRedis
) -> None:
    candidate_id = uuid.uuid4()
    pdf = b"%PDF-1.4 body"

    await azure_blob.upload_pdf(pdf, candidate_id=candidate_id)

    [blob_client] = fake_service.blob_clients.values()
    [call] = blob_client.upload_calls
    assert call["overwrite"] is True
    assert call["content_settings"].content_type == azure_blob.PDF_CONTENT_TYPE
    assert call["data"] == pdf


async def test_upload_pdf_same_bytes_lands_at_same_path(
    fake_service: _FakeBlobServiceClient, fake_redis: _FakeRedis
) -> None:
    """Re-uploading byte-identical PDFs is idempotent at the path level."""
    candidate_id = uuid.uuid4()
    pdf = b"%PDF-1.4 dedup"

    first = await azure_blob.upload_pdf(pdf, candidate_id=candidate_id)
    second = await azure_blob.upload_pdf(pdf, candidate_id=candidate_id)

    assert first.path == second.path
    assert first.sha256 == second.sha256
    # Two upload calls landed on the same BlobClient instance.
    [blob_client] = fake_service.blob_clients.values()
    assert len(blob_client.upload_calls) == 2


async def test_upload_pdf_rejects_non_pdf_before_network(
    fake_service: _FakeBlobServiceClient, fake_redis: _FakeRedis
) -> None:
    with pytest.raises(azure_blob.PdfMagicBytesError):
        await azure_blob.upload_pdf(b"not a pdf", candidate_id=uuid.uuid4())
    assert fake_service.blob_clients == {}


async def test_upload_pdf_rejects_oversize_before_network(
    fake_service: _FakeBlobServiceClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cv_max_file_size_mb", 1)
    oversize = b"%PDF-" + b"x" * (azure_blob.BYTES_PER_MB + 1)
    with pytest.raises(azure_blob.FileTooLargeError):
        await azure_blob.upload_pdf(oversize, candidate_id=uuid.uuid4())
    assert fake_service.blob_clients == {}


# --- get_signed_url --------------------------------------------------------


async def test_get_signed_url_calls_generate_blob_sas_with_read_only(
    fake_service: _FakeBlobServiceClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_generate(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "sig=fake&se=2026-05-23T12%3A30%3A00Z"

    monkeypatch.setattr(azure_blob, "generate_blob_sas", fake_generate)

    url = await azure_blob.get_signed_url("cvs/abc/def.pdf")

    assert captured["account_name"] == "kabilfaketest"
    assert captured["account_key"] == "abc123base64paddedvalue=="
    assert captured["container_name"] == "kabil-cvs"
    assert captured["blob_name"] == "cvs/abc/def.pdf"
    assert isinstance(captured["permission"], BlobSasPermissions)
    assert captured["permission"].read is True
    assert captured["permission"].write is False
    assert captured["permission"].delete is False
    assert url.endswith("?sig=fake&se=2026-05-23T12%3A30%3A00Z")


async def test_get_signed_url_expiry_matches_ttl(
    fake_service: _FakeBlobServiceClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_generate(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "sig=x"

    monkeypatch.setattr(azure_blob, "generate_blob_sas", fake_generate)

    before = datetime.now(UTC)
    await azure_blob.get_signed_url("cvs/x/y.pdf", ttl_minutes=30)
    after = datetime.now(UTC)

    expiry: datetime = captured["expiry"]
    assert expiry.tzinfo is UTC
    assert before + timedelta(minutes=30) <= expiry <= after + timedelta(minutes=30)


async def test_get_signed_url_caches_with_eighty_percent_ttl(
    fake_service: _FakeBlobServiceClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(azure_blob, "generate_blob_sas", lambda **_: "sig=cached")

    await azure_blob.get_signed_url("cvs/c/k.pdf", ttl_minutes=15)

    [(key, value, ex)] = fake_redis.set_calls
    assert key.startswith(azure_blob.SAS_CACHE_KEY_PREFIX)
    assert "cvs/c/k.pdf" in key
    assert "ttl15" in key
    assert value.endswith("?sig=cached")
    # 15 min * 60 s * 0.8 = 720 s.
    assert ex == int(15 * 60 * azure_blob.SAS_CACHE_TTL_RATIO)
    assert ex == 720


async def test_get_signed_url_cache_hit_skips_sas_generation(
    fake_service: _FakeBlobServiceClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def fake_generate(**_: Any) -> str:
        nonlocal call_count
        call_count += 1
        return "sig=fresh"

    monkeypatch.setattr(azure_blob, "generate_blob_sas", fake_generate)

    first = await azure_blob.get_signed_url("cvs/x/y.pdf", ttl_minutes=15)
    assert call_count == 1

    second = await azure_blob.get_signed_url("cvs/x/y.pdf", ttl_minutes=15)
    # Second call must short-circuit on the cached value — no new SAS minted.
    assert call_count == 1
    assert second == first


async def test_get_signed_url_different_ttls_have_separate_cache_keys(
    fake_service: _FakeBlobServiceClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def fake_generate(**_: Any) -> str:
        nonlocal call_count
        call_count += 1
        return f"sig=call{call_count}"

    monkeypatch.setattr(azure_blob, "generate_blob_sas", fake_generate)

    await azure_blob.get_signed_url("cvs/p/q.pdf", ttl_minutes=15)
    await azure_blob.get_signed_url("cvs/p/q.pdf", ttl_minutes=60)

    # Same blob_path, different TTL → two distinct cache keys + two SAS mints.
    assert call_count == 2
    assert len(fake_redis.set_calls) == 2


# --- delete_pdf ------------------------------------------------------------


async def test_delete_pdf_calls_sdk_delete(
    fake_service: _FakeBlobServiceClient, fake_redis: _FakeRedis
) -> None:
    await azure_blob.delete_pdf("cvs/a/b.pdf")
    [blob_client] = fake_service.blob_clients.values()
    assert blob_client.delete_calls == 1


async def test_delete_pdf_swallows_not_found(
    monkeypatch: pytest.MonkeyPatch, configured_settings: None, fake_redis: _FakeRedis
) -> None:
    service = _FakeBlobServiceClient(delete_raises=True)
    monkeypatch.setattr(azure_blob, "_blob_service_client", lambda: service)

    # No exception propagates even though the SDK raises ResourceNotFoundError.
    await azure_blob.delete_pdf("cvs/missing/blob.pdf")


# --- Configuration errors --------------------------------------------------


def test_blob_service_client_raises_when_conn_string_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "azure_blob_connection_string", None)
    with pytest.raises(azure_blob.AzureBlobConfigError, match="not configured"):
        azure_blob._blob_service_client()


def test_account_credentials_raises_when_conn_string_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "azure_blob_connection_string", None)
    azure_blob._account_credentials.cache_clear()
    with pytest.raises(azure_blob.AzureBlobConfigError, match="not configured"):
        azure_blob._account_credentials()
    azure_blob._account_credentials.cache_clear()


def test_account_credentials_raises_when_account_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Connection string with AccountName but no AccountKey.
    monkeypatch.setattr(
        settings,
        "azure_blob_connection_string",
        "DefaultEndpointsProtocol=https;AccountName=foo;EndpointSuffix=core.windows.net",
    )
    azure_blob._account_credentials.cache_clear()
    with pytest.raises(azure_blob.AzureBlobConfigError, match="AccountKey"):
        azure_blob._account_credentials()
    azure_blob._account_credentials.cache_clear()


def test_account_credentials_raises_when_account_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "azure_blob_connection_string",
        "DefaultEndpointsProtocol=https;AccountKey=abc==;EndpointSuffix=core.windows.net",
    )
    azure_blob._account_credentials.cache_clear()
    with pytest.raises(azure_blob.AzureBlobConfigError, match="AccountName"):
        azure_blob._account_credentials()
    azure_blob._account_credentials.cache_clear()


def test_account_credentials_handles_base64_key_with_equals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AccountKey is base64 and almost always contains ``=`` padding;
    our parser must split on the first ``=`` only."""
    monkeypatch.setattr(
        settings,
        "azure_blob_connection_string",
        "DefaultEndpointsProtocol=https;"
        "AccountName=demo;"
        "AccountKey=abc/def+ghi==;"
        "EndpointSuffix=core.windows.net",
    )
    azure_blob._account_credentials.cache_clear()
    name, key = azure_blob._account_credentials()
    assert name == "demo"
    assert key == "abc/def+ghi=="
    azure_blob._account_credentials.cache_clear()


# --- Constants pinned by the architecture doc ------------------------------


def test_constants_match_doc() -> None:
    assert azure_blob.PDF_MAGIC_BYTES == b"%PDF-"
    assert azure_blob.DEFAULT_SAS_TTL_MINUTES == 15
    assert azure_blob.SAS_CACHE_TTL_RATIO == 0.8
    assert azure_blob.CV_BLOB_PATH_TEMPLATE == "cvs/{candidate_id}/{sha256}.pdf"
    assert azure_blob.PDF_CONTENT_TYPE == "application/pdf"
