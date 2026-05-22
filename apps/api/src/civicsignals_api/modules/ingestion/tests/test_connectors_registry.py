"""Connector registry + dispatch (doc 18 §1, §5; TODO D6, D7).

Asserts the wave-1 generic primitives *and* the wave-2 multi-tenant platforms
register under their type names, that ``connector_for`` resolves a recipe to its
connector and parses the per-connector config block, and that an unknown connector
/ bad config fails loudly.
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.ingestion import connectors
from civicsignals_api.modules.ingestion.connectors import (
    ConnectorError,
    UnknownConnectorError,
    connector_for,
    get_connector,
    registered_names,
)
from civicsignals_api.modules.ingestion.connectors.http_static import (
    HttpStaticConfig,
    HttpStaticConnector,
)
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe

_BASE_RECIPE: dict[str, object] = {
    "recipe_id": "reg-test",
    "connector": "http_static",
    "version": 1,
    "entity": {"name": "Test", "state": "WA"},
    "fields": {"title": {"selectors": ["h1"]}},
}


def _recipe(**overrides: object) -> Recipe:
    data = {**_BASE_RECIPE, **overrides}
    return recipes_services.parse_recipe(data)


def test_all_wave1_connectors_registered() -> None:
    assert set(registered_names()) >= {
        "http_static",
        "rss",
        "pdf_extractor",
        "rest_api_pager",
        "bulk_download",
    }


def test_all_wave2_platform_connectors_registered() -> None:
    # The six multi-tenant platforms (doc 18 §5 wave 2; D7).
    assert set(registered_names()) >= {
        "boarddocs",
        "granicus_peak",
        "civicplus",
        "socrata",
        "ckan",
        "arcgis_rest",
    }


def test_all_wave3_federal_and_niche_connectors_registered() -> None:
    # The nine wave-3 federal + niche connectors (doc 18 §5 wave 3; D8). Includes
    # the two deferred-to-v2 stubs (samgov/grantsgov) — registered but not wired.
    assert set(registered_names()) >= {
        "usaspending",
        "gdelt",
        "bonfire_euna",
        "ionwave",
        "nces_ccd",
        "ipeds",
        "census_gov",
        "samgov",
        "grantsgov",
    }


def test_deferred_federal_connectors_are_no_op_in_mvp() -> None:
    # samgov/grantsgov are deferred to v2 (TODO.md "Out of scope"): registered, but
    # discover() emits no pointers and the fetcher refuses any fetch.
    from civicsignals_api.modules.ingestion.connectors._deferred_federal import (
        DeferredConnectorError,
    )

    for name in ("samgov", "grantsgov"):
        recipe = _recipe(connector=name, connector_config={name: {}})
        connector = connector_for(recipe)
        assert connector.discover(["https://example.gov/seed"]) == []
        fetcher = connector.build_fetcher()
        with pytest.raises(DeferredConnectorError, match="deferred to v2"):
            fetcher.fetch("https://example.gov", user_agent="ua", max_redirects=5)


def test_get_connector_resolves_by_name() -> None:
    assert get_connector("http_static") is HttpStaticConnector


def test_unknown_connector_raises_with_known_list() -> None:
    with pytest.raises(UnknownConnectorError) as exc:
        get_connector("does_not_exist")
    assert "does_not_exist" in str(exc.value)
    # The error lists what *is* registered so a typo is obvious.
    assert "http_static" in str(exc.value)


def test_connector_for_instantiates_and_parses_config() -> None:
    recipe = _recipe(connector_config={"http_static": {"max_attempts": 5, "timeout_seconds": 12}})
    connector = connector_for(recipe)
    assert isinstance(connector, HttpStaticConnector)
    assert isinstance(connector.config, HttpStaticConfig)
    assert connector.config.max_attempts == 5
    assert connector.config.timeout_seconds == 12.0


def test_config_defaults_applied_when_block_absent() -> None:
    recipe = _recipe()  # no connector_config block at all
    connector = connector_for(recipe)
    assert isinstance(connector.config, HttpStaticConfig)
    assert connector.config.max_attempts == 3  # schema/model default
    assert connector.config.conditional_get is True


def test_schema_rejects_bad_config_before_dispatch() -> None:
    # The canonical JSON Schema is the first line of defense: a wrongly-typed
    # config field fails recipe parsing, before any connector sees it.
    from civicsignals_api.modules.recipes.runner import RecipeValidationError

    with pytest.raises(RecipeValidationError):
        _recipe(connector_config={"http_static": {"max_attempts": "lots"}})


def test_connector_parse_config_is_defensive() -> None:
    # parse_config is the second line of defense (a block that slipped past the
    # schema). Feed it a block that fails the Pydantic model and assert it raises a
    # ConnectorError rather than an opaque ValidationError.
    recipe = _recipe()
    object.__setattr__(recipe, "connector_config", {"http_static": {"max_attempts": "not-an-int"}})
    with pytest.raises(ConnectorError):
        HttpStaticConnector.parse_config(recipe)


def test_register_rejects_name_clash() -> None:
    from civicsignals_api.modules.ingestion.connectors.base import Connector, register

    class _Clash(Connector):
        name = "http_static"  # already taken by HttpStaticConnector

        def build_fetcher(self):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    with pytest.raises(ConnectorError, match="already registered"):
        register(_Clash)


def test_importing_package_populates_registry() -> None:
    # Importing the package (done at module top) is what registers connectors.
    assert connectors.registered_names()  # non-empty
