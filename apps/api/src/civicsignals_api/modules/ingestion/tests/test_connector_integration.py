"""Layer A — per-source connector *integration* tests (e2e suite).

Where the per-connector unit tests (``test_connector_<name>.py``) each pick apart
one connector's internals, this file drives **every** D6 generic + D7 platform
connector through the *same* production seam — :func:`crawl_recipe_with_connector`
(doc 18 §2; the D6 dispatch the scheduler/ingest worker uses) — and asserts on the
two ends of the lifecycle that prove correctness:

* **discover**: the exact :class:`SourcePointer` set a connector computes from a
  minimal in-test recipe + a :class:`StaticFetcher` serving mocked source bytes
  (RSS expands a feed to per-item pointers; ``rest_api_pager`` follows pagination;
  ``bulk_download`` detects the manifest entry; the meeting platforms parse the
  listing into detail links; the open-data APIs emit one pointer per row/feature).
* **extract + normalize**: the :class:`CanonicalRecord`s the runner produces, with
  exact field values + ``signal_types`` (selector-based extraction over the served
  body), so the test proves the *whole* ``discover -> fetch -> extract -> normalize``
  path, not merely "no exception".

Network is fully mocked (``httpx.MockTransport`` patched onto each fetcher's
``_get_client``, or a static-body fetcher), a :class:`FakeClock` keeps the runner's
politeness window from sleeping, and recipes set ``respect_robots_txt: False`` so a
robots.txt round-trip isn't needed. Everything is DB-free.

The connectors covered (mirrors doc 18 §5 wave 1 generics + wave 2 platforms):

* **D6 generic** — ``http_static``, ``rss``, ``pdf_extractor``, ``rest_api_pager``,
  ``bulk_download``.
* **D7 platform** — ``boarddocs``, ``granicus_peak``, ``civicplus`` (meeting/agenda,
  static HTML) and ``socrata``, ``ckan``, ``arcgis_rest`` (open-data/GIS JSON APIs).

``feedparser`` / ``pdfplumber`` ship in optional extras not installed in CI's
``--frozen`` job: the RSS lifecycle test ``importorskip``s feedparser, and the PDF
path monkeypatches ``_pdfplumber_text`` (the established offline pattern) plus a
real-library smoke test guarded by ``importorskip``.
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import connectors
from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors import pdf_extractor as pdf_module
from civicsignals_api.modules.ingestion.connectors.http_static import HttpxFetcher
from civicsignals_api.modules.ingestion.connectors.pdf_extractor import (
    PdfConfig,
    PdfExtractionMethod,
    PdfExtractor,
    PdfExtractorConnector,
)
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import CanonicalRecord, Recipe

# Connectors this Layer-A suite must cover end to end (doc 18 §5 wave 1 + wave 2).
D6_GENERIC = ("http_static", "rss", "pdf_extractor", "rest_api_pager", "bulk_download")
D7_PLATFORM = ("boarddocs", "granicus_peak", "civicplus", "socrata", "ckan", "arcgis_rest")

Handler = Callable[[httpx.Request], httpx.Response]


class FakeClock:
    """Deterministic clock so the runner's politeness window never really sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _mock_static_network(handler: Handler) -> mock._patch[Callable[[object], httpx.Client]]:
    """Patch every static :class:`HttpxFetcher`'s client onto a mocked transport."""
    return mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler))


def _mock_api_network(handler: Handler) -> mock._patch[Callable[[object], httpx.Client]]:
    """Patch the REST-pager fetcher's client (socrata/ckan/arcgis reuse it)."""
    return mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler))


def _crawl(recipe: Recipe, handler: Handler, *, api: bool = False) -> list[CanonicalRecord]:
    """Run the full D6 dispatch for ``recipe`` with the network mocked.

    Patches :func:`load_recipe` to return our in-test recipe and the relevant
    fetcher's client onto ``handler``, then calls the same entry point the
    ingest worker uses. A :class:`FakeClock` keeps politeness from sleeping.
    """
    network = _mock_api_network(handler) if api else _mock_static_network(handler)
    with (
        network,
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        return ingestion_services.crawl_recipe_with_connector(
            recipe.recipe_id, [], clock=FakeClock()
        )


# A robots.txt 404 (permissive) for static crawlers when a recipe leaves robots on.
def _with_robots_404(handler: Handler) -> Handler:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return handler(request)

    return wrapped


# ===========================================================================
# Coverage guard: every D6/D7 connector this suite targets is registered.
# ===========================================================================


def test_all_target_connectors_registered() -> None:
    registered = set(connectors.registered_names())
    missing = sorted(set(D6_GENERIC + D7_PLATFORM) - registered)
    assert not missing, f"connectors missing from the registry: {missing}"


# ===========================================================================
# D6 #1 — http_static: trivial pass-through discover, static HTML extraction.
# ===========================================================================

_HTTP_STATIC_RECIPE: dict[str, object] = {
    "recipe_id": "http-static-it",
    "connector": "http_static",
    "version": 4,
    "entity": {"name": "WA DES", "state": "WA", "kind": "state_agency"},
    "fetch": {"respect_robots_txt": True, "politeness_seconds": 10, "jitter_seconds": 0},
    "fields": {
        "title": {"selectors": ["h1.solicitation-title", "h1"], "required": True},
        "due_date": {"selectors": [".due time"], "attr": "datetime"},
    },
    "signal_types": ["rfp_posted"],
}

_HTTP_STATIC_HTML = (
    "<html><body><main>"
    '<h1 class="solicitation-title">RFP 7 — Network Upgrade</h1>'
    '<p class="due"><time datetime="2026-03-15">Mar 15, 2026</time></p>'
    "</main></body></html>"
)


def test_http_static_discover_is_pass_through() -> None:
    recipe = recipes_services.parse_recipe(_HTTP_STATIC_RECIPE)
    connector = connectors.connector_for(recipe)
    pointers = connector.discover(["https://webs.des.wa.gov/a", "https://webs.des.wa.gov/b"])
    assert [p.url for p in pointers] == [
        "https://webs.des.wa.gov/a",
        "https://webs.des.wa.gov/b",
    ]
    assert all(p.connector == "http_static" for p in pointers)


def test_http_static_full_lifecycle() -> None:
    recipe = recipes_services.parse_recipe(_HTTP_STATIC_RECIPE)
    seed = "https://webs.des.wa.gov/rfp-7"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTTP_STATIC_HTML)

    # http_static seeds drive discovery, so run through run_recipe via the connector
    # with explicit seed URLs (crawl_recipe_with_connector passes []).
    with (
        _mock_static_network(_with_robots_404(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector(
            "http-static-it", [seed], clock=FakeClock()
        )

    assert len(records) == 1
    record = records[0]
    assert record.recipe_id == "http-static-it"
    assert record.recipe_version == 4
    assert record.source_url == seed
    assert record.fields["title"] == "RFP 7 — Network Upgrade"
    assert record.fields["due_date"] == "2026-03-15"  # read via attr=datetime
    assert record.signal_types == ["rfp_posted"]
    assert record.entity.state == "WA"


# ===========================================================================
# D6 #2 — rss: discover expands the feed into one pointer per item; the runner
# then fetches + extracts each linked article.
# ===========================================================================

_RSS_RECIPE: dict[str, object] = {
    "recipe_id": "govtech-feed-it",
    "connector": "rss",
    "version": 1,
    "entity": {"name": "GovTech", "kind": "publisher"},
    "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
    "connector_config": {"rss": {"feed_url": "https://govtech.example/rss"}},
    "fields": {"title": {"selectors": ["h1.headline", "h1"], "required": True}},
    "signal_types": ["news"],
}

_RSS_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>GovTech Feed</title>
  <item>
    <title>City adopts new procurement portal</title>
    <link>https://govtech.example/articles/portal</link>
    <pubDate>Tue, 20 May 2025 09:00:00 GMT</pubDate>
    <description>A summary of the portal article.</description>
  </item>
  <item>
    <title>State releases RFP for fiber</title>
    <link>https://govtech.example/articles/fiber</link>
    <pubDate>Mon, 19 May 2025 09:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

_RSS_ARTICLES = {
    "https://govtech.example/articles/portal": (
        '<html><body><h1 class="headline">Portal goes live citywide</h1></body></html>'
    ),
    "https://govtech.example/articles/fiber": (
        '<html><body><h1 class="headline">Fiber RFP open through June</h1></body></html>'
    ),
}


def _rss_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://govtech.example/rss":
        return httpx.Response(200, text=_RSS_FEED_XML, headers={"content-type": "application/xml"})
    body = _RSS_ARTICLES.get(url)
    if body is not None:
        return httpx.Response(200, text=body)
    return httpx.Response(404)


def test_rss_discover_expands_feed_into_item_pointers() -> None:
    pytest.importorskip("feedparser")
    recipe = recipes_services.parse_recipe(_RSS_RECIPE)
    connector = connectors.connector_for(recipe)
    with _mock_static_network(_rss_handler):
        pointers = connector.discover([])
    assert [p.url for p in pointers] == [
        "https://govtech.example/articles/portal",
        "https://govtech.example/articles/fiber",
    ]
    # Feed-level fields ride along as hint_metadata (fallback if the article 404s).
    assert pointers[0].hint_metadata["title"] == "City adopts new procurement portal"
    assert "summary" in pointers[0].hint_metadata
    assert all(p.connector == "rss" for p in pointers)


def test_rss_full_lifecycle() -> None:
    pytest.importorskip("feedparser")
    recipe = recipes_services.parse_recipe(_RSS_RECIPE)
    records = _crawl(recipe, _rss_handler)
    assert [r.source_url for r in records] == [
        "https://govtech.example/articles/portal",
        "https://govtech.example/articles/fiber",
    ]
    assert [r.fields["title"] for r in records] == [
        "Portal goes live citywide",
        "Fiber RFP open through June",
    ]
    assert all(r.signal_types == ["news"] for r in records)


# ===========================================================================
# D6 #3 — pdf_extractor: consumed by other connectors, not scheduled directly.
# Cover the extraction chain (pdfplumber text layer mocked) + a real-library
# smoke test. discover() is the trivial pass-through (one pointer per seed PDF).
# ===========================================================================

_PDF_RECIPE: dict[str, object] = {
    "recipe_id": "minutes-pdf-it",
    "connector": "pdf_extractor",
    "version": 1,
    "entity": {"name": "Example District", "state": "WA"},
    "connector_config": {"pdf_extractor": {"ocr_fallback": True}},
    "fields": {"title": {"selectors": ["h1"]}},
    "signal_types": ["board_decision"],
}

_PDF_BYTES = b"%PDF-1.7 fake bytes for the integration test"


def test_pdf_discover_is_pass_through_to_the_pdf_url() -> None:
    recipe = recipes_services.parse_recipe(_PDF_RECIPE)
    connector = connectors.connector_for(recipe)
    assert isinstance(connector, PdfExtractorConnector)
    pointers = connector.discover(["https://district.example/minutes/2026-05.pdf"])
    assert [p.url for p in pointers] == ["https://district.example/minutes/2026-05.pdf"]
    assert pointers[0].connector == "pdf_extractor"


def test_pdf_extraction_chain_text_layer_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # pdfplumber ships in the extraction extra (absent under --frozen): mock the
    # text-layer extractor so the chain runs offline, mirroring the unit tests.
    monkeypatch.setattr(
        pdf_module,
        "_pdfplumber_text",
        lambda b, *, max_pages: ("Board minutes: budget approved 5-0", 4),
    )
    recipe = recipes_services.parse_recipe(_PDF_RECIPE)
    connector = connectors.connector_for(recipe)
    assert isinstance(connector, PdfExtractorConnector)
    extractor = connector.extractor()
    assert isinstance(extractor, PdfExtractor)
    result = extractor.extract(_PDF_BYTES)
    assert result.text == "Board minutes: budget approved 5-0"
    assert result.method is PdfExtractionMethod.TEXT
    assert result.page_count == 4
    assert result.degraded is False


def test_pdf_ocr_fallback_when_no_text_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    # No text layer -> the OCR rung (injected fake until E9) recovers text and the
    # result is flagged degraded (doc 18 §3.4 / §4 cat. F).
    monkeypatch.setattr(pdf_module, "_pdfplumber_text", lambda b, *, max_pages: ("", 2))

    class _FakeOcr:
        def image_pdf_to_text(self, pdf_bytes: bytes, *, max_pages: int | None) -> str:
            return "text recovered via OCR"

    extractor = PdfExtractor(PdfConfig(ocr_fallback=True), ocr_engine=_FakeOcr())
    result = extractor.extract(_PDF_BYTES)
    assert result.text == "text recovered via OCR"
    assert result.method is PdfExtractionMethod.OCR
    assert result.degraded is True


def test_pdf_real_pdfplumber_smoke() -> None:
    # Smoke against the real library when the extraction extra is installed: a
    # trivial text-layer-free PDF extracts empty (-> OCR territory).
    pytest.importorskip("pdfplumber")
    minimal_pdf = (
        b"%PDF-1.1\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    text, pages = pdf_module._pdfplumber_text(minimal_pdf, max_pages=None)
    assert text == ""
    assert pages == 1


# ===========================================================================
# D6 #4 — rest_api_pager: discover walks pages forward (one pointer per page);
# the runner re-fetches each page's JSON body and extracts over it.
# ===========================================================================

_REST_RECIPE: dict[str, object] = {
    "recipe_id": "grants-api-it",
    "connector": "rest_api_pager",
    "version": 2,
    "entity": {"name": "Grants.gov"},
    "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
    "connector_config": {
        "rest_api_pager": {
            "base_url": "https://api.grants.gov/v1/opportunities",
            "pagination": {
                "mode": "page",
                "items_path": "opportunities",
                "page_size": 2,
                "start_page": 1,
                "max_pages": 10,
            },
        }
    },
    # The JSON page body is re-served verbatim as the fetched body. A REST page is
    # an array of records, not an HTML document, so the runner's selector chain
    # finds nothing — the field is optional and dead-letters gracefully. This
    # connector's value is *pagination* (one record per page); per-record field
    # extraction is what the open-data connectors below add via the <dl> projection.
    "fields": {"page_kind": {"selectors": ["article.opportunity"]}},
    "signal_types": ["grant_opportunity"],
}

_REST_PAGES: dict[int, dict[str, object]] = {
    1: {"opportunities": [{"id": 1}, {"id": 2}]},
    2: {"opportunities": [{"id": 3}]},
    3: {"opportunities": []},
}


def test_rest_api_pager_discover_follows_pagination() -> None:
    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page") or "1"
        seen_pages.append(page)
        return httpx.Response(200, json=_REST_PAGES[int(page)])

    recipe = recipes_services.parse_recipe(_REST_RECIPE)
    connector = connectors.connector_for(recipe)
    with _mock_api_network(handler):
        pointers = list(connector.discover([]))

    # Pages 1 and 2 had items; page 3 was empty -> stop. One pointer per non-empty page.
    assert len(pointers) == 2
    assert seen_pages == ["1", "2", "3"]
    assert pointers[0].hint_metadata["item_count"] == 2
    assert pointers[1].hint_metadata["item_count"] == 1
    assert pointers[0].url.startswith("https://api.grants.gov/v1/opportunities")
    assert "page=1" in pointers[0].url
    assert "page=2" in pointers[1].url


def test_rest_api_pager_full_lifecycle() -> None:
    # Pagination follows JSON pages; each non-empty page becomes one
    # CanonicalRecord. The selector misses on the JSON-as-text body (optional
    # field -> graceful dead-letter), so we assert the page-to-record mapping +
    # provenance + that the source_urls are the per-page request URLs.
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page") or "1")
        return httpx.Response(200, json=_REST_PAGES[page])

    recipe = recipes_services.parse_recipe(_REST_RECIPE)
    records = _crawl(recipe, handler, api=True)
    assert len(records) == 2  # two non-empty pages -> two records
    assert all(r.recipe_id == "grants-api-it" for r in records)
    assert all(r.recipe_version == 2 for r in records)
    assert all(r.signal_types == ["grant_opportunity"] for r in records)
    # source_url is the concrete per-page request URL discover walked.
    assert "page=1" in records[0].source_url
    assert "page=2" in records[1].source_url
    # The optional selector found nothing in a JSON page -> dead-lettered, no crash.
    assert records[0].fields["page_kind"] is None


# ===========================================================================
# D6 #5 — bulk_download: discover detects whether the file changed and emits one
# pointer (the manifest entry) when so; the runner fetches + extracts it.
# ===========================================================================

_BULK_FILE_URL = "https://nces.ed.gov/ccd/data.html"

_BULK_RECIPE: dict[str, object] = {
    "recipe_id": "nces-ccd-it",
    "connector": "bulk_download",
    "version": 1,
    "entity": {"name": "NCES"},
    "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
    "connector_config": {
        "bulk_download": {
            "file_url": _BULK_FILE_URL,
            "format": "csv",
            "checkpoint_strategy": "etag",
        }
    },
    "fields": {"dataset": {"selectors": ["h1.dataset-title", "h1"], "required": True}},
    "signal_types": ["dataset_published"],
}

# The bulk file is served as HTML so the runner's selector extraction has something
# concrete to assert on (the connector is format-agnostic at the fetch layer).
_BULK_BODY = '<html><body><h1 class="dataset-title">CCD 2026 Directory</h1></body></html>'


def test_bulk_download_discover_detects_changed_file() -> None:
    # No prior checkpoint (default _NullCheckpointStore) -> the file is "new" ->
    # exactly one pointer carrying the manifest hint_metadata.
    recipe = recipes_services.parse_recipe(_BULK_RECIPE)
    connector = connectors.connector_for(recipe)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_BULK_BODY, headers={"ETag": '"v1"'})

    with _mock_static_network(handler):
        pointers = list(connector.discover([]))

    assert len(pointers) == 1
    assert pointers[0].url == _BULK_FILE_URL
    assert pointers[0].hint_metadata["checkpoint_strategy"] == "etag"
    assert pointers[0].hint_metadata["format"] == "csv"
    assert pointers[0].connector == "bulk_download"


def test_bulk_download_full_lifecycle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_BULK_BODY, headers={"ETag": '"v1"'})

    recipe = recipes_services.parse_recipe(_BULK_RECIPE)
    records = _crawl(recipe, handler)
    assert len(records) == 1
    assert records[0].source_url == _BULK_FILE_URL
    assert records[0].fields["dataset"] == "CCD 2026 Directory"
    assert records[0].signal_types == ["dataset_published"]


# ===========================================================================
# D7 #6-#8 — meeting/agenda platforms (boarddocs / granicus_peak / civicplus).
# Same mechanical shape: discover crawls the tenant listing into per-meeting
# detail pointers; the runner fetches + extracts each detail page (static HTML).
# ===========================================================================


def _meeting_recipe(connector: str, connector_config: dict[str, object]) -> dict[str, object]:
    return {
        "recipe_id": f"{connector}-it",
        "connector": connector,
        "version": 1,
        "entity": {"name": "Example Gov", "state": "WA"},
        "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
        "connector_config": {connector: connector_config},
        "fields": {"title": {"selectors": ["h1.meeting-title", "h1"], "required": True}},
        "signal_types": ["board_decision"],
    }


# (connector, config, expected listing URL, listing-link href, expected pointer URL)
_MEETING_CASES = [
    pytest.param(
        "boarddocs",
        {"state": "wa", "subdomain": "example-sd"},
        "https://go.boarddocs.com/wa/example-sd/Board.nsf/Public",
        "https://go.boarddocs.com/wa/example-sd/Agenda/200",
        "<a class='meeting-link' href='{href}'>May</a>",
        id="boarddocs",
    ),
    pytest.param(
        "granicus_peak",
        {"subdomain": "example-city", "view_id": 3},
        "https://example-city.granicus.com/ViewPublisher.php?view_id=3",
        "https://example-city.granicus.com/AgendaViewer.php?meeting=88",
        "<table class='listingTable'><a href='{href}'>Mtg</a></table>",
        id="granicus_peak",
    ),
    pytest.param(
        "civicplus",
        {"subdomain": "example-town", "platform": "civicweb"},
        "https://example-town.civicweb.net/Portal/MeetingTypeList.aspx",
        "https://example-town.civicweb.net/Portal/MeetingDetail.aspx?id=42",
        "<a class='meeting-detail-link' href='{href}'>Detail</a>",
        id="civicplus",
    ),
]

_MEETING_DETAIL_HTML = "<html><body><h1 class='meeting-title'>May Board Meeting</h1></body></html>"


@pytest.mark.parametrize(
    ("connector", "config", "listing_url", "detail_url", "link_tpl"), _MEETING_CASES
)
def test_meeting_platform_discover_parses_listing_links(
    connector: str,
    config: dict[str, object],
    listing_url: str,
    detail_url: str,
    link_tpl: str,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, text=f"<html><body>{link_tpl.format(href=detail_url)}</body></html>"
        )

    recipe = recipes_services.parse_recipe(_meeting_recipe(connector, config))
    conn = connectors.connector_for(recipe)
    with _mock_static_network(handler):
        pointers = list(conn.discover([]))

    # The tenant listing URL is built from the per-tenant config (doc 16 §16).
    assert seen[0] == listing_url
    assert [p.url for p in pointers] == [detail_url]
    assert pointers[0].hint_metadata["listing_url"] == listing_url
    assert pointers[0].connector == connector


@pytest.mark.parametrize(
    ("connector", "config", "listing_url", "detail_url", "link_tpl"), _MEETING_CASES
)
def test_meeting_platform_full_lifecycle(
    connector: str,
    config: dict[str, object],
    listing_url: str,
    detail_url: str,
    link_tpl: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == listing_url:
            return httpx.Response(
                200, text=f"<html><body>{link_tpl.format(href=detail_url)}</body></html>"
            )
        if url == detail_url:
            return httpx.Response(200, text=_MEETING_DETAIL_HTML)
        return httpx.Response(404)

    recipe = recipes_services.parse_recipe(_meeting_recipe(connector, config))
    records = _crawl(recipe, handler)
    assert len(records) == 1
    assert records[0].source_url == detail_url
    assert records[0].fields["title"] == "May Board Meeting"
    assert records[0].signal_types == ["board_decision"]


# ===========================================================================
# D7 #9-#11 — open-data / GIS APIs (socrata / ckan / arcgis_rest).
# discover pages the API and emits one pointer per row/feature; each row is
# projected to a stable <dl> HTML the runner extracts with dd[data-key='...'].
# ===========================================================================


def _opendata_recipe(connector: str, connector_config: dict[str, object]) -> dict[str, object]:
    return {
        "recipe_id": f"{connector}-it",
        "connector": connector,
        "version": 3,
        "entity": {"name": "Open Data", "state": "NY"},
        "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
        "connector_config": {connector: connector_config},
        "fields": {
            "title": {"selectors": ["dd[data-key='title']"], "required": True},
            "amount": {"selectors": ["dd[data-key='award_amount']"]},
        },
        "signal_types": ["contract_award"],
    }


def test_socrata_discover_emits_one_pointer_per_row() -> None:
    rows = {
        0: [
            {":id": "r1", "title": "Contract A", "award_amount": "5000"},
            {":id": "r2", "title": "Contract B", "award_amount": "9000"},
        ],
        2: [{":id": "r3", "title": "Contract C", "award_amount": "1500"}],  # short -> last
    }
    seen_offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("$offset") or "0"
        seen_offsets.append(offset)
        return httpx.Response(200, json=rows[int(offset)])

    recipe = recipes_services.parse_recipe(
        _opendata_recipe(
            "socrata",
            {"domain": "data.cityofnewyork.us", "dataset_id": "abcd-1234", "page_size": 2},
        )
    )
    connector = connectors.connector_for(recipe)
    with _mock_api_network(handler):
        pointers = list(connector.discover([]))

    assert len(pointers) == 3
    assert seen_offsets == ["0", "2"]
    assert all(p.hint_metadata["dataset_id"] == "abcd-1234" for p in pointers)
    assert all(p.connector == "socrata" for p in pointers)


def test_socrata_full_lifecycle() -> None:
    rows = [
        {":id": "r1", "title": "Contract A", "award_amount": "5000"},
        {":id": "r2", "title": "Contract B", "award_amount": "9000"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if int(request.url.params.get("$offset") or "0") == 0:
            return httpx.Response(200, json=rows)
        return httpx.Response(200, json=[])

    recipe = recipes_services.parse_recipe(
        _opendata_recipe(
            "socrata", {"domain": "data.x.gov", "dataset_id": "aaaa-bbbb", "page_size": 50}
        )
    )
    records = _crawl(recipe, handler, api=True)
    assert len(records) == 2
    assert [r.fields["title"] for r in records] == ["Contract A", "Contract B"]
    assert [r.fields["amount"] for r in records] == ["5000", "9000"]
    assert all(r.signal_types == ["contract_award"] for r in records)
    assert all(r.recipe_version == 3 for r in records)


def test_ckan_discover_emits_one_pointer_per_record() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset") or "0")
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "records": [
                            {"_id": 1, "title": "Grant A", "award_amount": "100"},
                            {"_id": 2, "title": "Grant B", "award_amount": "200"},
                        ],
                        "total": 2,
                    },
                },
            )
        return httpx.Response(200, json={"success": True, "result": {"records": []}})

    recipe = recipes_services.parse_recipe(
        _opendata_recipe(
            "ckan", {"domain": "catalog.data.gov", "resource_id": "res-9", "page_size": 50}
        )
    )
    connector = connectors.connector_for(recipe)
    with _mock_api_network(handler):
        pointers = list(connector.discover([]))
    assert len(pointers) == 2
    assert all(p.hint_metadata["resource_id"] == "res-9" for p in pointers)
    assert all(p.connector == "ckan" for p in pointers)


def test_ckan_full_lifecycle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset") or "0")
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "records": [
                            {"_id": 1, "title": "Grant A", "award_amount": "100"},
                            {"_id": 2, "title": "Grant B", "award_amount": "200"},
                        ]
                    },
                },
            )
        return httpx.Response(200, json={"success": True, "result": {"records": []}})

    recipe = recipes_services.parse_recipe(
        _opendata_recipe(
            "ckan", {"domain": "catalog.data.gov", "resource_id": "res-9", "page_size": 50}
        )
    )
    records = _crawl(recipe, handler, api=True)
    assert len(records) == 2
    assert [r.fields["title"] for r in records] == ["Grant A", "Grant B"]
    assert [r.fields["amount"] for r in records] == ["100", "200"]
    assert all(r.signal_types == ["contract_award"] for r in records)


def test_arcgis_rest_discover_emits_one_pointer_per_feature() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("resultOffset") or "0")
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"attributes": {"OBJECTID": 11, "title": "Project A", "award_amount": "7"}},
                        {"attributes": {"OBJECTID": 12, "title": "Project B", "award_amount": "8"}},
                    ],
                    "exceededTransferLimit": False,
                },
            )
        return httpx.Response(200, json={"features": []})

    recipe = recipes_services.parse_recipe(
        _opendata_recipe(
            "arcgis_rest",
            {"layer_url": "https://gis.example/FeatureServer/0", "page_size": 50},
        )
    )
    connector = connectors.connector_for(recipe)
    with _mock_api_network(handler):
        pointers = list(connector.discover([]))
    assert len(pointers) == 2
    assert all(
        p.hint_metadata["layer_url"] == "https://gis.example/FeatureServer/0" for p in pointers
    )
    # The pointer URL deep-links the feature by its stable OBJECTID.
    assert "objectIds=11" in pointers[0].url
    assert "objectIds=12" in pointers[1].url
    assert all(p.connector == "arcgis_rest" for p in pointers)


def test_arcgis_rest_full_lifecycle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("resultOffset") or "0")
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"attributes": {"OBJECTID": 11, "title": "Project A", "award_amount": "7"}},
                        {"attributes": {"OBJECTID": 12, "title": "Project B", "award_amount": "8"}},
                    ],
                    "exceededTransferLimit": False,
                },
            )
        return httpx.Response(200, json={"features": []})

    recipe = recipes_services.parse_recipe(
        _opendata_recipe(
            "arcgis_rest",
            {"layer_url": "https://gis.example/FeatureServer/0", "page_size": 50},
        )
    )
    records = _crawl(recipe, handler, api=True)
    assert len(records) == 2
    assert [r.fields["title"] for r in records] == ["Project A", "Project B"]
    assert [r.fields["amount"] for r in records] == ["7", "8"]
    assert all(r.signal_types == ["contract_award"] for r in records)
