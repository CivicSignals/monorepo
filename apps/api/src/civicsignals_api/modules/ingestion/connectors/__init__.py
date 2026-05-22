"""Source-type connectors (doc 18 §1, §5; TODO D6).

A **connector is code for a source type** (doc 16 §17); a **recipe** instantiates
one connector for a single tenant/source. This package holds the connector
abstraction + registry (:mod:`.base`) and **wave 1 — the generic primitives**
(doc 18 §5):

* :mod:`.http_static` — generic HTTP fetcher (robots/politeness via the runner;
  retries/backoff + conditional GET here).
* :mod:`.rss` — feedparser-based feed ingestion.
* :mod:`.pdf_extractor` — pdfplumber → OCR (E9) → LLM-table fallback chain;
  consumed by other connectors, not scheduled.
* :mod:`.rest_api_pager` — REST/JSON client with pagination, auth, rate limiting.
* :mod:`.bulk_download` — periodic file fetch with checkpointing.

Importing this package registers every connector (its module's ``@register``
runs), so :func:`~.base.get_connector` / :func:`~.base.connector_for` resolve a
recipe's ``connector`` field to its class. The recipe runner dispatches through
this seam (``recipes.runner.run_with_connector``).
"""

from __future__ import annotations

from .base import (
    Connector,
    ConnectorError,
    UnknownConnectorError,
    connector_for,
    get_connector,
    register,
    registered_names,
)

# Importing the connector modules runs their ``@register`` decorators, populating
# the registry. Listed explicitly (not glob-imported) so the set of wave-1
# connectors is auditable and import order is deterministic.
from .bulk_download import (
    BulkDownloadConfig,
    BulkDownloadConnector,
    Checkpoint,
    CheckpointStore,
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

__all__ = [
    "AuthConfig",
    "BulkDownloadConfig",
    "BulkDownloadConnector",
    "Checkpoint",
    "CheckpointStore",
    "Connector",
    "ConnectorError",
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
    "UnknownConnectorError",
    "connector_for",
    "get_connector",
    "register",
    "registered_names",
]
