"""``manual_upload`` — human-workflow connector (doc 18 §1 cat. H, §5 wave 5; D10).

Routes **uploaded FOIA responses** (PDF/attachments) and **customer CSV imports**
through the same extraction pipeline as scraped content (doc 18 §1 cat. H):

    uploaded bytes  →  store_raw_document (D3)  →  extraction pipeline (E1)

Unlike scraper connectors, this connector's input is **provided bytes / rows**
rather than a fetched URL. The lifecycle is therefore adapted:

* ``discover`` — no-op for manual uploads (the pointer is the already-stored raw
  document id, inserted by :func:`manual_upload_services.submit_raw_document`).
* ``fetch`` — no-op (bytes are already in S3 via D3; the pipeline reads them
  back via ``ingestion.services.get_raw_document``).
* ``extract`` / ``normalize`` — fully reuse the shared extraction pipeline (E1)
  so downstream signal processing is **identical** to scraped content.

Two input paths are supported by :mod:`.services`:

(a) **Uploaded FOIA responses** — accept a stored raw document (from M3's FOIA
    attachment / D3 storage) and route it through the extraction pipeline.  The
    FOIA module (M3) already wires ``upload_attachment → store_raw_document →
    enqueue_extraction``; the connector registration here ensures ``manual_upload``
    is a recognised connector type so M3's stored rows have valid provenance.

(b) **Customer CSV import** — accept an uploaded CSV, validate size + columns,
    parse rows into normalized records (tolerating malformed rows — skip + report),
    store one raw document per row via D3, and enqueue each for extraction.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Fetcher

from .base import Connector, ConnectorError, register

# ---------------------------------------------------------------------------
# Connector config
# ---------------------------------------------------------------------------


class ManualUploadConfig(BaseModel):
    """Per-recipe ``connector_config.manual_upload`` block.

    Recipes using the ``manual_upload`` connector carry no required config —
    all parameters are optional overrides for the extraction pipeline defaults.
    """

    model_config = ConfigDict(extra="forbid")

    #: prefilter passed to the extraction relevance gate (doc 19 §3). Uploaded
    #: FOIA responses are pre-vetted by a human, so defaulting to
    #: ``assume_relevant`` skips the cheap-but-noisy relevance LLM gate.
    prefilter: str = "assume_relevant"


# ---------------------------------------------------------------------------
# No-fetch fetcher — the bytes are already in S3 via D3
# ---------------------------------------------------------------------------


class _NoOpFetcher:
    """Fetcher for the manual_upload path: bytes are already in S3 (D3).

    The extraction pipeline reads the raw document via
    ``ingestion.services.get_raw_document`` + ``RawDocumentStorage.get_document``
    — there is nothing for the connector's fetcher to fetch.  Any call to
    ``fetch`` here is a programming error (the pipeline should never invoke it
    for a manual-upload job).

    ``robots_txt`` returns ``None`` (no URL, no robots.txt to check).
    """

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        raise ConnectorError(
            "manual_upload connector: fetch() must never be called — "
            "the raw document is already stored via D3; the pipeline reads it back directly."
        )

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


@register
class ManualUploadConnector(Connector):
    """Human-workflow connector (doc 18 §1 cat. H, §5 wave 5; D10).

    Routes uploaded FOIA responses and customer CSV imports through the same
    extraction pipeline as scraped content (doc 18 §1 cat. H).  The connector
    is intentionally thin: unlike scrapers it never reaches out to an external
    URL.  Its registered type name ``"manual_upload"`` is what the ingestion
    services write into ``ingestion_raw_document.connector`` so provenance rows
    are correctly identified.

    ``build_fetcher`` returns the no-op fetcher (bytes already in S3).
    ``discover`` returns an empty list — the pointer is the already-stored raw
    document id, enqueued by the upload services.
    """

    name: ClassVar[str] = "manual_upload"
    config_model: ClassVar[type[BaseModel] | None] = ManualUploadConfig

    def build_fetcher(self) -> Fetcher:
        return _NoOpFetcher()

    def discover(self, seed_urls: object) -> list[object]:  # type: ignore[override]
        """No-op — manual uploads are not discovered by polling; they are pushed."""
        return []


__all__ = [
    "ManualUploadConfig",
    "ManualUploadConnector",
]
