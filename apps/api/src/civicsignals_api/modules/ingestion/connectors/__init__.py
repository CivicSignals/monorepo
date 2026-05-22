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

**Wave 3 — federal + niche** (doc 18 §5 wave 3; TODO D8):

* :mod:`.usaspending` / :mod:`.gdelt` — public, keyless federal/open-data JSON
  APIs (USAspending awards; GDELT 2.0 DOC news). They reuse
  :class:`.rest_api_pager.RestApiPagerFetcher` for rate-limit + retry and project
  each record to the stable HTML ``<dl>`` (:mod:`._platform`) the runner extracts.
* :mod:`.bonfire_euna` / :mod:`.ionwave` — multi-tenant e-procurement platforms,
  ingested via their public listing data APIs (same JSON→HTML projection path).
* :mod:`.nces_ccd` / :mod:`.ipeds` / :mod:`.census_gov` — federal **entity
  directory** bulk imports (K-12 / higher-ed / all-government). Thin subclasses of
  the bulk-download machinery (:mod:`._bulk_entity_directory`) with source-specific
  defaults; normalize() resolves rows by stable external id (NCES/IPEDS/FIPS).
* :mod:`.samgov` / :mod:`.grantsgov` — **deferred to v2** (federal coverage is out
  of MVP scope — TODO.md "Out of scope"). Registered but not wired for live
  fetching (:mod:`._deferred_federal`): ``discover`` emits no pointers and the
  fetcher refuses any fetch.

Importing this package registers every connector (its module's ``@register``
runs), so :func:`~.base.get_connector` / :func:`~.base.connector_for` resolve a
recipe's ``connector`` field to its class. The recipe runner dispatches through
this seam (``recipes.runner.run_with_connector``).
"""

from __future__ import annotations

# Importing the connector modules runs their ``@register`` decorators, populating
# the registry. Listed explicitly (not glob-imported) so the set of registered
# connectors is auditable and import order is deterministic.
from ._bulk_entity_directory import (
    BulkEntityDirectoryConfig,
    BulkEntityDirectoryConnector,
)
from ._deferred_federal import (
    DeferredConnectorError,
    DeferredFederalConfig,
    DeferredFederalConnector,
)
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
from .bonfire_euna import (
    BonfireEunaConfig,
    BonfireEunaConnector,
)
from .bulk_download import (
    BulkDownloadConfig,
    BulkDownloadConnector,
    Checkpoint,
    CheckpointStore,
)
from .census_gov import (
    CensusGovConfig,
    CensusGovConnector,
)
from .civicplus import (
    CivicplusConfig,
    CivicplusConnector,
)
from .ckan import (
    CkanConfig,
    CkanConnector,
)
from .gdelt import (
    GdeltConfig,
    GdeltConnector,
)
from .granicus_peak import (
    GranicusPeakConfig,
    GranicusPeakConnector,
)
from .grantsgov import (
    GrantsgovConfig,
    GrantsgovConnector,
)
from .http_static import (
    HttpStaticConfig,
    HttpStaticConnector,
    HttpxFetcher,
)
from .ionwave import (
    IonwaveConfig,
    IonwaveConnector,
)
from .ipeds import (
    IpedsConfig,
    IpedsConnector,
)
from .nces_ccd import (
    NcesCcdConfig,
    NcesCcdConnector,
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
from .samgov import (
    SamgovConfig,
    SamgovConnector,
)
from .socrata import (
    SocrataConfig,
    SocrataConnector,
)
from .usaspending import (
    UsaspendingConfig,
    UsaspendingConnector,
)

__all__ = [
    "ArcgisRestConfig",
    "ArcgisRestConnector",
    "AuthConfig",
    "BoarddocsConfig",
    "BoarddocsConnector",
    "BonfireEunaConfig",
    "BonfireEunaConnector",
    "BulkDownloadConfig",
    "BulkDownloadConnector",
    "BulkEntityDirectoryConfig",
    "BulkEntityDirectoryConnector",
    "CensusGovConfig",
    "CensusGovConnector",
    "Checkpoint",
    "CheckpointStore",
    "CivicplusConfig",
    "CivicplusConnector",
    "CkanConfig",
    "CkanConnector",
    "Connector",
    "ConnectorError",
    "DeferredConnectorError",
    "DeferredFederalConfig",
    "DeferredFederalConnector",
    "GdeltConfig",
    "GdeltConnector",
    "GranicusPeakConfig",
    "GranicusPeakConnector",
    "GrantsgovConfig",
    "GrantsgovConnector",
    "HttpStaticConfig",
    "HttpStaticConnector",
    "HttpxFetcher",
    "IonwaveConfig",
    "IonwaveConnector",
    "IpedsConfig",
    "IpedsConnector",
    "NcesCcdConfig",
    "NcesCcdConnector",
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
    "SamgovConfig",
    "SamgovConnector",
    "SocrataConfig",
    "SocrataConnector",
    "UnknownConnectorError",
    "UsaspendingConfig",
    "UsaspendingConnector",
    "connector_for",
    "get_connector",
    "register",
    "registered_names",
]
