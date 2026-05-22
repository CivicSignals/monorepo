"""Connector abstraction + registry (doc 18 §1, §2, §5; TODO D6).

A **connector is code for a source *type*** (doc 16 §17): ``http_static``, ``rss``,
``rest_api_pager`` … . A **recipe** is declarative YAML that instantiates one
connector for a single tenant/source — there are hundreds to thousands of recipes
but only ~24 connectors. The recipe runner (D1/D11) owns the *orchestration and
policy* of the ``discover -> fetch -> extract -> normalize`` lifecycle (robots.txt,
the politeness window, version pinning, the ordered selector-fallback chain); a
connector supplies the *source-type behavior* the runner can't know generically:

* the :class:`~civicsignals_api.modules.recipes.runner.Fetcher` to use (a static
  HTTP client, a REST/JSON client, a feed/file fetcher, …), and
* a :meth:`Connector.discover` that turns a recipe's seed URLs into the concrete
  set of :class:`SourcePointer` to fetch this run (expand a feed into per-item
  pointers, walk a paginated API, check a bulk file's manifest, …).

The runner stays the single place that enforces the legal/ethical posture, so no
connector can accidentally bypass robots.txt or the politeness window (doc 18
§2.2): every connector's fetcher is driven through ``RecipeRunner.fetch``.

Connectors register themselves by **type name** (the recipe's ``connector``
field) via :func:`register`; :func:`get_connector` / :func:`connector_for`
resolve a recipe to its connector. Registration is import-time and idempotent —
importing :mod:`civicsignals_api.modules.ingestion.connectors` populates the
registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from civicsignals_api.modules.recipes.services import (
    Clock,
    Fetcher,
    Recipe,
    SourcePointer,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel


class ConnectorError(Exception):
    """A connector could not be resolved, configured, or run."""


class UnknownConnectorError(ConnectorError):
    """No connector is registered for a recipe's ``connector`` type name."""

    def __init__(self, name: str, known: Sequence[str]) -> None:
        self.name = name
        self.known = list(known)
        known_str = ", ".join(sorted(self.known)) or "<none registered>"
        super().__init__(f"unknown connector {name!r}; registered connectors: {known_str}")


class Connector(ABC):
    """Source-type connector: the seam the recipe runner dispatches to (D6).

    Subclasses set :attr:`name` (the registry key, matching a recipe's
    ``connector`` field) and :attr:`config_model` (the Pydantic model the
    per-connector ``connector_config[<name>]`` block parses into), then implement
    :meth:`build_fetcher` and (optionally) :meth:`discover`. The default
    :meth:`discover` is the trivial pass-through used by ``http_static`` — turn
    each seed URL into a pointer; connectors like ``rss`` / ``rest_api_pager`` /
    ``bulk_download`` override it to expand/compute the candidate set.

    Connectors do **not** re-implement ``extract``/``normalize``: those run
    through the shared runner so the ordered selector-fallback chain, drift
    counters, and dead-letter sink (D11) apply uniformly. A connector whose source
    is JSON/structured rather than HTML supplies its records through ``fetch`` and
    leans on the runner's field extraction over the serialized body.
    """

    #: Registry key — the recipe ``connector`` field that selects this connector.
    name: ClassVar[str]
    #: Pydantic model for the per-connector ``connector_config`` block (or ``None``
    #: for a connector that takes no config).
    config_model: ClassVar[type[BaseModel] | None] = None

    def __init__(self, recipe: Recipe, *, clock: Clock | None = None) -> None:
        self.recipe = recipe
        self.clock = clock
        self.config = self.parse_config(recipe)

    # -- config -------------------------------------------------------------
    @classmethod
    def parse_config(cls, recipe: Recipe) -> BaseModel | None:
        """Parse ``recipe.connector_config[cls.name]`` into :attr:`config_model`.

        The canonical JSON Schema (``packages/recipe-schema``) already validated
        the *shape*; this gives the connector a typed, defaulted view. Returns
        ``None`` when the connector declares no ``config_model``. A connector that
        *requires* config (e.g. ``rest_api_pager``) raises :class:`ConnectorError`
        for a missing block via its own model's required fields.
        """
        if cls.config_model is None:
            return None
        block = recipe.connector_config.get(cls.name, {})
        if not isinstance(block, dict):  # pragma: no cover - schema guards this
            raise ConnectorError(f"connector_config.{cls.name} must be a mapping")
        from pydantic import ValidationError

        try:
            return cls.config_model.model_validate(block)
        except ValidationError as exc:
            raise ConnectorError(
                f"invalid connector_config.{cls.name} for recipe {recipe.recipe_id!r}: {exc}"
            ) from exc

    # -- lifecycle hooks ----------------------------------------------------
    @abstractmethod
    def build_fetcher(self) -> Fetcher:
        """Return the :class:`Fetcher` the runner drives for this recipe.

        The fetcher must conform to the runner's ``Fetcher`` Protocol
        (synchronous ``fetch`` + ``robots_txt``); the runner — not the fetcher —
        enforces robots.txt and the politeness window (doc 18 §2.2).
        """

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Turn seed URLs into the pointers to fetch this run (doc 18 §2.1).

        Default: one pointer per seed (``http_static``). Override to expand a feed
        into per-item pointers, walk a paginated API, or short-circuit when a bulk
        file is unchanged.
        """
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
            )
            for url in seed_urls
        ]


# ----------------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------------

_REGISTRY: dict[str, type[Connector]] = {}


def register(connector_cls: type[Connector]) -> type[Connector]:
    """Register ``connector_cls`` under its :attr:`Connector.name` (a class decorator).

    Idempotent re-registration of the *same* class is allowed (modules re-import
    cleanly); registering a *different* class under a taken name is a programming
    error and raises.
    """
    name = connector_cls.name
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not connector_cls:
        raise ConnectorError(f"connector name {name!r} already registered to {existing.__name__}")
    _REGISTRY[name] = connector_cls
    return connector_cls


def registered_names() -> list[str]:
    """Sorted list of registered connector type names."""
    return sorted(_REGISTRY)


def get_connector(name: str) -> type[Connector]:
    """Resolve a connector class by type name, or raise :class:`UnknownConnectorError`."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownConnectorError(name, registered_names()) from None


def connector_for(recipe: Recipe, *, clock: Clock | None = None) -> Connector:
    """Instantiate the connector a recipe selects via its ``connector`` field."""
    return get_connector(recipe.connector)(recipe, clock=clock)


__all__ = [
    "Connector",
    "ConnectorError",
    "UnknownConnectorError",
    "connector_for",
    "get_connector",
    "register",
    "registered_names",
]
