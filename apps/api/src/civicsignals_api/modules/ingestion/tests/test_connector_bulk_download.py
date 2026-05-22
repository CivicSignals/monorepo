"""``bulk_download`` connector (doc 18 §1 cat. G, §5 wave 1 #5; D6).

Verifies checkpointing: ``discover`` emits a pointer only when the file changed
since the last-seen marker (etag / last-modified / content-hash), and zero
pointers when unchanged. The file HTTP fetch is mocked (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx

from civicsignals_api.modules.ingestion.connectors.bulk_download import (
    BulkDownloadConfig,
    BulkDownloadConnector,
    Checkpoint,
    content_sha256,
)
from civicsignals_api.modules.ingestion.connectors.http_static import HttpxFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe, SourcePointer

Handler = Callable[[httpx.Request], httpx.Response]

_FILE_URL = "https://nces.ed.gov/ccd/data.csv"


class _Store:
    """In-memory :class:`CheckpointStore` for the tests."""

    def __init__(self, checkpoint: Checkpoint) -> None:
        self._checkpoint = checkpoint

    def load(self, recipe_id: str) -> Checkpoint:
        return self._checkpoint


def _recipe(checkpoint_strategy: str = "etag") -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "nces-ccd",
            "connector": "bulk_download",
            "version": 1,
            "entity": {"name": "NCES"},
            "connector_config": {
                "bulk_download": {
                    "file_url": _FILE_URL,
                    "format": "csv",
                    "checkpoint_strategy": checkpoint_strategy,
                }
            },
        }
    )


def _connector(*, checkpoint: Checkpoint, strategy: str = "etag") -> BulkDownloadConnector:
    return BulkDownloadConnector(_recipe(strategy), checkpoint_store=_Store(checkpoint))


def _discover(connector: BulkDownloadConnector, handler: Handler) -> list[SourcePointer]:
    with mock.patch.object(
        HttpxFetcher,
        "_get_client",
        lambda self: httpx.Client(transport=httpx.MockTransport(handler)),
    ):
        return list(connector.discover([]))


# ---------------------------------------------------------------------------
# First run (no prior marker) -> always changed
# ---------------------------------------------------------------------------


def test_first_run_emits_pointer() -> None:
    # No prior checkpoint -> the file is "new" -> one pointer.
    connector = _connector(checkpoint=Checkpoint())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="a,b,c\n1,2,3\n", headers={"ETag": '"v1"'})

    pointers = _discover(connector, handler)
    assert len(pointers) == 1
    assert pointers[0].url == _FILE_URL
    assert pointers[0].hint_metadata["checkpoint_strategy"] == "etag"
    assert pointers[0].hint_metadata["format"] == "csv"


# ---------------------------------------------------------------------------
# ETag strategy
# ---------------------------------------------------------------------------


def test_etag_unchanged_304_emits_nothing() -> None:
    connector = _connector(checkpoint=Checkpoint(etag='"v1"'), strategy="etag")

    def handler(request: httpx.Request) -> httpx.Response:
        # The static fetcher sends If-None-Match from its own validator cache only
        # after a prior 200; for a fresh conditional probe we simulate the server
        # answering 304 to confirm "unchanged".
        return httpx.Response(304)

    pointers = _discover(connector, handler)
    assert pointers == []  # unchanged -> zero pointers (doc 18 §2.1 bulk)


def test_etag_changed_emits_pointer() -> None:
    connector = _connector(checkpoint=Checkpoint(etag='"old"'), strategy="etag")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="new data", headers={"ETag": '"new"'})

    pointers = _discover(connector, handler)
    assert len(pointers) == 1  # etag differs -> changed


def test_etag_same_value_emits_nothing() -> None:
    connector = _connector(checkpoint=Checkpoint(etag='"same"'), strategy="etag")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data", headers={"ETag": '"same"'})

    pointers = _discover(connector, handler)
    assert pointers == []  # server returned the same etag -> unchanged


# ---------------------------------------------------------------------------
# Last-Modified strategy
# ---------------------------------------------------------------------------


def test_last_modified_changed_emits_pointer() -> None:
    connector = _connector(
        checkpoint=Checkpoint(last_modified="Mon, 01 Jan 2024 00:00:00 GMT"),
        strategy="last_modified",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="data", headers={"Last-Modified": "Tue, 02 Jan 2024 00:00:00 GMT"}
        )

    pointers = _discover(connector, handler)
    assert len(pointers) == 1


def test_last_modified_unchanged_emits_nothing() -> None:
    stamp = "Mon, 01 Jan 2024 00:00:00 GMT"
    connector = _connector(checkpoint=Checkpoint(last_modified=stamp), strategy="last_modified")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data", headers={"Last-Modified": stamp})

    pointers = _discover(connector, handler)
    assert pointers == []


# ---------------------------------------------------------------------------
# Content-hash strategy (dedupe)
# ---------------------------------------------------------------------------


def test_content_hash_unchanged_emits_nothing() -> None:
    body = "stable,bulk,file\n1,2,3\n"
    connector = _connector(
        checkpoint=Checkpoint(content_hash=content_sha256(body.encode("utf-8"))),
        strategy="content_hash",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    pointers = _discover(connector, handler)
    assert pointers == []  # byte-identical -> deduped, zero pointers


def test_content_hash_changed_emits_pointer() -> None:
    connector = _connector(
        checkpoint=Checkpoint(content_hash=content_sha256(b"old contents")),
        strategy="content_hash",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="brand new contents")

    pointers = _discover(connector, handler)
    assert len(pointers) == 1


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_config_parses_and_seed_ignored() -> None:
    connector = _connector(checkpoint=Checkpoint())
    assert isinstance(connector.config, BulkDownloadConfig)
    assert connector.config.file_url == _FILE_URL
    assert connector.config.checkpoint_strategy == "etag"


def test_default_store_treats_every_run_as_changed() -> None:
    # No checkpoint_store -> _NullCheckpointStore -> always "changed" (correct,
    # just unoptimized) so the connector works before D4 wires the durable marker.
    connector = BulkDownloadConnector(_recipe("etag"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data", headers={"ETag": '"x"'})

    pointers = _discover(connector, handler)
    assert len(pointers) == 1


def test_content_sha256_matches_hashlib() -> None:
    import hashlib

    data = b"some bulk file"
    assert content_sha256(data) == hashlib.sha256(data).hexdigest()
