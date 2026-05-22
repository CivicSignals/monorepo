"""``usaspending`` — USAspending.gov award API (doc 18 §1 cat. A, §5 wave 3 #14; D8).

USAspending.gov (Treasury) publishes ~$10T+ of federal contract awards, grants,
and loans behind a free, **no-key** REST API (doc 16 §2, §11, §16:
``https://api.usaspending.gov/``). Wave 3 ingests it for parity / historical
spend on SLED entities receiving federal funds (doc 16 §2 Tier 2). Public domain,
no auth — so unlike SAM.gov / Grants.gov *scraping* (v2; see ``samgov`` /
``grantsgov`` stubs) this open-data API is in MVP scope.

USAspending specifics this connector handles (vs. the generic ``rest_api_pager``):

* The award-search response envelope ``{"results": [...], "page_metadata":
  {"hasNext": true, "page": N}}`` — awards live under ``results``.
* ``page`` / ``limit`` pagination, advanced via ``page_metadata.hasNext`` (we stop
  on the first ``hasNext: false`` or short page).
* Per-recipe scoping params (``award_type_codes`` etc.) passed through verbatim as
  query params so a recipe targets the awards it cares about. The default
  ``award_type_codes`` keeps the example self-contained.

``discover`` pages the search endpoint (reusing the wave-1
:class:`RestApiPagerFetcher` for rate-limit + retry/backoff), then emits **one
pointer per award**; each is projected to a stable HTML ``<dl>`` (doc 18 §3.4
extraction is selector-based) re-served from a :class:`StaticBodyFetcher`, so the
runner extracts a recipe's fields with ``dd[data-key='<field>']`` without a second
network hit. Seam for D12: per-tenant params live wholly in ``connector_config``.
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


class UsaspendingConfig(BaseModel):
    """Per-recipe ``connector_config.usaspending`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    # API base; the default is the public production host (no key, public domain).
    base_url: str = "https://api.usaspending.gov"
    # Search endpoint path appended to ``base_url`` (the award-search GET endpoint).
    endpoint: str = "/api/v2/search/spending_by_award/"
    # Extra query params scoping the search (award_type_codes, recipient_id, …).
    query_params: dict[str, str] | None = None
    page_size: int = 100
    max_pages: int = 50
    rate_limit_per_minute: int | None = None


def _search_url(config: UsaspendingConfig) -> str:
    return f"{trim_base(config.base_url)}{config.endpoint}"


@register
class UsaspendingConnector(Connector):
    """USAspending.gov award-search connector (doc 18 §1 cat. A, §5 wave 3 #14)."""

    name: ClassVar[str] = "usaspending"
    config_model: ClassVar[type[BaseModel] | None] = UsaspendingConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: UsaspendingConfig) -> RestApiPagerConfig:
        """Adapt USAspending onto the generic pager (rate-limit + retry only).

        ``discover`` drives pagination itself (awards nest under ``results`` and the
        next-page signal is ``page_metadata.hasNext``); the API is keyless so auth
        is ``none``.
        """
        return RestApiPagerConfig(
            base_url=_search_url(config),
            pagination=PaginationConfig(mode="page", items_path="results"),
            auth=AuthConfig(mode="none"),
            rate_limit_per_minute=config.rate_limit_per_minute,
        )

    def build_fetcher(self) -> Fetcher:
        return StaticBodyFetcher(self._bodies)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Page the award search and emit one pointer per award (doc 18 §2.1 REST)."""
        config = self.config
        assert isinstance(config, UsaspendingConfig)
        search_url = _search_url(config)
        base_params = dict(config.query_params or {})

        fetcher = RestApiPagerFetcher(self._pager_config(config), clock=self.clock)
        records: list[dict[str, Any]] = []
        try:
            for page_index in range(config.max_pages):
                params = {
                    **base_params,
                    "limit": str(config.page_size),
                    "page": str(page_index + 1),  # USAspending pages are 1-based
                }
                url = with_query(search_url, params)
                status, body, _headers = fetcher.fetch(
                    url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status in (401, 403):
                    raise ConnectorError(
                        f"usaspending auth failed for {search_url!r} (HTTP {status}); "
                        "the public API is keyless — check the endpoint (doc 18 §4 cat. A)"
                    )
                payload = parse_json_body(body, connector="usaspending")
                page_results = dotted_get(payload, "results")
                page_list = page_results if isinstance(page_results, list) else []
                if not page_list:
                    break
                records.extend(r for r in page_list if isinstance(r, dict))
                has_next = dotted_get(payload, "page_metadata.hasNext")
                if has_next is False:
                    break  # the API explicitly says this is the last page
                if has_next is None and len(page_list) < config.page_size:
                    break  # no hasNext signal and a short page -> last page
        finally:
            fetcher.close()

        return self._records_to_pointers(config, records)

    def _records_to_pointers(
        self, config: UsaspendingConfig, records: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        search_url = _search_url(config)

        def url_for(index: int, record: dict[str, Any]) -> str:
            # Awards carry a stable generated id; deep-link by it when present.
            award_id = record.get("generated_internal_id") or record.get("Award ID")
            row_ref = str(award_id) if isinstance(award_id, str | int) else str(index)
            return with_query(search_url, {"award": row_ref})

        urls = records_to_bodies(records, url_for=url_for, bodies=self._bodies)
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
                hint_metadata={"endpoint": config.endpoint},
            )
            for url in urls
        ]


__all__ = [
    "UsaspendingConfig",
    "UsaspendingConnector",
]
