"""``samgov`` / ``grantsgov`` — deferred-to-v2 federal stubs (doc 18 §5 wave 3
#12-#13; D8; TODO.md "Out of scope").

Federal coverage / SAM.gov / Grants.gov scraping is out of MVP scope. These
connectors are **registered but not wired**: ``discover`` emits no pointers and
the fetcher refuses any fetch. This file pins that contract so a future change
can't silently turn on live federal scraping.
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.ingestion.connectors import connector_for
from civicsignals_api.modules.ingestion.connectors._deferred_federal import (
    DeferredConnectorError,
    DeferredFederalConnector,
)
from civicsignals_api.modules.ingestion.connectors.grantsgov import (
    GrantsgovConfig,
    GrantsgovConnector,
)
from civicsignals_api.modules.ingestion.connectors.samgov import SamgovConfig, SamgovConnector
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe


def _recipe(connector: str, connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": f"{connector}-test",
            "connector": connector,
            "version": 1,
            "entity": {"name": "Federal", "state": "DC"},
            "connector_config": {connector: connector_config},
            "fields": {"title": {"selectors": ["h1"], "required": True}},
            "signal_types": ["rfp_posted"],
        }
    )


@pytest.mark.parametrize(
    ("connector", "cls"),
    [("samgov", SamgovConnector), ("grantsgov", GrantsgovConnector)],
)
def test_resolves_in_registry(connector: str, cls: type[DeferredFederalConnector]) -> None:
    instance = connector_for(_recipe(connector, {}))
    assert isinstance(instance, cls)
    assert isinstance(instance, DeferredFederalConnector)
    assert cls.deferred_to_v2 is True


@pytest.mark.parametrize("connector", ["samgov", "grantsgov"])
def test_discover_emits_no_pointers(connector: str) -> None:
    instance = connector_for(_recipe(connector, {}))
    # Even with seed URLs, a scheduled run is a no-op (never reaches outward).
    assert instance.discover(["https://sam.gov/seed", "https://grants.gov/seed"]) == []


@pytest.mark.parametrize("connector", ["samgov", "grantsgov"])
def test_fetcher_refuses_live_fetch(connector: str) -> None:
    instance = connector_for(_recipe(connector, {}))
    fetcher = instance.build_fetcher()
    with pytest.raises(DeferredConnectorError, match="deferred to v2"):
        fetcher.fetch("https://api.sam.gov/opportunities", user_agent="ua", max_redirects=5)


def test_config_is_permissive_for_forward_looking_recipes() -> None:
    # The deferred config tolerates extra keys so a recipe sketching the intended
    # v2 params still validates (it just doesn't run in MVP).
    sam = SamgovConfig.model_validate({"api_key": "env:SAM_KEY", "ptype": "o"})
    grants = GrantsgovConfig.model_validate({"oppStatuses": "posted"})
    assert sam is not None
    assert grants is not None
