"""``civicplus`` — CivicPlus / CivicClerk municipal platform (doc 18 §1 cat. E,
§5 wave 2 #8; D7).

CivicPlus is a municipal-government CMS used by 2,500+ local governments;
CivicClerk is its agenda-management module (doc 16 §16, Tier 1). Public meeting
materials live on a stable per-tenant host
(``https://<subdomain>.civicweb.net`` / ``<subdomain>.civicplus.com`` /
``<subdomain>.api.civicclerk.com``). One connector covers every municipality — a
per-tenant recipe supplies the ``base_url`` (or ``subdomain`` + ``platform``) and
this connector crawls that town's meeting/agenda index, emitting one pointer per
detail page for the runner to fetch + extract (board_decision, rfp_posted, budget
per doc 16 §16).

Static HTML, public records — no JS rendering (doc 18 §5 prefers static). The
CivicWeb/CivicClerk meeting portals serve a static list. Newer CivicClerk
deployments that render their list entirely client-side (a SPA with no static
fallback) are the one place in wave 2 most likely to need the headless browser:
that is a **per-recipe** ``http_browser`` switch once D9 lands, noted in the
recipe's ``legal_notes`` — we do not render JS inside this connector.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ._meeting_platform import MeetingPlatformConnector
from .base import ConnectorError, register

_PLATFORM_HOST = {
    "civicweb": "civicweb.net",
    "civicplus": "civicplus.com",
    "civicclerk": "api.civicclerk.com",
}


class CivicplusConfig(BaseModel):
    """Per-recipe ``connector_config.civicplus`` (mirrors the JSON Schema).

    Provide either ``base_url`` (full tenant URL) or ``subdomain`` + ``platform``
    (one of ``civicweb`` / ``civicplus`` / ``civicclerk``). ``meeting_path`` is the
    index path appended to the tenant base when crawling for meeting links.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    subdomain: str | None = None
    platform: Literal["civicweb", "civicplus", "civicclerk"] = "civicweb"
    meeting_path: str = "Portal/MeetingTypeList.aspx"


def _tenant_base(config: CivicplusConfig) -> str:
    if config.base_url:
        return config.base_url.rstrip("/")
    if config.subdomain:
        host = _PLATFORM_HOST[config.platform]
        return f"https://{config.subdomain}.{host}"
    raise ConnectorError(
        "civicplus connector_config needs either base_url or subdomain+platform "
        "(doc 16 §16: the per-tenant subdomain pattern)"
    )


@register
class CivicplusConnector(MeetingPlatformConnector):
    """CivicPlus / CivicClerk meeting connector (doc 18 §1 cat. E, §5 wave 2 #8)."""

    name: ClassVar[str] = "civicplus"
    config_model: ClassVar[type[BaseModel] | None] = CivicplusConfig

    def _listing_urls(self, seed_urls: Sequence[str]) -> list[str]:
        config = self.config
        assert isinstance(config, CivicplusConfig)
        if seed_urls:
            return list(seed_urls)
        base = _tenant_base(config)
        return [f"{base}/{config.meeting_path.lstrip('/')}"]

    def _link_selectors(self) -> list[str]:
        # CivicWeb/CivicClerk render the meeting list as anchors to per-meeting
        # detail pages. Primary → fallbacks (doc 18 §3.4).
        return [
            "a.meeting-detail-link",
            "table.meetings a[href*='Detail']",
            "a[href*='MeetingDetail']",
            "a[href*='Meeting']",
        ]


__all__ = [
    "CivicplusConfig",
    "CivicplusConnector",
]
