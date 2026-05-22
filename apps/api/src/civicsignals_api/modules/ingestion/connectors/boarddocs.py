"""``boarddocs`` — BoardDocs (Diligent) meeting platform (doc 18 §1 cat. E,
§5 wave 2 #6; D7).

BoardDocs hosts public agendas/minutes for ~10,000 K-12 districts and community
colleges; its public URLs follow a stable per-tenant pattern (doc 16 §16, Tier 1):
``https://go.boarddocs.com/<state>/<subdomain>/Board.nsf/Public``. One connector
covers every district — a per-tenant recipe supplies the ``state`` + ``subdomain``
(or a full ``base_url`` override) and this connector crawls that tenant's meeting
index, emitting one pointer per meeting detail page for the runner to fetch +
extract (board_decision, rfp_posted, budget, contract_award per doc 16 §16).

Static HTML, public records — no JS rendering (doc 18 §5 prefers static). BoardDocs
serves a static meeting list; any district whose BoardDocs is fronted by a JS-only
shell is a per-recipe ``http_browser`` decision once D9 lands, noted in the
recipe's ``legal_notes`` — not handled here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ._meeting_platform import MeetingPlatformConnector
from .base import ConnectorError, register


class BoarddocsConfig(BaseModel):
    """Per-recipe ``connector_config.boarddocs`` (mirrors the JSON Schema).

    Provide either ``base_url`` (full tenant URL) or ``state`` + ``subdomain`` (the
    stable BoardDocs pattern). ``meeting_path`` is the index path appended to the
    tenant base when crawling for meeting links.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    state: str | None = None
    subdomain: str | None = None
    meeting_path: str = "Board.nsf/Public"


def _tenant_base(config: BoarddocsConfig) -> str:
    if config.base_url:
        return config.base_url.rstrip("/")
    if config.state and config.subdomain:
        return f"https://go.boarddocs.com/{config.state}/{config.subdomain}"
    raise ConnectorError(
        "boarddocs connector_config needs either base_url or state+subdomain "
        "(doc 16 §16: the per-tenant subdomain pattern)"
    )


@register
class BoarddocsConnector(MeetingPlatformConnector):
    """BoardDocs meeting/agenda connector (doc 18 §1 cat. E, §5 wave 2 #6)."""

    name: ClassVar[str] = "boarddocs"
    config_model: ClassVar[type[BaseModel] | None] = BoarddocsConfig

    def _listing_urls(self, seed_urls: Sequence[str]) -> list[str]:
        config = self.config
        assert isinstance(config, BoarddocsConfig)
        if seed_urls:
            return list(seed_urls)
        base = _tenant_base(config)
        return [f"{base}/{config.meeting_path.lstrip('/')}"]

    def _link_selectors(self) -> list[str]:
        # BoardDocs renders the meeting list as anchors carrying the agenda id.
        # Primary → fallbacks (doc 18 §3.4): the platform's stable hooks first,
        # then a generic agenda-link fallback.
        return [
            "a.meeting-link",
            "li.meeting a[href]",
            "a[href*='Agenda']",
        ]


__all__ = [
    "BoarddocsConfig",
    "BoarddocsConnector",
]
