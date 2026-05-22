"""Content-addressable raw-document storage on S3/MinIO (doc 18 §2.2, §3.6; D3).

The ingestion ``fetch`` step writes raw bytes here **before anything parses them**
(doc 18 §2.2, step 4) so the snapshot is the source of truth and extraction is
replayable against it once a recipe is fixed (doc 18 §3.6). Storage is
*content-addressable*: the object key is derived from the SHA-256 of the bytes
(``sha256/<hex>``), so identical content dedupes naturally regardless of which
recipe/URL produced it — a re-fetch of unchanged content is a no-op upload to the
same key, and the ``ingestion_raw_document`` row can dedupe on the same hash.

The boto3 client is created lazily and is **injectable** — production wires the
configured MinIO/S3 endpoint from :mod:`civicsignals_api.config`; tests pass a
``moto``-mocked or fake client so no network is touched (D3 req 4). No module
imports boto3 directly except through this seam (the storage analogue of the
single-LLM-gateway rule, doc 06 §7).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from civicsignals_api.config import Settings, get_settings

# Key namespace prefix for content-addressed objects. Keeping a single flat
# ``sha256/`` namespace (rather than the doc's illustrative
# ``{connector}/{date}/{hash}`` layout) is what makes the store *content*-
# addressable: the same bytes always land at the same key, so identical content
# fetched by two recipes is stored once. Provenance (connector, date, recipe)
# lives on the ``ingestion_raw_document`` row, not in the key.
_KEY_PREFIX = "sha256"


def content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data`` — the content address."""
    return hashlib.sha256(data).hexdigest()


def key_for_hash(hash_hex: str) -> str:
    """Return the S3 object key for a content hash (``sha256/<hex>``)."""
    return f"{_KEY_PREFIX}/{hash_hex}"


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Result of storing bytes: the content address + where/how it landed.

    ``key`` is the S3 object key (``sha256/<hash>``); ``content_hash`` is its
    SHA-256 hex digest; ``size`` is the byte length. The tuple ``(key, hash)`` is
    stable across re-uploads of identical content, which is what lets the
    ``ingestion_raw_document`` row upsert idempotently (D3 req 3).
    """

    key: str
    content_hash: str
    size: int
    content_type: str


class SupportsS3(Protocol):
    """Structural type for the slice of the boto3 S3 client this module uses.

    Declaring it as a Protocol keeps the storage service mockable without boto3
    in scope (tests pass a fake or a ``moto``-backed real client) and lets mypy
    stay strict without depending on ``mypy_boto3_s3`` stubs at runtime.
    """

    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> object: ...

    def head_object(self, **kwargs: object) -> object: ...

    def delete_object(self, **kwargs: object) -> object: ...


def build_s3_client(settings: Settings | None = None) -> SupportsS3:
    """Build a boto3 S3 client from settings (MinIO in dev, AWS in prod).

    ``s3_endpoint_url`` selects MinIO (set) vs native AWS S3 (unset). boto3 is
    imported lazily so importing this module never requires it at type-check
    time and the api image doesn't pay for it on import.
    """
    settings = settings or get_settings()
    import boto3  # lazy: only the ingest worker (and tests) construct a client

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
    )
    return client  # type: ignore[no-any-return]  # boto3 client is untyped


class RawDocumentStorage:
    """Content-addressable object store for raw fetched documents (D3 req 1).

    Wraps an injected S3 client + bucket. ``put_document`` writes bytes at their
    content address (idempotent: re-storing identical bytes overwrites the same
    key with identical content); ``get_document`` reads them back by key. The
    same bytes always produce the same key, so dedupe is inherent.
    """

    def __init__(self, client: SupportsS3, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @property
    def bucket(self) -> str:
        return self._bucket

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RawDocumentStorage:
        """Construct the storage from app settings (the production path)."""
        settings = settings or get_settings()
        return cls(build_s3_client(settings), settings.s3_raw_bucket)

    def put_document(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        precomputed_hash: str | None = None,
    ) -> StoredObject:
        """Store ``data`` at its content address and return the :class:`StoredObject`.

        The key is ``sha256/<hash>``; storing the same bytes again writes the same
        key with byte-identical content (a harmless idempotent overwrite), so
        identical content dedupes to one object. ``ContentType`` is recorded as
        object metadata for faithful round-tripping. Pass ``precomputed_hash`` to
        reuse a hash the caller already computed (avoids re-hashing large bytes).
        """
        hash_hex = precomputed_hash or content_hash(data)
        key = key_for_hash(hash_hex)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return StoredObject(
            key=key, content_hash=hash_hex, size=len(data), content_type=content_type
        )

    def put_document_if_absent(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        precomputed_hash: str | None = None,
    ) -> StoredObject:
        """Like :meth:`put_document` but skip the upload when the object is present.

        Because storage is content-addressed, an object already at ``sha256/<hash>``
        is byte-identical to ``data`` — so a re-fetch of unchanged content costs one
        HEAD instead of a PUT. Always returns the :class:`StoredObject` (the content
        address is computed regardless of whether an upload happened).
        ``precomputed_hash`` lets the caller reuse a hash it already computed.
        """
        hash_hex = precomputed_hash or content_hash(data)
        key = key_for_hash(hash_hex)
        if not self.exists(key):
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        return StoredObject(
            key=key, content_hash=hash_hex, size=len(data), content_type=content_type
        )

    def get_document(self, key: str) -> bytes:
        """Fetch the raw bytes stored at ``key`` (raises if the key is absent).

        The boto3 ``StreamingBody`` is closed after reading so the underlying HTTP
        connection is returned to the pool promptly — leaving it open in a
        long-running worker would leak sockets.
        """
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]  # type: ignore[index]  # boto3 StreamingBody
        try:
            data: bytes = body.read()
        finally:
            body.close()
        return data

    def exists(self, key: str) -> bool:
        """Return whether an object exists at ``key`` (cheap HEAD request).

        Lets the upsert path skip a redundant upload when the content-addressed
        object is already present (the bytes can't differ — the key *is* the
        hash), so a re-fetch of unchanged content costs one HEAD, not a PUT.

        Returns ``False`` only for a genuine *not-found*; any other client error
        (credentials, throttling, endpoint outage) is re-raised so an operational
        problem surfaces instead of being silently read as "absent".
        """
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            # A HEAD on a missing key surfaces as 404 / NoSuchKey / NotFound; any
            # other error code is operational and must propagate.
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


__all__ = [
    "RawDocumentStorage",
    "StoredObject",
    "SupportsS3",
    "build_s3_client",
    "content_hash",
    "key_for_hash",
]
