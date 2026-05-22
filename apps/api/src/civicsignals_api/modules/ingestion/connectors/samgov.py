"""``samgov`` — SAM.gov opportunities (DEFERRED TO V2; doc 18 §5 wave 3 #12; D8).

SAM.gov (System for Award Management, GSA) is the federal opportunities + entity
registration system, with a free Get Opportunities Public API (doc 16 §2:
``https://open.gsa.gov/api/get-opportunities-public-api/``). doc 18 §5 wave 3 lists
it for parity, but doc 16 §2 marks it **Tier 3 (v2 federal expansion)** and
**federal coverage / SAM.gov scraping is explicitly out of MVP scope** (TODO.md
"Out of scope": *"only public-portal grants in MVP"*).

So this is a **registration-only stub**: the ``samgov`` type name resolves in the
registry (so the connector is referenceable and D12's coverage plan can flag it as
known-but-deferred), but it runs no live ingestion — ``discover`` emits zero
pointers and the fetcher refuses any fetch. See :mod:`._deferred_federal` for the
shared boundary. The full implementation (a ``rest_api_pager``-style connector over
the Get Opportunities Public API, with the GSA API key) is **# TODO v2**.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ._deferred_federal import DeferredFederalConfig, DeferredFederalConnector
from .base import register


class SamgovConfig(DeferredFederalConfig):
    """Per-recipe ``connector_config.samgov`` — deferred to v2 (see module docstring)."""


@register
class SamgovConnector(DeferredFederalConnector):
    """SAM.gov opportunities connector — DEFERRED TO V2 (doc 18 §5 wave 3 #12).

    Registered but not wired (TODO.md "Out of scope"). # TODO v2: implement over
    the GSA Get Opportunities Public API (doc 16 §2).
    """

    name: ClassVar[str] = "samgov"
    config_model: ClassVar[type[BaseModel] | None] = SamgovConfig


__all__ = [
    "SamgovConfig",
    "SamgovConnector",
]
