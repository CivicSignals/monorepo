"""``socrata`` — Socrata SODA open-data API (doc 18 §1 cat. E, §5 wave 2 #9; D7).

Socrata (Tyler Tech) powers hundreds of state/city open-data portals
(``data.cityofnewyork.us``, ``data.colorado.gov``, ``opendata.dc.gov`` …). One
connector covers them all: a per-tenant recipe supplies the portal **domain** and
a **dataset id** (the 4x4 resource id), and this connector queries that tenant's
SODA endpoint ``https://<domain>/resource/<dataset_id>.json`` (doc 16 §16:
"write one Socrata connector and we can query any of hundreds of … portals").

SODA specifics this connector handles (vs. the generic ``rest_api_pager``):

* **Pagination** is ``$limit`` / ``$offset`` (SoQL), not ``page`` / ``cursor``.
* **App token** auth via the ``X-App-Token`` header (optional but lifts the
  anonymous rate limit) — resolved from the environment, never committed.
* Optional **SoQL** ``$where`` / ``$order`` / ``$select`` clauses so a recipe
  scopes to "rows changed since X" and orders deterministically.

``discover`` issues the paged SODA queries (reusing the wave-1
:class:`RestApiPagerFetcher` for auth + rate-limit + retry), then emits **one
pointer per row**; each row is projected to a stable HTML ``<dl>`` (doc 18 §3.4
extraction is selector-based) and re-served from a :class:`StaticBodyFetcher`, so
the runner extracts a recipe's fields with ``dd[data-key='<column>']`` without a
second network hit. Seam for D12 (mega-recipes): the per-tenant params live wholly
in ``connector_config`` + ``tenant``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Clock, Fetcher, Recipe, SourcePointer

from ._platform import (
    StaticBodyFetcher,
    parse_json_body,
    records_to_bodies,
    trim_base,
    with_query,
)
from .base import Connector, ConnectorError, register
from .rest_api_pager import (
    AuthConfig,
    PaginationConfig,
    RestApiPagerConfig,
    RestApiPagerFetcher,
)


class SocrataConfig(BaseModel):
    """Per-recipe ``connector_config.socrata`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    dataset_id: str
    # SoQL query scoping (https://dev.socrata.com/docs/queries/). All optional.
    where: str | None = None
    order: str | None = None
    select: str | None = None
    page_size: int = 100
    max_pages: int = 50
    rate_limit_per_minute: int | None = None
    # Socrata app token (``env:NAME`` resolved at run time). Optional — anonymous
    # access works but is throttled harder.
    app_token: str | None = None


def _resource_url(config: SocrataConfig) -> str:
    domain = trim_base(config.domain)
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return f"{domain}/resource/{config.dataset_id}.json"


def _soql_params(config: SocrataConfig) -> dict[str, str]:
    params: dict[str, str] = {}
    if config.where:
        params["$where"] = config.where
    if config.order:
        params["$order"] = config.order
    if config.select:
        params["$select"] = config.select
    return params


@register
class SocrataConnector(Connector):
    """Socrata SODA open-data connector (doc 18 §1 cat. E, §5 wave 2 #9)."""

    name: ClassVar[str] = "socrata"
    config_model: ClassVar[type[BaseModel] | None] = SocrataConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        # Per-row HTML projections, filled by ``discover`` and served by the
        # fetcher (held by reference — see StaticBodyFetcher / doc note above).
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: SocrataConfig) -> RestApiPagerConfig:
        """Adapt the SODA params onto the generic REST pager's config.

        SODA uses ``$offset`` / ``$limit`` (offset mode) and the response *is* the
        array of rows (``items_path`` is the root). The app token rides as the
        ``X-App-Token`` header.
        """
        return RestApiPagerConfig(
            base_url=_resource_url(config),
            pagination=PaginationConfig(
                mode="offset",
                items_path="",  # the SODA body is the rows array at the root
                offset_param="$offset",
                limit_param="$limit",
                page_size=config.page_size,
                max_pages=config.max_pages,
            ),
            auth=AuthConfig(
                mode="api_key" if config.app_token else "none",
                header="X-App-Token",
                value=config.app_token,
            ),
            rate_limit_per_minute=config.rate_limit_per_minute,
        )

    def build_fetcher(self) -> Fetcher:
        """The runner fetches the per-row HTML projections built in ``discover``."""
        return StaticBodyFetcher(self._bodies)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Page the SODA endpoint and emit one pointer per row (doc 18 §2.1 REST)."""
        config = self.config
        assert isinstance(config, SocrataConfig)
        pager_config = self._pager_config(config)
        resource_url = _resource_url(config)
        soql = _soql_params(config)

        fetcher = RestApiPagerFetcher(pager_config, clock=self.clock)
        rows: list[dict[str, Any]] = []
        try:
            for page_index in range(config.max_pages):
                params = {
                    "$limit": str(config.page_size),
                    "$offset": str(page_index * config.page_size),
                    **soql,
                }
                url = with_query(resource_url, params)
                status, body, _headers = fetcher.fetch(
                    url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status in (401, 403):
                    raise ConnectorError(
                        f"socrata auth failed for {config.domain!r} (HTTP {status}); "
                        "check the app token (doc 18 §4 cat. A)"
                    )
                payload = parse_json_body(body, connector="socrata")
                page_rows = payload if isinstance(payload, list) else []
                if not page_rows:
                    break
                rows.extend(r for r in page_rows if isinstance(r, dict))
                if len(page_rows) < config.page_size:
                    break  # short page -> last page
        finally:
            fetcher.close()

        return self._rows_to_pointers(config, rows)

    def _rows_to_pointers(
        self, config: SocrataConfig, rows: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        resource_url = _resource_url(config)

        def url_for(index: int, row: dict[str, Any]) -> str:
            # A stable, per-row pointer URL (deep-link into the dataset row). The
            # ``:id`` system field is row-stable when present; else fall back to
            # the row index within this run.
            row_id = row.get(":id") if isinstance(row.get(":id"), str) else None
            return with_query(resource_url, {"$offset": str(index), "row": row_id or str(index)})

        urls = records_to_bodies(rows, url_for=url_for, bodies=self._bodies)
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
                hint_metadata={"dataset_id": config.dataset_id},
            )
            for url in urls
        ]


__all__ = [
    "SocrataConfig",
    "SocrataConnector",
]
