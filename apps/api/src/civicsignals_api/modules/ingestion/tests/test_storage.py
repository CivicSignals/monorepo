"""Content-addressable S3 storage tests (D3 req 1, req 4).

Exercises :class:`RawDocumentStorage` against a ``moto``-mocked S3 so no real
network/MinIO is needed. Asserts the content-address contract: identical bytes
always land at the same key (dedupe), put/get round-trips faithfully, and
different bytes get distinct keys.
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from civicsignals_api.modules.ingestion.storage import (
    RawDocumentStorage,
    content_hash,
    key_for_hash,
)

_BUCKET = "civic-raw-test"


@pytest.fixture
def storage() -> Iterator[RawDocumentStorage]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield RawDocumentStorage(client, _BUCKET)


def test_put_returns_content_address(storage: RawDocumentStorage) -> None:
    data = b"<html><body>hello</body></html>"
    stored = storage.put_document(data, content_type="text/html")

    assert stored.content_hash == content_hash(data)
    assert stored.key == key_for_hash(stored.content_hash)
    assert stored.key.startswith("sha256/")
    assert stored.size == len(data)
    assert stored.content_type == "text/html"


def test_put_get_round_trip(storage: RawDocumentStorage) -> None:
    data = b"raw fetched bytes \x00\x01\x02"
    stored = storage.put_document(data)
    assert storage.get_document(stored.key) == data


def test_same_bytes_same_key_dedupes(storage: RawDocumentStorage) -> None:
    data = b"identical content"
    first = storage.put_document(data, content_type="text/html")
    second = storage.put_document(data, content_type="text/html")

    # Content-addressable: identical bytes -> identical key/hash, so there is
    # only ever one object for this content (a harmless idempotent overwrite).
    assert first.key == second.key
    assert first.content_hash == second.content_hash
    assert storage.get_document(first.key) == data


def test_different_bytes_distinct_keys(storage: RawDocumentStorage) -> None:
    a = storage.put_document(b"document A")
    b = storage.put_document(b"document B")
    assert a.key != b.key
    assert a.content_hash != b.content_hash


def test_exists_reflects_presence(storage: RawDocumentStorage) -> None:
    assert storage.exists(key_for_hash(content_hash(b"absent"))) is False
    stored = storage.put_document(b"present")
    assert storage.exists(stored.key) is True


def test_get_missing_key_raises(storage: RawDocumentStorage) -> None:
    with pytest.raises(ClientError) as exc_info:
        storage.get_document("sha256/deadbeef")
    # A read of an absent key surfaces as a NoSuchKey ClientError.
    assert exc_info.value.response["Error"]["Code"] == "NoSuchKey"


def test_put_if_absent_skips_upload_when_present() -> None:
    """The conditional put HEADs first and skips the PUT for already-stored bytes."""

    class _CountingClient:
        def __init__(self) -> None:
            self.puts = 0
            self._objects: dict[str, bytes] = {}

        def put_object(self, **kwargs: object) -> object:
            self.puts += 1
            self._objects[str(kwargs["Key"])] = b""
            return {}

        def head_object(self, **kwargs: object) -> object:
            if str(kwargs["Key"]) not in self._objects:
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {}

        def get_object(self, **kwargs: object) -> object:  # pragma: no cover - unused
            return {}

    client = _CountingClient()
    store = RawDocumentStorage(client, "bucket")
    data = b"same bytes"
    first = store.put_document_if_absent(data)
    second = store.put_document_if_absent(data)
    assert first.key == second.key
    # Uploaded exactly once despite two calls — the second HEAD found it present.
    assert client.puts == 1


def test_exists_reraises_non_404() -> None:
    """A non-404 client error (e.g. AccessDenied) propagates rather than reading
    as 'absent', so operational problems surface."""

    class _FailingClient:
        def head_object(self, **kwargs: object) -> object:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")

        def put_object(self, **kwargs: object) -> object:  # pragma: no cover
            return {}

        def get_object(self, **kwargs: object) -> object:  # pragma: no cover
            return {}

    store = RawDocumentStorage(_FailingClient(), "bucket")
    with pytest.raises(ClientError) as exc_info:
        store.exists("sha256/whatever")
    assert exc_info.value.response["Error"]["Code"] == "AccessDenied"
