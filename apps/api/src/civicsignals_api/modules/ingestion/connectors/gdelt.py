"""``gdelt`` — GDELT 2.0 DOC API for news (doc 18 §1 cat. A, §5 wave 3 #15; D8).

GDELT (the Global Database of Events, Language, and Tone) exposes a free, keyless
news-article search API — the **DOC 2.0 API** (doc 16 §16:
``https://api.gdeltproject.org/api/v2/doc/doc``). It is a good OSS fit because the
data is open with no licensing complications (doc 16 §16 Tier 2: ``news_mention``,
``personnel_change``). Public/open data, no auth — in MVP scope (unlike the
deferred ``samgov`` / ``grantsgov`` federal scraping).

GDELT specifics this connector handles (vs. the generic ``rest_api_pager``):

* **No pagination**: the DOC API returns a *single* time-windowed result set per
  query (the recipe scopes recency via ``timespan`` / ``startdatetime``), so
  ``discover`` issues exactly one request — ``maxrecords`` caps the page size.
* The ``ArtList`` response envelope ``{"articles": [...]}`` — articles live under
  ``articles``; an empty/garbage body yields zero pointers.
* The mandatory ``query`` + ``mode=ArtList`` + ``format=json`` params; a recipe's
  ``query`` is GDELT's boolean search expression.

``discover`` issues the single DOC query (reusing the wave-1
:class:`RestApiPagerFetcher` for rate-limit + retry/backoff), then emits **one
pointer per article**; each is projected to a stable HTML ``<dl>`` (doc 18 §3.4
extraction is selector-based) re-served from a :class:`StaticBodyFetcher`, so the
runner extracts a recipe's fields with ``dd[data-key='<field>']`` (``title``,
``url``, ``domain``, ``seendate``, …) without a second network hit. The article
*body* itself is reached by a separate ``rss``/``http_static`` recipe if needed —
this connector surfaces the GDELT-extracted metadata as the signal.
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


class GdeltConfig(BaseModel):
    """Per-recipe ``connector_config.gdelt`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    # GDELT boolean search expression (e.g. ``"school district" sourcecountry:US``).
    query: str
    base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    # Recency window — GDELT ``timespan`` (e.g. ``1d``, ``3d``) scopes the search.
    timespan: str | None = "1d"
    # Cap on articles returned by the single query (GDELT ``maxrecords``, max 250).
    max_records: int = 75
    rate_limit_per_minute: int | None = None


@register
class GdeltConnector(Connector):
    """GDELT 2.0 DOC news-search connector (doc 18 §1 cat. A, §5 wave 3 #15)."""

    name: ClassVar[str] = "gdelt"
    config_model: ClassVar[type[BaseModel] | None] = GdeltConfig

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        super().__init__(recipe, clock=clock)
        self._bodies: dict[str, str] = {}

    def _pager_config(self, config: GdeltConfig) -> RestApiPagerConfig:
        """Adapt GDELT onto the generic pager (rate-limit + retry only).

        GDELT is keyless and unpaginated; ``discover`` issues a single request.
        ``items_path`` points at the ``articles`` array for completeness, but the
        connector reads it explicitly.
        """
        return RestApiPagerConfig(
            base_url=trim_base(config.base_url),
            pagination=PaginationConfig(mode="page", items_path="articles", max_pages=1),
            auth=AuthConfig(mode="none"),
            rate_limit_per_minute=config.rate_limit_per_minute,
        )

    def build_fetcher(self) -> Fetcher:
        return StaticBodyFetcher(self._bodies)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Issue the single DOC query and emit one pointer per article (doc 18 §2.1)."""
        config = self.config
        assert isinstance(config, GdeltConfig)
        params: dict[str, str] = {
            "query": config.query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(config.max_records),
        }
        if config.timespan:
            params["timespan"] = config.timespan
        url = with_query(trim_base(config.base_url), params)

        fetcher = RestApiPagerFetcher(self._pager_config(config), clock=self.clock)
        try:
            status, body, _headers = fetcher.fetch(
                url,
                user_agent=self.recipe.fetch.user_agent,
                max_redirects=self.recipe.fetch.max_redirects,
            )
            if status in (401, 403):
                raise ConnectorError(
                    f"gdelt request rejected (HTTP {status}); the DOC API is keyless — "
                    "check the query (doc 18 §4 cat. A)"
                )
            payload = parse_json_body(body, connector="gdelt")
        finally:
            fetcher.close()

        articles = dotted_get(payload, "articles")
        records = [a for a in articles if isinstance(a, dict)] if isinstance(articles, list) else []
        return self._records_to_pointers(config, records)

    def _records_to_pointers(
        self, config: GdeltConfig, records: Sequence[dict[str, Any]]
    ) -> list[SourcePointer]:
        def url_for(index: int, record: dict[str, Any]) -> str:
            # The article's own URL is its stable identifier; fall back to the index.
            article_url = record.get("url")
            return (
                article_url if isinstance(article_url, str) and article_url else f"#article-{index}"
            )

        urls = records_to_bodies(records, url_for=url_for, bodies=self._bodies)
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
                hint_metadata={"query": config.query},
            )
            for url in urls
        ]


__all__ = [
    "GdeltConfig",
    "GdeltConnector",
]
