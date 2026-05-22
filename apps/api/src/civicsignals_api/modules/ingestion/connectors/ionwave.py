"""``ionwave`` — IonWave e-procurement platform (doc 18 §1 cat. E, §5 wave 3 #20;
D8).

IonWave is an e-procurement platform popular with school districts and small local
governments (doc 16 §2, §16: Tier 1, country-wide, public listings scrapable via
the per-tenant subdomain pattern). A **multi-tenant** platform: one connector
covers every tenant via a per-tenant public bid-listing endpoint, selected in the
recipe's ``connector_config``.

Wave 3 treats IonWave's public **bid-listing data API** (the JSON the tenant
portal's listing renders from) as the source — staying on the documented
public-listing path (doc 16 §16) rather than scraping the rendered HTML. This
connector reuses the wave-1 :class:`RestApiPagerFetcher` for rate-limit + retry,
pages the tenant's bid-listing endpoint, and emits **one pointer per bid**
projected to a stable HTML ``<dl>`` (doc 18 §3.4) re-served from a
:class:`StaticBodyFetcher`, so the runner extracts a recipe's fields with
``dd[data-key='<field>']`` (``title``, ``bid_number``, ``close_date``, …) without a
second network hit. Seam for D12: per-tenant params live wholly in
``connector_config`` + the recipe's ``tenant`` block.
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


class IonwaveConfig(BaseModel):
    """Per-recipe ``connector_config.ionwave`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    # Tenant portal base URL, e.g. ``https://cityofx.ionwave.net``.
    portal_url: str
    # Public bid-listing API path appended to ``portal_url``.
    listing_path: str = "/Bids/Public/List"
    # Dotted path to the bids array in the response envelope.
    items_path: str = "bids"
    page_size: int = 50
    max_pages: int = 50
    rate_limit_per_minute: int | None = None


def _listing_url(config: IonwaveConfig) -> str:
    return f"{trim_base(config.portal_url)}{config.listing_path}"


@register
class IonwaveConnector(Connector):
    """IonWave e-procurement connector (doc 18 §1 cat. E, §5 wave 3 #20)."""

    name: ClassVar[str] = "ionwave"
    config_model: ClassVar[type[BaseModel] | None] = IonwaveConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: IonwaveConfig) -> RestApiPagerConfig:
        """Adapt IonWave onto the generic pager (rate-limit + retry only).

        ``discover`` drives offset pagination itself; the public bid listing needs
        no auth.
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
        """Page the public bid listing and emit one pointer per bid (doc 18 §2.1)."""
        config = self.config
        assert isinstance(config, IonwaveConfig)
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
                        f"ionwave access denied for {config.portal_url!r} (HTTP {status}); "
                        "we ingest public listings only and never bypass a login "
                        "(doc 16 §18; doc 18 §4 cat. E)"
                    )
                payload = parse_json_body(body, connector="ionwave")
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
        self, config: IonwaveConfig, records: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        listing_url = _listing_url(config)

        def url_for(index: int, record: dict[str, Any]) -> str:
            bid_id = record.get("bid_number") or record.get("id")
            row_ref = str(bid_id) if isinstance(bid_id, str | int) else str(index)
            return with_query(listing_url, {"bid": row_ref})

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
    "IonwaveConfig",
    "IonwaveConnector",
]
