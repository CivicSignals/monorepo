"""Shared base for the wave-3 federal entity-directory bulk imports (doc 18 §1
cat. G, §5 wave 3 #16-#18; TODO D8).

``nces_ccd`` (K-12 districts/schools), ``ipeds`` (higher-ed institutions), and
``census_gov`` (Census of Governments — ~90,000 government entities) are all the
same mechanical shape (doc 16 §16, all **Tier 1**, all public domain):

* The source publishes a periodic **bulk file** (CSV/zip) — an *entity directory*,
  not a signal stream (doc 16 §2/§5/§6: ``n/a (entity directory)``). normalize()
  resolves each row to an Entity by its stable external id (NCES id, IPEDS unit id,
  FIPS code — doc 18 §2.4 step 5 "always prefer that path").
* Ingestion is **checkpointed**: ``discover`` cheaply checks whether the file
  changed and, if not, emits zero pointers so a re-run does no work (doc 18 §2.1
  bulk). The wave-1 :class:`BulkDownloadConnector` already implements exactly this,
  so these connectors are thin subclasses that:

  - register under their own type name (so the registry / a recipe can select
    ``nces_ccd`` rather than the generic ``bulk_download`` — clearer provenance and
    a place to attach source-specific defaults / id semantics), and
  - expose a source-specific config (``file_url`` defaulted to the published dump,
    plus the checkpoint strategy) that maps onto the generic
    :class:`BulkDownloadConfig` for the shared discover/fetch logic.

Keeping these as their own connectors (vs. three ``bulk_download`` recipes) means
D12's mega-recipes name the real source and the entity-id semantics live with the
connector, while the brittle download/checkpoint code stays in one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Clock, Fetcher, Recipe, SourcePointer

from .base import Connector
from .bulk_download import (
    BulkDownloadConfig,
    Checkpoint,
    CheckpointStore,
    _NullCheckpointStore,
    content_sha256,
)
from .http_static import HttpStaticConfig, HttpxFetcher


class BulkEntityDirectoryConfig(BaseModel):
    """Per-recipe config for a federal entity-directory bulk import.

    Subclassed only to set a source-specific default ``file_url``; the fields
    mirror the per-connector JSON Schema block.
    """

    model_config = ConfigDict(extra="forbid")

    # URL of the published dump. Each connector defaults this to its source's
    # current bulk file; a recipe overrides to pin a specific vintage/year.
    file_url: str
    # The federal directory dumps ship as zip archives (CCD/IPEDS/COG); a recipe
    # overrides for a source that publishes a flat CSV/JSON.
    format: Literal["csv", "json", "zip"] = "zip"
    checkpoint_strategy: Literal["etag", "last_modified", "content_hash"] = "etag"


class BulkEntityDirectoryConnector(Connector):
    """Base for the wave-3 entity-directory bulk imports (doc 18 §1 cat. G, §5 wave 3).

    Delegates the change-detection / fetch wiring to the wave-1 bulk-download
    machinery; the only per-source variation is the config (default file URL +
    id semantics, documented per subclass). Pass a :class:`CheckpointStore` to
    enable change detection; the default treats every run as changed (correct,
    just not optimized) so the connector works before D4 wires the durable marker.
    """

    config_model: ClassVar[type[BaseModel] | None] = BulkEntityDirectoryConfig
    #: Signal types this directory produces — none; it populates Entities only.
    produces_entities_only: ClassVar[bool] = True

    def __init__(
        self,
        recipe: Recipe,
        *,
        clock: Clock | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        super().__init__(recipe, clock=clock)
        self._checkpoint_store: CheckpointStore = checkpoint_store or _NullCheckpointStore()

    def _bulk_config(self) -> BulkDownloadConfig:
        config = self.config
        assert isinstance(config, BulkEntityDirectoryConfig)
        return BulkDownloadConfig(
            file_url=config.file_url,
            format=config.format,
            checkpoint_strategy=config.checkpoint_strategy,
        )

    def build_fetcher(self) -> Fetcher:
        # The dump is downloaded over plain HTTP; the static fetcher already does
        # conditional GET + retries, which is exactly the bulk-download fetch.
        return HttpxFetcher(HttpStaticConfig())

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Check whether the bulk file changed; emit one pointer if so, else none.

        ``seed_urls`` are ignored — the file URL lives in ``connector_config``.
        Returns an empty list when the checkpoint shows the file is unchanged
        (doc 18 §2.1 bulk: "If unchanged, output zero pointers").
        """
        config = self._bulk_config()
        checkpoint = self._checkpoint_store.load(self.recipe.recipe_id)
        if not self._has_changed(config, checkpoint):
            return []
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=config.file_url,
                hint_metadata={
                    "checkpoint_strategy": config.checkpoint_strategy,
                    "format": config.format,
                    "entity_directory": True,
                },
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
            return False
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
    "BulkEntityDirectoryConfig",
    "BulkEntityDirectoryConnector",
]
