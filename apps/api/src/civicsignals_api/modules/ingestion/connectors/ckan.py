"""``ckan`` — CKAN open-data catalog API (doc 18 §1 cat. E, §5 wave 2 #10; D7).

CKAN is the open-source catalog software behind Data.gov and many state/city
data portals (doc 16 §16). One connector covers every CKAN tenant: a per-tenant
recipe supplies the catalog **domain** and a datastore **resource_id**, and this
connector queries that tenant's CKAN Action API
``https://<domain>/api/3/action/datastore_search`` (doc 16 §16: "use to discover
the real datasets behind the catalog").

CKAN specifics this connector handles:

* The Action API envelope: ``{"success": true, "result": {"records": [...],
  "total": N}}`` — records live under ``result.records``; ``success: false``
  surfaces the CKAN error message.
* ``limit`` / ``offset`` pagination against ``result.total``.
* Optional ``q`` full-text filter and ``filters`` (a JSON object) so a recipe
  scopes the rows it pulls.
* Optional API key via the ``Authorization`` header (private datasets;
  ``env:NAME`` resolved at run time, never committed).

``discover`` pages the datastore (reusing the wave-1
:class:`RestApiPagerFetcher` for auth + rate-limit + retry) and emits **one
pointer per record**; each record is projected to a stable HTML ``<dl>`` (doc 18
§3.4 extraction is selector-based) re-served from a :class:`StaticBodyFetcher`, so
the runner extracts a recipe's fields with ``dd[data-key='<field>']`` without a
second network hit. Seam for D12: per-tenant params live wholly in
``connector_config``.
"""

from __future__ import annotations

import json
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


class CkanConfig(BaseModel):
    """Per-recipe ``connector_config.ckan`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    resource_id: str
    # Optional CKAN datastore_search scoping.
    q: str | None = None
    filters: dict[str, Any] | None = None
    page_size: int = 100
    max_pages: int = 50
    rate_limit_per_minute: int | None = None
    # CKAN API key for private datasets (``env:NAME`` resolved at run time).
    api_key: str | None = None


def _action_url(config: CkanConfig) -> str:
    domain = trim_base(config.domain)
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return f"{domain}/api/3/action/datastore_search"


@register
class CkanConnector(Connector):
    """CKAN open-data catalog connector (doc 18 §1 cat. E, §5 wave 2 #10)."""

    name: ClassVar[str] = "ckan"
    config_model: ClassVar[type[BaseModel] | None] = CkanConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: CkanConfig) -> RestApiPagerConfig:
        """Adapt the CKAN params onto the generic REST pager's config.

        Only auth + rate limiting are reused from the pager; ``discover`` drives
        pagination itself (CKAN nests records under ``result.records``). The API
        key rides as the ``Authorization`` header per CKAN convention.
        """
        return RestApiPagerConfig(
            base_url=_action_url(config),
            pagination=PaginationConfig(mode="offset", items_path="result.records"),
            auth=AuthConfig(
                mode="api_key" if config.api_key else "none",
                header="Authorization",
                value=config.api_key,
            ),
            rate_limit_per_minute=config.rate_limit_per_minute,
        )

    def build_fetcher(self) -> Fetcher:
        return StaticBodyFetcher(self._bodies)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Page the CKAN datastore and emit one pointer per record (doc 18 §2.1)."""
        config = self.config
        assert isinstance(config, CkanConfig)
        action_url = _action_url(config)
        base_params = self._base_params(config)

        fetcher = RestApiPagerFetcher(self._pager_config(config), clock=self.clock)
        records: list[dict[str, Any]] = []
        try:
            for page_index in range(config.max_pages):
                params = {
                    **base_params,
                    "limit": str(config.page_size),
                    "offset": str(page_index * config.page_size),
                }
                url = with_query(action_url, params)
                status, body, _headers = fetcher.fetch(
                    url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status in (401, 403):
                    raise ConnectorError(
                        f"ckan auth failed for {config.domain!r} (HTTP {status}); "
                        "check the API key (doc 18 §4 cat. A)"
                    )
                payload = parse_json_body(body, connector="ckan")
                if isinstance(payload, dict) and payload.get("success") is False:
                    error = payload.get("error")
                    raise ConnectorError(f"ckan datastore_search error: {error}")
                page_records = dotted_get(payload, "result.records")
                page_list = page_records if isinstance(page_records, list) else []
                if not page_list:
                    break
                records.extend(r for r in page_list if isinstance(r, dict))
                if len(page_list) < config.page_size:
                    break  # short page -> last page
        finally:
            fetcher.close()

        return self._records_to_pointers(config, records)

    def _base_params(self, config: CkanConfig) -> dict[str, str]:
        params: dict[str, str] = {"resource_id": config.resource_id}
        if config.q:
            params["q"] = config.q
        if config.filters:
            params["filters"] = json.dumps(config.filters, sort_keys=True)
        return params

    def _records_to_pointers(
        self, config: CkanConfig, records: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        action_url = _action_url(config)

        def url_for(index: int, record: dict[str, Any]) -> str:
            row_id = record.get("_id")
            row_ref = str(row_id) if isinstance(row_id, str | int) else str(index)
            return with_query(action_url, {"resource_id": config.resource_id, "row": row_ref})

        urls = records_to_bodies(records, url_for=url_for, bodies=self._bodies)
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
                hint_metadata={"resource_id": config.resource_id},
            )
            for url in urls
        ]


__all__ = [
    "CkanConfig",
    "CkanConnector",
]
