"""``grantsgov`` — Grants.gov opportunities (DEFERRED TO V2; doc 18 §5 wave 3 #13;
D8).

Grants.gov (HHS) is the federal funding-opportunities catalog, with a read API
(``v1/api/search2``) and the newer Simpler.Grants.gov developer portal (doc 16 §2:
``https://www.grants.gov/api`` / ``https://simpler.grants.gov/developers``).
doc 18 §5 wave 3 lists it, and doc 16 §2 marks it **Tier 2 (federal grants flow to
SLED entities — Department of Education ESSER, etc.)** — but **federal coverage /
Grants.gov scraping is explicitly out of MVP scope** (TODO.md "Out of scope":
*"only public-portal grants in MVP"*).

So this is a **registration-only stub**: the ``grantsgov`` type name resolves in
the registry (referenceable; D12 can flag it as known-but-deferred), but it runs no
live ingestion — ``discover`` emits zero pointers and the fetcher refuses any
fetch. See :mod:`._deferred_federal` for the shared boundary. The full
implementation (a ``rest_api_pager``-style connector over ``search2`` /
Simpler.Grants.gov) is **# TODO v2**.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ._deferred_federal import DeferredFederalConfig, DeferredFederalConnector
from .base import register


class GrantsgovConfig(DeferredFederalConfig):
    """Per-recipe ``connector_config.grantsgov`` — deferred to v2 (see module docstring)."""


@register
class GrantsgovConnector(DeferredFederalConnector):
    """Grants.gov opportunities connector — DEFERRED TO V2 (doc 18 §5 wave 3 #13).

    Registered but not wired (TODO.md "Out of scope"). # TODO v2: implement over
    the Grants.gov ``v1/api/search2`` / Simpler.Grants.gov API (doc 16 §2).
    """

    name: ClassVar[str] = "grantsgov"
    config_model: ClassVar[type[BaseModel] | None] = GrantsgovConfig


__all__ = [
    "GrantsgovConfig",
    "GrantsgovConnector",
]
