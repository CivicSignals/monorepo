"""``nces_ccd`` / ``ipeds`` / ``census_gov`` entity-directory bulk imports
(doc 18 §1 cat. G, §5 wave 3 #16-#18; D8).

These reuse the wave-1 bulk-download checkpointing: ``discover`` emits a pointer
only when the file changed (etag / last-modified / content-hash), zero otherwise.
Covers per-source default file URLs, the checkpoint short-circuit, the
entity-directory hint, and config validation — all against a mocked httpx
transport (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion.connectors._bulk_entity_directory import (
    BulkEntityDirectoryConnector,
)
from civicsignals_api.modules.ingestion.connectors.bulk_download import Checkpoint, content_sha256
from civicsignals_api.modules.ingestion.connectors.census_gov import (
    CensusGovConfig,
    CensusGovConnector,
)
from civicsignals_api.modules.ingestion.connectors.http_static import HttpxFetcher
from civicsignals_api.modules.ingestion.connectors.ipeds import IpedsConfig, IpedsConnector
from civicsignals_api.modules.ingestion.connectors.nces_ccd import NcesCcdConfig, NcesCcdConnector
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeValidationError
from civicsignals_api.modules.recipes.services import Recipe, SourcePointer

Handler = Callable[[httpx.Request], httpx.Response]


class _Store:
    def __init__(self, checkpoint: Checkpoint) -> None:
        self._checkpoint = checkpoint

    def load(self, recipe_id: str) -> Checkpoint:
        return self._checkpoint


def _recipe(connector: str, connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": f"{connector}-test",
            "connector": connector,
            "version": 1,
            "entity": {"entity_id": "x:1", "name": "Example", "state": "TX"},
            "connector_config": {connector: connector_config},
            "fields": {"name": {"selectors": ["dd[data-key='NAME']"], "required": True}},
            "signal_types": [],
        }
    )


def _discover(connector: BulkEntityDirectoryConnector, handler: Handler) -> list[SourcePointer]:
    with mock.patch.object(
        HttpxFetcher,
        "_get_client",
        lambda self: httpx.Client(transport=httpx.MockTransport(handler)),
    ):
        return list(connector.discover([]))


# ---------------------------------------------------------------------------
# Default file URLs (per source)
# ---------------------------------------------------------------------------


def test_default_file_urls_per_source() -> None:
    assert "nces.ed.gov/ccd" in NcesCcdConfig().file_url
    assert "ipeds" in IpedsConfig().file_url
    assert "census.gov" in CensusGovConfig().file_url
    # All default to a checkpointed zip download.
    assert NcesCcdConfig().format == "zip"
    assert CensusGovConfig().checkpoint_strategy == "etag"


# ---------------------------------------------------------------------------
# Checkpoint behavior (shared base) — first run vs unchanged
# ---------------------------------------------------------------------------


def test_first_run_emits_entity_directory_pointer() -> None:
    connector = NcesCcdConnector(_recipe("nces_ccd", {}), checkpoint_store=_Store(Checkpoint()))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="LEAID,LEA_NAME\n1,Austin ISD\n", headers={"ETag": '"v1"'})

    pointers = _discover(connector, handler)
    assert len(pointers) == 1
    assert pointers[0].hint_metadata["entity_directory"] is True
    assert pointers[0].hint_metadata["checkpoint_strategy"] == "etag"


def test_unchanged_etag_emits_nothing() -> None:
    connector = IpedsConnector(
        _recipe("ipeds", {"checkpoint_strategy": "etag"}),
        checkpoint_store=_Store(Checkpoint(etag='"same"')),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data", headers={"ETag": '"same"'})

    assert _discover(connector, handler) == []


def test_content_hash_unchanged_emits_nothing() -> None:
    body = "FIPS,NAME\n4805000,City of Austin\n"
    connector = CensusGovConnector(
        _recipe("census_gov", {"checkpoint_strategy": "content_hash"}),
        checkpoint_store=_Store(Checkpoint(content_hash=content_sha256(body.encode("utf-8")))),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    assert _discover(connector, handler) == []


def test_override_file_url() -> None:
    connector = NcesCcdConnector(
        _recipe("nces_ccd", {"file_url": "https://nces.ed.gov/ccd/custom-2024.zip"}),
        checkpoint_store=_Store(Checkpoint()),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "custom-2024.zip" in str(request.url)
        return httpx.Response(200, text="x", headers={"ETag": '"v1"'})

    pointers = _discover(connector, handler)
    assert pointers[0].url == "https://nces.ed.gov/ccd/custom-2024.zip"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_config_rejects_unknown_key() -> None:
    with pytest.raises(RecipeValidationError):
        _recipe("nces_ccd", {"not_a_field": 1})


def test_config_models_default_to_csv_directory() -> None:
    assert NcesCcdConfig().checkpoint_strategy == "etag"
    assert IpedsConfig().file_url.endswith(".zip")
    assert CensusGovConfig().file_url.endswith(".zip")
