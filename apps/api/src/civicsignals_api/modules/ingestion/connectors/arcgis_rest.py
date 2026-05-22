"""``arcgis_rest`` — Esri ArcGIS REST query API (doc 18 §1 cat. E, §5 wave 2 #11; D7).

Esri's ArcGIS REST Services / Open Data Hub host government GIS + tabular data —
facilities, capital projects, permits (doc 16 §16, Tier 2). One connector covers
every Esri tenant: a per-tenant recipe supplies a **layer query URL**
(``https://<host>/.../FeatureServer/<layer>``) and this connector queries its
``/query`` endpoint for the layer's features.

ArcGIS specifics this connector handles (vs. the generic ``rest_api_pager``):

* The query envelope: ``{"features": [{"attributes": {...}, "geometry": {...}}],
  "exceededTransferLimit": true}`` — the useful tabular data is each feature's
  ``attributes`` object (geometry is dropped by default).
* Pagination is ``resultOffset`` / ``resultRecordCount`` with the server's
  ``exceededTransferLimit`` flag signalling "more pages exist".
* The mandatory ``f=json`` and ``where`` params (``1=1`` fetches all rows); an
  ``error`` object in the body surfaces an ArcGIS-side failure.
* Optional token auth (secured services) via a ``token`` query param
  (``env:NAME`` resolved at run time, never committed).

``discover`` pages the layer query (reusing the wave-1 :class:`RestApiPagerFetcher`
for rate-limit + retry), flattens each feature's ``attributes`` to a record, and
emits **one pointer per feature**; each is projected to a stable HTML ``<dl>``
(doc 18 §3.4 extraction is selector-based) re-served from a
:class:`StaticBodyFetcher`, so the runner extracts a recipe's fields with
``dd[data-key='<field>']`` without a second network hit. Seam for D12: per-tenant
params live wholly in ``connector_config``.
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
from .rest_api_pager import AuthConfig, PaginationConfig, RestApiPagerConfig, RestApiPagerFetcher


class ArcgisRestConfig(BaseModel):
    """Per-recipe ``connector_config.arcgis_rest`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    # The layer endpoint, e.g. ".../FeatureServer/0" (we append "/query").
    layer_url: str
    where: str = "1=1"
    out_fields: str = "*"
    page_size: int = 100
    max_pages: int = 50
    include_geometry: bool = False
    rate_limit_per_minute: int | None = None
    # Token for secured ArcGIS services (``env:NAME`` resolved at run time).
    token: str | None = None


def _query_url(config: ArcgisRestConfig) -> str:
    return f"{trim_base(config.layer_url)}/query"


@register
class ArcgisRestConnector(Connector):
    """ArcGIS REST query connector (doc 18 §1 cat. E, §5 wave 2 #11)."""

    name: ClassVar[str] = "arcgis_rest"
    config_model: ClassVar[type[BaseModel] | None] = ArcgisRestConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: ArcgisRestConfig) -> RestApiPagerConfig:
        """Adapt the ArcGIS params onto the generic REST pager (auth + rate limit).

        The token rides as a ``token`` query param (ArcGIS convention);
        ``discover`` drives pagination itself via ``resultOffset``.
        """
        return RestApiPagerConfig(
            base_url=_query_url(config),
            pagination=PaginationConfig(mode="offset", items_path="features"),
            auth=AuthConfig(
                mode="api_key" if config.token else "none",
                param="token",
                value=config.token,
            ),
            rate_limit_per_minute=config.rate_limit_per_minute,
        )

    def build_fetcher(self) -> Fetcher:
        return StaticBodyFetcher(self._bodies)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Page the layer ``/query`` and emit one pointer per feature (doc 18 §2.1)."""
        config = self.config
        assert isinstance(config, ArcgisRestConfig)
        query_url = _query_url(config)

        fetcher = RestApiPagerFetcher(self._pager_config(config), clock=self.clock)
        auth_query = fetcher.auth_query()
        features: list[dict[str, Any]] = []
        try:
            for page_index in range(config.max_pages):
                params = {
                    "where": config.where,
                    "outFields": config.out_fields,
                    "returnGeometry": "true" if config.include_geometry else "false",
                    "f": "json",
                    "resultOffset": str(page_index * config.page_size),
                    "resultRecordCount": str(config.page_size),
                    **auth_query,
                }
                url = with_query(query_url, params)
                status, body, _headers = fetcher.fetch(
                    url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status in (401, 403):
                    raise ConnectorError(
                        f"arcgis_rest auth failed for {config.layer_url!r} (HTTP {status}); "
                        "check the token (doc 18 §4 cat. A)"
                    )
                payload = parse_json_body(body, connector="arcgis_rest")
                if isinstance(payload, dict) and "error" in payload:
                    raise ConnectorError(f"arcgis_rest query error: {payload['error']}")
                page_features = payload.get("features") if isinstance(payload, dict) else None
                feature_list = page_features if isinstance(page_features, list) else []
                if not feature_list:
                    break
                features.extend(self._feature_record(f, config) for f in feature_list)
                exceeded = (
                    payload.get("exceededTransferLimit") if isinstance(payload, dict) else None
                )
                if not exceeded and len(feature_list) < config.page_size:
                    break  # no more pages
        finally:
            fetcher.close()

        return self._features_to_pointers(config, features)

    def _feature_record(self, feature: object, config: ArcgisRestConfig) -> dict[str, Any]:
        """Flatten one ArcGIS feature to a record (attributes, + geometry opt-in)."""
        if not isinstance(feature, dict):
            return {}
        attrs = feature.get("attributes")
        record: dict[str, Any] = dict(attrs) if isinstance(attrs, dict) else {}
        if config.include_geometry and isinstance(feature.get("geometry"), dict):
            record["geometry"] = feature["geometry"]
        return record

    def _features_to_pointers(
        self, config: ArcgisRestConfig, features: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        query_url = _query_url(config)

        def url_for(index: int, record: dict[str, Any]) -> str:
            # ArcGIS rows carry a stable OBJECTID; use it when present.
            object_id = record.get("OBJECTID") or record.get("objectid")
            row_ref = str(object_id) if isinstance(object_id, str | int) else str(index)
            return with_query(query_url, {"objectIds": row_ref, "f": "json"})

        urls = records_to_bodies(features, url_for=url_for, bodies=self._bodies)
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
                hint_metadata={"layer_url": config.layer_url},
            )
            for url in urls
        ]


__all__ = [
    "ArcgisRestConfig",
    "ArcgisRestConnector",
]
