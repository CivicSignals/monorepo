"""``rss`` — RSS/Atom feed connector (doc 18 §1 cat. B, §5 wave 1 #2; D6).

The source publishes an RSS/Atom feed (GovTech, StateScoop, Route Fifty, many
``.gov`` press pages). ``discover()`` fetches + parses the feed with **feedparser**
and emits one :class:`SourcePointer` per item, carrying the entry's feed-level
fields (title, link, published, summary) as ``hint_metadata`` so they survive even
if the linked article later 404s (doc 18 §4 "Article URLs no longer resolve" →
cache the feed snippet). The runner then fetches each item's linked page via the
shared static :class:`HttpxFetcher` and extracts through the normal chain.

feedparser ships in the **ingestion optional extra** (pyproject), so it is
imported lazily — the lean ``api`` image (and anything that merely imports this
module) never needs it. Exercising the RSS path without the extra raises a clear
:class:`RssUnavailableError`. feedparser is famously lenient, which is the point:
malformed XML is handled rather than crashing (doc 18 §4 "Malformed XML").
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Fetcher, SourcePointer

from .base import Connector, ConnectorError, register
from .http_static import HttpStaticConfig, HttpxFetcher


class RssUnavailableError(ConnectorError):
    """feedparser (the ``ingestion`` extra) is not installed."""

    def __init__(self, cause: ImportError | None = None) -> None:
        super().__init__(
            "the rss connector requires feedparser, which ships in the 'ingestion' "
            "optional extra. Install it: `uv sync --extra ingestion`. Only "
            "worker_ingest carries this extra; the lean api image does not."
        )
        self.__cause__ = cause


class RssConfig(BaseModel):
    """Per-recipe ``connector_config.rss`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    feed_url: str | None = None
    max_items: int | None = None
    fetch_item_body: bool = True


def _parse_feed(feed_body: str) -> Any:
    """Lazily import feedparser and parse a feed body, or raise a clear guard."""
    try:
        import feedparser
    except ImportError as exc:  # pragma: no cover - exercised via the guard test
        raise RssUnavailableError(exc) from exc
    return feedparser.parse(feed_body)


def _entry_metadata(entry: Any) -> dict[str, object]:
    """Project the feed-level fields we keep as a pointer's ``hint_metadata``.

    These are the fallback content cached from the feed itself, so an item whose
    article URL later stops resolving still carries a usable title/summary
    (doc 18 §4 cat. B).
    """
    meta: dict[str, object] = {}
    for key in ("title", "summary", "published", "updated", "id"):
        value = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if value:
            meta[key] = value
    return meta


@register
class RssConnector(Connector):
    """RSS/Atom feed connector (doc 18 §1 cat. B, §5 wave 1 #2)."""

    name: ClassVar[str] = "rss"
    config_model: ClassVar[type[BaseModel] | None] = RssConfig

    def build_fetcher(self) -> Fetcher:
        # RSS fetches the feed (in discover) and each item's article (in fetch)
        # over plain HTTP, so it reuses the static fetcher. The runner still owns
        # robots.txt + politeness for every fetch.
        return HttpxFetcher(HttpStaticConfig())

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Fetch the feed, parse it, and emit one pointer per item (doc 18 §2.1).

        The feed URL is ``connector_config.rss.feed_url`` when set, else the first
        seed URL. We fetch the feed through the same :class:`HttpxFetcher` so the
        feed request is also subject to retries (the runner applies robots/politeness
        when it later fetches each item).
        """
        config = self.config
        assert isinstance(config, RssConfig)
        feed_url = config.feed_url or (seed_urls[0] if seed_urls else None)
        if feed_url is None:
            raise ConnectorError(
                f"rss recipe {self.recipe.recipe_id!r} has no feed_url and no seed URL"
            )

        fetcher = HttpxFetcher(HttpStaticConfig())
        try:
            status, body, _headers = fetcher.fetch(
                feed_url,
                user_agent=self.recipe.fetch.user_agent,
                max_redirects=self.recipe.fetch.max_redirects,
            )
        finally:
            fetcher.close()
        if status == 404:
            # Feed moved/gone — surface so the recipe is paused, not silently empty
            # (doc 18 §4 "Feed URL 404").
            raise ConnectorError(f"rss feed {feed_url!r} returned 404 (feed probably moved)")

        parsed = _parse_feed(body)
        entries = list(getattr(parsed, "entries", []) or [])
        if config.max_items is not None:
            entries = entries[: config.max_items]

        pointers: list[SourcePointer] = []
        for entry in entries:
            link = entry.get("link") if hasattr(entry, "get") else getattr(entry, "link", None)
            if not link:
                continue
            pointers.append(
                SourcePointer(
                    recipe_id=self.recipe.recipe_id,
                    connector=self.recipe.connector,
                    url=str(link),
                    hint_metadata=_entry_metadata(entry),
                )
            )
        return pointers


__all__ = [
    "RssConfig",
    "RssConnector",
    "RssUnavailableError",
]
