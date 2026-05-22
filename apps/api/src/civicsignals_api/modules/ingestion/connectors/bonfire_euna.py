"""``bonfire_euna`` — Bonfire / Euna Supplier Network procurement (doc 18 §1 cat. E,
§5 wave 3 #19; D8).

Bonfire (now part of the Euna Supplier Network) is a government/education
e-procurement marketplace used by many cities, counties, and school districts
(doc 16 §2, §16: Tier 1, public listings scrapable; respect ToS on login-gated
content — ``supplier.eunasolutions.com``). It is a **multi-tenant** platform: one
connector covers every tenant via a per-tenant public opportunity-listing
endpoint, selected in the recipe's ``connector_config``.

Wave 3 treats Bonfire's public **opportunity-listing data API** (the JSON the
tenant portal's listing page itself calls) as the source — keeping us on the
documented public-listing path (doc 16 §16) rather than scraping rendered HTML.
This connector therefore reuses the wave-1 :class:`RestApiPagerFetcher` for
rate-limit + retry, pages the tenant's listing endpoint, and emits **one pointer
per opportunity** projected to a stable HTML ``<dl>`` (doc 18 §3.4) re-served from
a :class:`StaticBodyFetcher`, so the runner extracts a recipe's fields with
``dd[data-key='<field>']`` (``title``, ``due_date``, ``reference``, …) without a
second network hit.

Login-gated tenants (some Bonfire content sits behind a free vendor login) are out
of scope: a recipe targeting one is rejected in PR review per the legal posture
(doc 16 §16, §18) — we ingest public listings only and never bypass a login.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Clock, Fetcher, Recipe, SourcePointer

from ._platform import (
    StaticBodyFetcher,
    dotted_get,
    parse_json_body,
    records_to_bodies,
    trim_base,
    with_query,
)
from .base import Connector, ConnectorError, register
from .rest_api_pager import AuthConfig, PaginationConfig, RestApiPagerConfig, RestApiPagerFetcher


class BonfireEunaConfig(BaseModel):
    """Per-recipe ``connector_config.bonfire_euna`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    # Tenant portal base URL, e.g. ``https://cityofx.bonfirehub.com``.
    portal_url: str
    # Public opportunity-listing API path appended to ``portal_url``.
    listing_path: str = "/api/portal/opportunities"
    # Dotted path to the opportunities array in the response envelope.
    items_path: str = "opportunities"
    page_size: int = 50
    max_pages: int = 50
    rate_limit_per_minute: int | None = None


def _listing_url(config: BonfireEunaConfig) -> str:
    return f"{trim_base(config.portal_url)}{config.listing_path}"


@register
class BonfireEunaConnector(Connector):
    """Bonfire / Euna Supplier Network procurement connector (doc 18 §1 cat. E, §5 wave 3 #19)."""

    name: ClassVar[str] = "bonfire_euna"
    config_model: ClassVar[type[BaseModel] | None] = BonfireEunaConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: BonfireEunaConfig) -> RestApiPagerConfig:
        """Adapt Bonfire onto the generic pager (rate-limit + retry only).

        ``discover`` drives offset pagination itself; the public listing endpoint
        needs no auth (login-gated tenants are out of scope — see module docstring).
        """
        return RestApiPagerConfig(
            base_url=_listing_url(config),
            pagination=PaginationConfig(mode="offset", items_path=config.items_path),
            auth=AuthConfig(mode="none"),
            rate_limit_per_minute=config.rate_limit_per_minute,
        )

    def build_fetcher(self) -> Fetcher:
        return StaticBodyFetcher(self._bodies)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Page the public opportunity listing and emit one pointer per bid (doc 18 §2.1)."""
        config = self.config
        assert isinstance(config, BonfireEunaConfig)
        listing_url = _listing_url(config)

        fetcher = RestApiPagerFetcher(self._pager_config(config), clock=self.clock)
        records: list[dict[str, Any]] = []
        try:
            for page_index in range(config.max_pages):
                params = {
                    "limit": str(config.page_size),
                    "offset": str(page_index * config.page_size),
                }
                url = with_query(listing_url, params)
                status, body, _headers = fetcher.fetch(
                    url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status in (401, 403):
                    raise ConnectorError(
                        f"bonfire_euna access denied for {config.portal_url!r} (HTTP {status}); "
                        "we ingest public listings only and never bypass a login "
                        "(doc 16 §18; doc 18 §4 cat. E)"
                    )
                payload = parse_json_body(body, connector="bonfire_euna")
                page_items = dotted_get(payload, config.items_path)
                page_list = page_items if isinstance(page_items, list) else []
                if not page_list:
                    break
                records.extend(r for r in page_list if isinstance(r, dict))
                if len(page_list) < config.page_size:
                    break  # short page -> last page
        finally:
            fetcher.close()

        return self._records_to_pointers(config, records)

    def _records_to_pointers(
        self, config: BonfireEunaConfig, records: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        listing_url = _listing_url(config)

        def url_for(index: int, record: dict[str, Any]) -> str:
            opp_id = record.get("id") or record.get("reference")
            row_ref = str(opp_id) if isinstance(opp_id, str | int) else str(index)
            return with_query(listing_url, {"opportunity": row_ref})

        urls = records_to_bodies(records, url_for=url_for, bodies=self._bodies)
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
                hint_metadata={"portal_url": config.portal_url},
            )
            for url in urls
        ]


__all__ = [
    "BonfireEunaConfig",
    "BonfireEunaConnector",
]
