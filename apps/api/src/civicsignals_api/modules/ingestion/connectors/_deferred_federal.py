"""Deferred-to-v2 federal connectors — registration-only stubs (doc 18 §5 wave 3
#12-#13; TODO D8, "Out of scope" in TODO.md).

``samgov`` (SAM.gov opportunities API) and ``grantsgov`` (Grants.gov /
Simpler.Grants.gov API) are listed in doc 18 §5 wave 3 for parity, but **federal
coverage — SAM.gov / Grants.gov scraping — is explicitly out of MVP scope**
(TODO.md "Out of scope": *"Federal coverage / SAM.gov / Grants.gov scraping (v2;
only public-portal grants in MVP)"*). doc 16 §2 marks SAM.gov **Tier 3 (v2 federal
expansion)** and Grants.gov **Tier 2 (federal grants flow to SLED entities)**.

So in MVP these are **registered but not wired for live ingestion**: the connector
type names resolve (the registry and a recipe can reference them, and D12's
coverage plan can list them as known-but-deferred), but neither runs a network
fetch. Concretely:

* ``discover`` returns **zero pointers** — a scheduled run is a no-op, never
  reaching outward to a federal API. This is the safe deferral: nothing is fetched,
  parsed, or stored.
* ``build_fetcher`` returns a fetcher whose ``fetch`` raises
  :class:`DeferredConnectorError` — so even a hand-built pointer can't trigger a
  live federal call. The error names the v2 boundary explicitly.

When v2 picks up federal coverage, each becomes a real
:class:`~.rest_api_pager.RestApiPagerConnector`-style connector (both sources are
documented JSON APIs — doc 16 §2: SAM.gov Get Opportunities Public API,
Grants.gov ``v1/api/search2`` / Simpler.Grants.gov). Until then, this module is the
single, auditable place the boundary lives.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Fetcher, SourcePointer

from .base import Connector, ConnectorError


class DeferredConnectorError(ConnectorError):
    """A v2-deferred connector was driven for a live fetch in MVP (doc 18 §5 wave 3).

    Raised only if a hand-built pointer reaches the fetcher; the normal path
    (``discover`` returns no pointers) never triggers it.
    """


class DeferredFederalConfig(BaseModel):
    """Config block for a deferred federal connector.

    Intentionally empty + permissive: a recipe may reference the connector to
    document intended v2 coverage, but no field is wired to a live call. Extra keys
    are tolerated so a forward-looking recipe (already sketching the v2 params)
    still validates.
    """

    model_config = ConfigDict(extra="allow")


class _DeferredFetcher:
    """A :class:`Fetcher` that refuses every fetch — the v2 boundary as code.

    ``discover`` returns no pointers so this is never reached on the normal path;
    it exists so a hand-built pointer can't smuggle a live federal call past the
    deferral (doc 18 §5 wave 3; TODO.md "Out of scope").
    """

    def __init__(self, connector_name: str) -> None:
        self._connector_name = connector_name

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        raise DeferredConnectorError(
            f"{self._connector_name!r} is deferred to v2 (federal coverage is out of MVP "
            "scope — TODO.md 'Out of scope'); live fetching is not wired. "
            "Only public-portal grants are in MVP."
        )

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:  # pragma: no cover - unused
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> _DeferredFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class DeferredFederalConnector(Connector):
    """Base for the v2-deferred federal connectors (registered, not wired).

    Subclasses set :attr:`name`; both ``discover`` (no pointers) and
    ``build_fetcher`` (a refusing fetcher) enforce the deferral so the connector is
    a safe no-op in MVP while still resolvable in the registry.
    """

    config_model: ClassVar[type[BaseModel] | None] = DeferredFederalConfig
    #: Marks this connector as a deferred-to-v2 stub (auditable in the registry).
    deferred_to_v2: ClassVar[bool] = True

    def build_fetcher(self) -> Fetcher:
        # TODO v2: replace with a real RestApiPagerFetcher when federal coverage
        # lands (doc 16 §2; doc 18 §5 wave 3).
        return _DeferredFetcher(self.name)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        # TODO v2: page the federal opportunities/grants API. In MVP we emit no
        # pointers so a scheduled run never reaches outward (doc 18 §5 wave 3).
        return []


__all__ = [
    "DeferredConnectorError",
    "DeferredFederalConfig",
    "DeferredFederalConnector",
]
