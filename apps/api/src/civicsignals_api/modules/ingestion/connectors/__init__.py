"""Source-type connectors (doc 18 §1, §5; TODO D6, D7).

A **connector is code for a source type** (doc 16 §17); a **recipe** instantiates
one connector for a single tenant/source. This package holds the connector
abstraction + registry (:mod:`.base`) and the connectors built so far.

**Wave 1 — the generic primitives** (doc 18 §5):

* :mod:`.http_static` — generic HTTP fetcher (robots/politeness via the runner;
  retries/backoff + conditional GET here).
* :mod:`.rss` — feedparser-based feed ingestion.
* :mod:`.pdf_extractor` — pdfplumber → OCR (E9) → LLM-table fallback chain;
  consumed by other connectors, not scheduled.
* :mod:`.rest_api_pager` — REST/JSON client with pagination, auth, rate limiting.
* :mod:`.bulk_download` — periodic file fetch with checkpointing.

**Wave 2 — multi-tenant platforms** (doc 18 §5; one connector covers thousands of
entities via per-tenant recipes — TODO D7):

* :mod:`.socrata` / :mod:`.ckan` / :mod:`.arcgis_rest` — open-data / GIS REST
  APIs. They reuse :class:`.rest_api_pager.RestApiPagerFetcher` for auth +
  rate-limit + retry, page the tenant's API in ``discover``, and project each JSON
  record into a stable HTML ``<dl>`` (:mod:`._platform`) so the runner's selector
  extraction (doc 18 §3.4) applies uniformly.
* :mod:`.boarddocs` / :mod:`.granicus_peak` / :mod:`.civicplus` — government
  meeting/agenda platforms. They reuse :class:`.http_static.HttpxFetcher`; their
  shared base (:mod:`._meeting_platform`) crawls the tenant's meeting index and
  emits one pointer per detail page for the runner to fetch + extract.

Importing this package registers every connector (its module's ``@register``
runs), so :func:`~.base.get_connector` / :func:`~.base.connector_for` resolve a
recipe's ``connector`` field to its class. The recipe runner dispatches through
this seam (``recipes.runner.run_with_connector``).
"""

from __future__ import annotations

# Importing the connector modules runs their ``@register`` decorators, populating
# the registry. Listed explicitly (not glob-imported) so the set of registered
# connectors is auditable and import order is deterministic.
from .arcgis_rest import (
    ArcgisRestConfig,
    ArcgisRestConnector,
)
from .base import (
    Connector,
    ConnectorError,
    UnknownConnectorError,
    connector_for,
    get_connector,
    register,
    registered_names,
)
from .boarddocs import (
    BoarddocsConfig,
    BoarddocsConnector,
)
from .bulk_download import (
    BulkDownloadConfig,
    BulkDownloadConnector,
    Checkpoint,
    CheckpointStore,
)
from .civicplus import (
    CivicplusConfig,
    CivicplusConnector,
)
from .ckan import (
    CkanConfig,
    CkanConnector,
)
from .granicus_peak import (
    GranicusPeakConfig,
    GranicusPeakConnector,
)
from .http_static import (
    HttpStaticConfig,
    HttpStaticConnector,
    HttpxFetcher,
)
from .pdf_extractor import (
    PdfConfig,
    PdfExtractionError,
    PdfExtractionResult,
    PdfExtractor,
    PdfExtractorConnector,
)
from .rest_api_pager import (
    AuthConfig,
    PaginationConfig,
    RestApiPagerConfig,
    RestApiPagerConnector,
    RestApiPagerFetcher,
)
from .rss import (
    RssConfig,
    RssConnector,
)
from .socrata import (
    SocrataConfig,
    SocrataConnector,
)

__all__ = [
    "ArcgisRestConfig",
    "ArcgisRestConnector",
    "AuthConfig",
    "BoarddocsConfig",
    "BoarddocsConnector",
    "BulkDownloadConfig",
    "BulkDownloadConnector",
    "Checkpoint",
    "CheckpointStore",
    "CivicplusConfig",
    "CivicplusConnector",
    "CkanConfig",
    "CkanConnector",
    "Connector",
    "ConnectorError",
    "GranicusPeakConfig",
    "GranicusPeakConnector",
    "HttpStaticConfig",
    "HttpStaticConnector",
    "HttpxFetcher",
    "PaginationConfig",
    "PdfConfig",
    "PdfExtractionError",
    "PdfExtractionResult",
    "PdfExtractor",
    "PdfExtractorConnector",
    "RestApiPagerConfig",
    "RestApiPagerConnector",
    "RestApiPagerFetcher",
    "RssConfig",
    "RssConnector",
    "SocrataConfig",
    "SocrataConnector",
    "UnknownConnectorError",
    "connector_for",
    "get_connector",
    "register",
    "registered_names",
]
