"""``granicus_peak`` — Granicus PEAK meeting platform (doc 18 §1 cat. E,
§5 wave 2 #7; D7).

Granicus (PEAK / Legistar / GovDelivery suite) serves 7,000+ government orgs —
cities, counties, universities — hosting agendas, minutes, and video on a stable
per-tenant subdomain (doc 16 §16, Tier 1):
``https://<subdomain>.granicus.com/ViewPublisher.php?view_id=<n>``. One connector
covers every org — a per-tenant recipe supplies the ``subdomain`` + ``view_id``
(or a full ``base_url``) and this connector crawls that org's meeting index,
emitting one pointer per meeting/agenda detail page for the runner to fetch +
extract (board_decision, rfp_posted, budget, contract_award per doc 16 §16).

Static HTML, public records — no JS rendering (doc 18 §5 prefers static). Granicus
PEAK serves a static meeting archive; iCal/RSS feeds some tenants expose are a
separate ``rss`` recipe, not this connector. Legistar (the legislative-info
sibling) is a Tier-2 future connector; this one targets the PEAK meeting archive.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ._meeting_platform import MeetingPlatformConnector
from .base import ConnectorError, register


class GranicusPeakConfig(BaseModel):
    """Per-recipe ``connector_config.granicus_peak`` (mirrors the JSON Schema).

    Provide either ``base_url`` (full tenant URL) or ``subdomain`` (+ optional
    ``view_id`` for the PEAK ``ViewPublisher`` archive). ``meeting_path`` is the
    archive path appended to the tenant base when crawling for meeting links.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    subdomain: str | None = None
    view_id: int | None = None
    meeting_path: str = "ViewPublisher.php"


def _listing_url(config: GranicusPeakConfig) -> str:
    if config.base_url:
        base = config.base_url.rstrip("/")
    elif config.subdomain:
        base = f"https://{config.subdomain}.granicus.com"
    else:
        raise ConnectorError(
            "granicus_peak connector_config needs either base_url or subdomain "
            "(doc 16 §16: the per-tenant subdomain pattern)"
        )
    path = config.meeting_path.lstrip("/")
    if config.view_id is not None and "view_id" not in path:
        return f"{base}/{path}?view_id={config.view_id}"
    return f"{base}/{path}"


@register
class GranicusPeakConnector(MeetingPlatformConnector):
    """Granicus PEAK meeting/agenda connector (doc 18 §1 cat. E, §5 wave 2 #7)."""

    name: ClassVar[str] = "granicus_peak"
    config_model: ClassVar[type[BaseModel] | None] = GranicusPeakConfig

    def _listing_urls(self, seed_urls: Sequence[str]) -> list[str]:
        config = self.config
        assert isinstance(config, GranicusPeakConfig)
        if seed_urls:
            return list(seed_urls)
        return [_listing_url(config)]

    def _link_selectors(self) -> list[str]:
        # Granicus PEAK lists meetings in an archive table; agenda/minutes links
        # carry the meeting id. Primary → fallbacks (doc 18 §3.4).
        return [
            "a.agenda-link",
            "table.listingTable a[href*='AgendaViewer']",
            "a[href*='MetaViewer']",
            "a[href*='agenda']",
        ]


__all__ = [
    "GranicusPeakConfig",
    "GranicusPeakConnector",
]
