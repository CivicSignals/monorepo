"""``bulk_download`` — periodic file fetch with checkpointing (doc 18 §1 cat. G,
§5 wave 1 #5; D6).

The source publishes periodic CSV/XLSX/JSON/zip dumps (NCES CCD, IPEDS, Census of
Governments, FIPS, NAICS). The whole point of this connector is **checkpointing**:
``discover()`` cheaply checks whether the file changed since last run and, if not,
emits **zero pointers** so a re-run does no work and processes no stale data
(doc 18 §2.1 bulk, §4 cat. G).

Three checkpoint strategies (recipe ``connector_config.bulk_download.checkpoint_strategy``):

* ``etag`` / ``last_modified`` — a conditional HEAD/GET: when the prior validator
  matches (server replies 304, or the header is unchanged), the file is unchanged.
* ``content_hash`` — fetch and hash; unchanged iff the SHA-256 matches the stored
  marker. The slowest but most robust (works when the server sends no validators).

The last-seen marker is supplied by the caller via a :class:`Checkpoint` seam
(the ingest worker reads it off the most recent ``ingestion_raw_document`` for the
recipe — D3's content hash *is* the durable marker — and D4 wires the read; the
connector stays storage-agnostic so it is unit-testable with an in-memory
checkpoint). Content dedupe at the byte level is then enforced again by
``store_raw_document`` (D3), so even a missed checkpoint can't create a duplicate.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Clock, Fetcher, Recipe, SourcePointer

from .base import Connector, register
from .http_static import HttpStaticConfig, HttpxFetcher


class BulkDownloadConfig(BaseModel):
    """Per-recipe ``connector_config.bulk_download`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    file_url: str
    format: Literal["csv", "json", "zip"] | None = None
    checkpoint_strategy: Literal["etag", "last_modified", "content_hash"] = "etag"


class Checkpoint(BaseModel):
    """The last-seen marker for a recipe's bulk file (doc 18 §2.1 bulk, §4 cat. G).

    Populated by the caller from the most recent stored fetch. Any field may be
    ``None`` on the first-ever run (no prior marker → always "changed").
    """

    model_config = ConfigDict(extra="forbid")

    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None


class CheckpointStore(Protocol):
    """Seam the caller implements to read the last-seen :class:`Checkpoint`.

    Production reads it off the latest ``ingestion_raw_document`` for the recipe
    (D3/D4); tests pass an in-memory implementation. Kept a seam so the connector
    is storage-agnostic and unit-testable offline.
    """

    def load(self, recipe_id: str) -> Checkpoint: ...


class _NullCheckpointStore:
    """Default store: no prior marker (every run sees the file as changed)."""

    def load(self, recipe_id: str) -> Checkpoint:
        return Checkpoint()


def content_sha256(data: bytes) -> str:
    """SHA-256 hex of ``data`` — the ``content_hash`` checkpoint marker."""
    return hashlib.sha256(data).hexdigest()


@register
class BulkDownloadConnector(Connector):
    """Periodic-file connector with checkpointing (doc 18 §1 cat. G, §5 wave 1 #5).

    Pass a :class:`CheckpointStore` (the caller's read of the last-seen marker) to
    enable change detection; the default :class:`_NullCheckpointStore` treats every
    run as changed (correct, just not optimized) so the connector works before D4
    wires the durable marker.
    """

    name: ClassVar[str] = "bulk_download"
    config_model: ClassVar[type[BaseModel] | None] = BulkDownloadConfig

    def __init__(
        self,
        recipe: Recipe,
        *,
        clock: Clock | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        super().__init__(recipe, clock=clock)
        self._checkpoint_store: CheckpointStore = checkpoint_store or _NullCheckpointStore()

    def build_fetcher(self) -> Fetcher:
        # The file is downloaded over plain HTTP; the static fetcher already does
        # conditional GET + retries, which is exactly the bulk-download fetch.
        return HttpxFetcher(HttpStaticConfig())

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Check whether the file changed; emit one pointer if so, else none.

        ``seed_urls`` are ignored — the file URL is ``connector_config.bulk_download
        .file_url``. Returns an empty list when the checkpoint shows the file is
        unchanged (doc 18 §2.1 bulk: "If unchanged, output zero pointers").
        """
        config = self.config
        assert isinstance(config, BulkDownloadConfig)
        checkpoint = self._checkpoint_store.load(self.recipe.recipe_id)

        if not self._has_changed(config, checkpoint):
            return []

        meta: dict[str, object] = {"checkpoint_strategy": config.checkpoint_strategy}
        if config.format is not None:
            meta["format"] = config.format
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=config.file_url,
                hint_metadata=meta,
            )
        ]

    def _has_changed(self, config: BulkDownloadConfig, checkpoint: Checkpoint) -> bool:
        """True iff the bulk file differs from the last-seen marker."""
        fetcher = HttpxFetcher(HttpStaticConfig())
        try:
            if config.checkpoint_strategy in ("etag", "last_modified"):
                return self._validator_changed(config, checkpoint, fetcher)
            return self._hash_changed(config, checkpoint, fetcher)
        finally:
            fetcher.close()

    def _validator_changed(
        self, config: BulkDownloadConfig, checkpoint: Checkpoint, fetcher: HttpxFetcher
    ) -> bool:
        prior = (
            checkpoint.etag if config.checkpoint_strategy == "etag" else checkpoint.last_modified
        )
        if prior is None:
            return True  # no prior marker -> first run -> changed
        status, _body, headers = fetcher.fetch(
            config.file_url,
            user_agent=self.recipe.fetch.user_agent,
            max_redirects=self.recipe.fetch.max_redirects,
        )
        if status == 304:
            return False  # conditional GET confirmed unchanged
        header_name = "etag" if config.checkpoint_strategy == "etag" else "last-modified"
        current = headers.get(header_name)
        if current is None:
            return True  # server stopped sending the validator -> assume changed
        return current != prior

    def _hash_changed(
        self, config: BulkDownloadConfig, checkpoint: Checkpoint, fetcher: HttpxFetcher
    ) -> bool:
        if checkpoint.content_hash is None:
            return True
        status, body, _headers = fetcher.fetch(
            config.file_url,
            user_agent=self.recipe.fetch.user_agent,
            max_redirects=self.recipe.fetch.max_redirects,
        )
        if status == 304:
            return False
        return content_sha256(body.encode("utf-8")) != checkpoint.content_hash


__all__ = [
    "BulkDownloadConfig",
    "BulkDownloadConnector",
    "Checkpoint",
    "CheckpointStore",
    "content_sha256",
]
