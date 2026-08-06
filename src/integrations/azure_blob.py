"""Azure Blob Storage wrapper for CV PDFs.

Four public coroutines:

- :func:`upload_pdf` — validates magic bytes + size, hashes the body, writes
  to ``cvs/{candidate_id}/{sha256}.pdf`` with ``overwrite=True`` (a re-upload
  of the same bytes lands at the same path and is a no-op for downstream
  consumers).
- :func:`download_pdf` — fetches a blob by path and returns its bytes. Used
  by the Step 3.9 CV processing chain to feed bytes into the text extractor.
  Raises :class:`BlobNotFoundError` on missing blob.
- :func:`get_signed_url` — returns a short-lived read-only SAS URL. The URL
  is cached in Redis for 80 % of its TTL so concurrent readers don't each
  generate a fresh signature.
- :func:`delete_pdf` — idempotent; ``ResourceNotFoundError`` is swallowed.

Configuration: ``AZURE_BLOB_CONNECTION_STRING`` provides the account name
and key; ``AZURE_BLOB_CONTAINER`` selects the container. The container is
assumed pre-created — a missing container surfaces as the SDK's own
``ResourceNotFoundError`` on the first write so the failure is loud.

The PDF bytes themselves are never logged — they're candidate CVs and may
contain PII; only the blob path and sha256 prefix appear in log records.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Final, cast

import redis.asyncio as aioredis
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from src.config import settings

logger = logging.getLogger(__name__)


# --- Constants pinned by the architecture doc -------------------------------

# A real PDF begins with the magic header ``%PDF-`` (5 bytes). Anything else
# is rejected before we waste a network round-trip on the upload.
PDF_MAGIC_BYTES: Final[bytes] = b"%PDF-"

# Object naming: one folder per candidate, file named after the body sha256
# so byte-identical re-uploads land at the same key.
CV_BLOB_PATH_TEMPLATE: Final[str] = "cvs/{candidate_id}/{sha256}.pdf"

# Default lifetime of a generated SAS, in minutes.
DEFAULT_SAS_TTL_MINUTES: Final[int] = 15

# Fraction of the SAS lifetime we cache. Cached URLs always have at least
# ``(1 - SAS_CACHE_TTL_RATIO)`` of their lifetime left, so any consumer that
# hits the cache still has a comfortable window to download.
SAS_CACHE_TTL_RATIO: Final[float] = 0.8

# Bump the ``v1`` segment when SAS generation parameters change so the next
# deploy doesn't serve stale (and now-invalid) signed URLs.
SAS_CACHE_KEY_PREFIX: Final[str] = "blob:signed_url:v1:"

BYTES_PER_MB: Final[int] = 1024 * 1024

# Content-Type header written with each PDF blob — improves direct-browser
# downloads and stops Azure from guessing.
PDF_CONTENT_TYPE: Final[str] = "application/pdf"

# Conn-string key names per Azure docs.
_CONN_STRING_ACCOUNT_NAME_KEY: Final[str] = "AccountName"
_CONN_STRING_ACCOUNT_KEY_KEY: Final[str] = "AccountKey"


# --- Exceptions -------------------------------------------------------------


class AzureBlobConfigError(RuntimeError):
    """Raised when required Azure Blob settings are missing or malformed."""


class PdfMagicBytesError(ValueError):
    """Raised when uploaded bytes don't begin with ``%PDF-``."""


class FileTooLargeError(ValueError):
    """Raised when an upload exceeds ``settings.cv_max_file_size_mb``."""


class BlobNotFoundError(LookupError):
    """Raised when :func:`download_pdf` can't find the requested blob."""


# --- Result type ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadedBlob:
    """Pointer to a freshly uploaded blob.

    ``url`` is the raw blob URL with no SAS appended — sharing it with the
    outside world requires going through :func:`get_signed_url` first.
    """

    path: str
    url: str
    sha256: str


# --- Lazy factories ---------------------------------------------------------
# Async clients (``_blob_service_client``, ``_redis_client``) are NOT cached:
# their internal aiohttp / aioredis pool is bound to the event loop that
# first used them. Celery tasks each call ``asyncio.run(...)`` so a cached
# client from a previous task would raise "Event loop is closed". The
# CPU-only ``_account_credentials`` parser stays cached — no async state.


@lru_cache(maxsize=1)
def _account_credentials() -> tuple[str, str]:
    """Return ``(account_name, account_key)`` parsed from the conn string.

    ``BlobServiceClient.from_connection_string`` stashes the credential in
    its internal ``credential`` attribute, but ``generate_blob_sas`` needs
    the raw account key as a positional argument — so we parse it once.
    """
    conn_string = settings.azure_blob_connection_string
    if not conn_string:
        raise AzureBlobConfigError(
            "AZURE_BLOB_CONNECTION_STRING is not configured; cannot use Azure Blob storage"
        )

    parts: dict[str, str] = {}
    for chunk in conn_string.split(";"):
        if "=" not in chunk:
            continue
        # Account keys are base64 and may contain ``=``, so split on the
        # first ``=`` only.
        key, value = chunk.split("=", 1)
        parts[key] = value

    account_name = parts.get(_CONN_STRING_ACCOUNT_NAME_KEY)
    account_key = parts.get(_CONN_STRING_ACCOUNT_KEY_KEY)
    if not account_name:
        raise AzureBlobConfigError(
            "AZURE_BLOB_CONNECTION_STRING is missing AccountName; cannot sign SAS URLs"
        )
    if not account_key:
        raise AzureBlobConfigError(
            "AZURE_BLOB_CONNECTION_STRING is missing AccountKey; cannot sign SAS URLs"
        )
    return account_name, account_key


def _blob_service_client() -> BlobServiceClient:
    conn_string = settings.azure_blob_connection_string
    if not conn_string:
        raise AzureBlobConfigError(
            "AZURE_BLOB_CONNECTION_STRING is not configured; cannot use Azure Blob storage"
        )
    return BlobServiceClient.from_connection_string(conn_string)


def _redis_client() -> aioredis.Redis:
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )
    return cast(aioredis.Redis, client)


# --- Helpers ----------------------------------------------------------------


def _validate_pdf(pdf_bytes: bytes) -> None:
    """Raise if ``pdf_bytes`` isn't a sensibly-sized PDF.

    Size check runs first because it's cheaper than the byte comparison and
    because a 100 MB blob of garbage shouldn't even reach the magic-byte
    check.
    """
    max_bytes = settings.cv_max_file_size_mb * BYTES_PER_MB
    if len(pdf_bytes) > max_bytes:
        raise FileTooLargeError(
            f"PDF is {len(pdf_bytes)} bytes; maximum is {max_bytes} "
            f"(cv_max_file_size_mb={settings.cv_max_file_size_mb})"
        )
    if not pdf_bytes.startswith(PDF_MAGIC_BYTES):
        raise PdfMagicBytesError("Uploaded file is not a PDF (missing %PDF- magic header)")


def _sha256_hex(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def _build_blob_path(candidate_id: uuid.UUID, sha256: str) -> str:
    return CV_BLOB_PATH_TEMPLATE.format(candidate_id=candidate_id, sha256=sha256)


def _sas_cache_key(blob_path: str, ttl_minutes: int) -> str:
    return f"{SAS_CACHE_KEY_PREFIX}{blob_path}:ttl{ttl_minutes}"


# --- Public API -------------------------------------------------------------


async def upload_pdf(pdf_bytes: bytes, *, candidate_id: uuid.UUID) -> UploadedBlob:
    """Upload ``pdf_bytes`` for ``candidate_id`` and return the blob handle.

    The blob path is deterministic in the body's sha256, so re-uploading the
    same bytes overwrites the same key (cheap no-op for the storage layer).
    """
    _validate_pdf(pdf_bytes)
    digest = _sha256_hex(pdf_bytes)
    blob_path = _build_blob_path(candidate_id, digest)

    service = _blob_service_client()
    blob_client = service.get_blob_client(container=settings.azure_blob_container, blob=blob_path)
    await blob_client.upload_blob(
        pdf_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type=PDF_CONTENT_TYPE),
    )
    logger.info(
        "azure_blob.upload",
        extra={
            "candidate_id": str(candidate_id),
            "blob_path": blob_path,
            "sha256_prefix": digest[:12],
            "size_bytes": len(pdf_bytes),
        },
    )
    return UploadedBlob(path=blob_path, url=blob_client.url, sha256=digest)


async def get_signed_url(blob_path: str, *, ttl_minutes: int = DEFAULT_SAS_TTL_MINUTES) -> str:
    """Return a read-only SAS URL valid for ``ttl_minutes`` minutes.

    Cached in Redis for 80 % of the lifetime. A Redis outage degrades to
    minting a fresh URL on every call (logged at WARNING) rather than
    failing the request.
    """
    cache_key = _sas_cache_key(blob_path, ttl_minutes)
    redis = _redis_client()

    try:
        cached = await redis.get(cache_key)
    except Exception:
        logger.warning("azure_blob.cache_get_failed", extra={"blob_path": blob_path})
        cached = None

    if cached is not None:
        logger.info("azure_blob.sas_cache_hit", extra={"blob_path": blob_path})
        # decode_responses=True returns ``str`` already; cast to keep mypy
        # happy on the Redis stub that says ``str | bytes | None``.
        return cast(str, cached)

    account_name, account_key = _account_credentials()
    expiry = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    sas_query = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=settings.azure_blob_container,
        blob_name=blob_path,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )

    service = _blob_service_client()
    blob_client = service.get_blob_client(container=settings.azure_blob_container, blob=blob_path)
    signed_url = f"{blob_client.url}?{sas_query}"

    cache_ttl_seconds = int(ttl_minutes * 60 * SAS_CACHE_TTL_RATIO)
    try:
        await redis.set(cache_key, signed_url, ex=cache_ttl_seconds)
    except Exception:
        logger.warning("azure_blob.cache_set_failed", extra={"blob_path": blob_path})

    logger.info(
        "azure_blob.sas_minted",
        extra={"blob_path": blob_path, "ttl_minutes": ttl_minutes},
    )
    return signed_url


async def download_pdf(blob_path: str) -> bytes:
    """Fetch ``blob_path`` from the configured container and return its bytes.

    Used by the CV processing chain (Step 3.9) so a worker holding only a
    blob path (off ``cv_documents.blob_url``) can hand bytes to
    :func:`src.utils.pdf.extract_text`. Bytes are not cached here — the
    text extractor maintains its own sha256-keyed Redis cache, which is
    where re-extraction savings actually compound.

    Raises:
        BlobNotFoundError: if the blob no longer exists. Treated as
            non-transient by the calling worker (no retry).
    """
    service = _blob_service_client()
    blob_client = service.get_blob_client(container=settings.azure_blob_container, blob=blob_path)
    try:
        downloader = await blob_client.download_blob()
        pdf_bytes = await downloader.readall()
    except ResourceNotFoundError as exc:
        logger.warning("azure_blob.download_missing", extra={"blob_path": blob_path})
        raise BlobNotFoundError(f"Blob not found: {blob_path}") from exc
    logger.info(
        "azure_blob.download",
        extra={"blob_path": blob_path, "size_bytes": len(pdf_bytes)},
    )
    return pdf_bytes


async def delete_pdf(blob_path: str) -> None:
    """Delete ``blob_path``; absent blobs are treated as already-deleted."""
    service = _blob_service_client()
    blob_client = service.get_blob_client(container=settings.azure_blob_container, blob=blob_path)
    try:
        await blob_client.delete_blob()
    except ResourceNotFoundError:
        logger.info("azure_blob.delete_noop", extra={"blob_path": blob_path})
        return
    logger.info("azure_blob.delete", extra={"blob_path": blob_path})
