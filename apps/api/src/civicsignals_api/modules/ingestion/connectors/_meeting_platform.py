"""Shared base for the wave-2 meeting/agenda platforms (doc 18 §1 cat. E, §5
wave 2 #6-#8; TODO D7).

``boarddocs``, ``granicus_peak``, and ``civicplus`` are all the same mechanical
shape (doc 16 §16: per-tenant subdomain, static HTML, public meeting materials):

1. ``discover`` fetches the tenant's **meeting/agenda index** (a listing page) and
   parses out the per-meeting **detail-page links**, emitting one
   :class:`SourcePointer` per meeting.
2. The recipe runner then fetches each detail page over plain HTTP and extracts
   the recipe's fields (title, date, agenda items, …) with CSS/XPath selectors —
   exactly the wave-1 ``http_static`` extraction path (doc 18 §3.4). The runner
   owns robots.txt + politeness for *every* fetch, so a per-tenant crawl can never
   bypass the legal posture (doc 18 §2.2).

The only per-platform variation is *how the listing links are found* and *how a
relative link resolves to an absolute detail URL*. This base captures the
discover/fetch wiring; each platform subclass supplies:

* ``_listing_urls(config)`` — the index URL(s) to crawl for a tenant, and
* ``_link_selectors(config)`` — the ordered CSS selectors that pick out the
  per-meeting ``<a href>`` links on a listing page (ordered like the recipe field
  selectors: primary → fallbacks, doc 18 §3.4), plus an optional href predicate.

No JS rendering: all three serve a static fallback for their public meeting lists
(doc 18 §5 prefers static; the headless browser is wave 4 / D9). A specific tenant
that *genuinely* requires JS (e.g. a CivicPlus site fronted entirely by a SPA with
no static list) is a per-recipe decision — set ``connector: http_browser`` once
D9 lands and note it in the recipe's ``legal_notes``; we do not implement
JS-rendering inside these connectors.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from civicsignals_api.modules.recipes.services import Fetcher, SourcePointer

from .base import Connector
from .http_static import HttpStaticConfig, HttpxFetcher


class MeetingPlatformConnector(Connector):
    """Static-HTML meeting/agenda platform base (doc 18 §1 cat. E, §5 wave 2)."""

    def build_fetcher(self) -> Fetcher:
        # Detail pages are static HTML; reuse the wave-1 fetcher (retries +
        # conditional GET). The runner owns robots.txt + politeness uniformly.
        return HttpxFetcher(HttpStaticConfig())

    @abstractmethod
    def _listing_urls(self, seed_urls: Sequence[str]) -> list[str]:
        """The tenant's meeting-index URL(s) to crawl for detail links."""

    @abstractmethod
    def _link_selectors(self) -> list[str]:
        """Ordered CSS selectors picking the per-meeting ``<a href>`` links."""

    def _accept_href(self, href: str) -> bool:
        """Predicate to keep/drop a discovered href (default: keep all)."""
        return True

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Crawl the listing page(s) and emit one pointer per meeting detail page.

        Fetches each listing through the shared :class:`HttpxFetcher` (so the
        listing request is also retried), parses the per-meeting links with the
        platform's ordered selectors, resolves them to absolute URLs, de-dupes, and
        returns one pointer per detail page. The runner fetches + extracts each.
        """
        listing_urls = self._listing_urls(seed_urls)
        fetcher = HttpxFetcher(HttpStaticConfig())
        pointers: list[SourcePointer] = []
        seen: set[str] = set()
        try:
            for listing_url in listing_urls:
                status, body, _headers = fetcher.fetch(
                    listing_url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status != 200 or not body:
                    # A 404/empty listing is surfaced as zero pointers for that
                    # index (the next index, if any, still runs); a platform-wide
                    # break shows up as drift across many recipes (doc 18 §4 cat. E).
                    continue
                for detail_url in self._extract_links(body, listing_url):
                    if detail_url in seen:
                        continue
                    seen.add(detail_url)
                    pointers.append(
                        SourcePointer(
                            recipe_id=self.recipe.recipe_id,
                            connector=self.recipe.connector,
                            url=detail_url,
                            hint_metadata={"listing_url": listing_url},
                        )
                    )
        finally:
            fetcher.close()
        return pointers

    def _extract_links(self, listing_html: str, listing_url: str) -> list[str]:
        """Parse per-meeting detail links from a listing page (ordered selectors)."""
        soup = BeautifulSoup(listing_html, "html.parser")
        links: list[str] = []
        for selector in self._link_selectors():
            for anchor in soup.select(selector):
                if not isinstance(anchor, Tag):
                    continue
                href = anchor.get("href")
                if not isinstance(href, str) or not href.strip():
                    continue
                absolute = urljoin(listing_url, href.strip())
                if not self._accept_href(absolute):
                    continue
                if urlparse(absolute).scheme not in ("http", "https"):
                    continue
                links.append(absolute)
            if links:
                # Primary selector matched — stop (later selectors are fallbacks).
                break
        # Preserve order, drop dupes within this listing.
        out: list[str] = []
        local_seen: set[str] = set()
        for link in links:
            if link not in local_seen:
                local_seen.add(link)
                out.append(link)
        return out


__all__ = ["MeetingPlatformConnector"]
