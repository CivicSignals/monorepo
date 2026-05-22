"""Recipe DSL loader + lifecycle runner (doc 18 §2, §3; TODO D1).

This is the orchestration skeleton for the connector lifecycle
``discover -> fetch -> extract -> normalize``. It is deliberately connector- and
network-agnostic: the *connectors* (``http_static``, ``rss``, …) land in D6+, so
here we:

* load a recipe YAML and validate it against the **canonical draft-07 JSON
  Schema** in ``packages/recipe-schema`` (the single source of truth — we never
  duplicate the schema in Python),
* run the four lifecycle steps against an **injectable fetcher** so the whole
  thing is testable offline,
* honor ``robots.txt`` and a **politeness window** (delay + jitter) in ``fetch``,
* pin the ``recipe_version`` onto every raw/extracted/canonical record,
* extract fields using the recipe's **primary selector** (index 0). The ordered
  fallback chain (flag ``degraded: true`` on a fallback hit) is a clean,
  documented extension point left for D11 — see ``_extract_field``.

The public surface other modules call is in ``services.py``; this module is the
implementation it delegates to.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.robotparser
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

import yaml
from jsonschema import Draft7Validator
from jsonschema import exceptions as js_exceptions

from civicsignals_api.config import get_settings

from .schemas import (
    CanonicalRecord,
    ExtractedDocument,
    FieldSpec,
    RawDocument,
    Recipe,
    SourcePointer,
)

# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------


class RecipeError(Exception):
    """A recipe could not be loaded or run (not a schema-validation failure)."""


class RecipeValidationError(RecipeError):
    """A recipe failed validation against the canonical JSON Schema."""

    def __init__(self, recipe_ref: str, messages: Sequence[str]) -> None:
        self.recipe_ref = recipe_ref
        self.messages = list(messages)
        joined = "; ".join(self.messages)
        super().__init__(f"recipe {recipe_ref!r} is invalid: {joined}")


class RobotsDisallowedError(RecipeError):
    """The fetch target is disallowed by the host's robots.txt (doc 18 §2.2)."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"robots.txt disallows fetching {url!r}")


class RequiredFieldMissingError(RecipeError):
    """A `required: true` field produced no value (doc 18 §3.1)."""

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(f"required field {field_name!r} extracted no value")


# ----------------------------------------------------------------------------
# Schema + recipe directory resolution
# ----------------------------------------------------------------------------
# Schemas live at the monorepo root (packages/recipe-schema/schema), outside the
# api package, so we walk up from this file to find them. Containers that lay the
# repo out differently set RECIPE_SCHEMA_DIR / RECIPES_DIR (see config.py).


# `packages/recipe-schema/schema` is the unambiguous repo-root marker: unlike a
# bare `recipes`, it can't collide with this very module's directory
# (`.../modules/recipes`), so we anchor both lookups on it.
_REPO_ROOT_MARKER = "packages/recipe-schema/schema"


def _repo_root() -> Path | None:
    """Find the monorepo root by walking up to the recipe-schema marker."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _REPO_ROOT_MARKER).exists():
            return parent
    return None


def recipe_schema_dir() -> Path:
    settings = get_settings()
    if settings.recipe_schema_dir:
        return Path(settings.recipe_schema_dir)
    root = _repo_root()
    if root is None:  # pragma: no cover - misconfigured deployment
        raise RecipeError(f"could not locate {_REPO_ROOT_MARKER}; set RECIPE_SCHEMA_DIR")
    return root / _REPO_ROOT_MARKER


def recipes_dir() -> Path:
    settings = get_settings()
    if settings.recipes_dir:
        return Path(settings.recipes_dir)
    root = _repo_root()
    if root is None:  # pragma: no cover - misconfigured deployment
        raise RecipeError("could not locate the repo root; set RECIPES_DIR")
    return root / "recipes"


@lru_cache(maxsize=1)
def _recipe_validator() -> Draft7Validator:
    schema_path = recipe_schema_dir() / "recipe.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


# ----------------------------------------------------------------------------
# Load + validate
# ----------------------------------------------------------------------------


def validate_recipe_data(data: object, *, recipe_ref: str = "<inline>") -> None:
    """Validate a parsed recipe mapping against the canonical JSON Schema.

    Raises ``RecipeValidationError`` listing every schema violation. The JSON
    Schema is authoritative (doc 18 §3.1); we do not re-encode its rules here.
    """
    validator = _recipe_validator()
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        raise RecipeValidationError(recipe_ref, [_format_error(e) for e in errors])


def _format_error(error: js_exceptions.ValidationError) -> str:
    location = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def parse_recipe(data: object, *, recipe_ref: str = "<inline>") -> Recipe:
    """Validate then parse a recipe mapping into the typed :class:`Recipe`."""
    validate_recipe_data(data, recipe_ref=recipe_ref)
    # Safe: schema validation guarantees a mapping with the known shape.
    return Recipe.model_validate(data)


def load_recipe_file(path: str | Path) -> Recipe:
    """Load + validate + parse a recipe YAML file."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem error
        raise RecipeError(f"cannot read recipe {path}: {exc}") from exc
    data = yaml.safe_load(raw)
    return parse_recipe(data, recipe_ref=str(path))


def load_recipe(recipe_id: str) -> Recipe:
    """Load the recipe ``recipes/<recipe_id>/recipe.yml`` by id."""
    path = recipes_dir() / recipe_id / "recipe.yml"
    if not path.exists():
        raise RecipeError(f"no recipe {recipe_id!r} at {path}")
    return load_recipe_file(path)


# ----------------------------------------------------------------------------
# Fetcher + clock seams (injectable so the runner is testable offline)
# ----------------------------------------------------------------------------


@runtime_checkable
class Fetcher(Protocol):
    """Network seam for the runner. Real connectors (D6+) ship HTTP/browser
    fetchers; tests pass a static in-memory one. ``fetch`` never parses.
    """

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        """Return ``(status_code, body, headers)`` for ``url``."""
        ...

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        """Return the host's ``robots.txt`` body, or ``None`` if absent."""
        ...


@runtime_checkable
class Clock(Protocol):
    """Time seam so politeness delays are assertable without real sleeping."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """Default :class:`Clock` backed by the stdlib."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:  # pragma: no cover - real sleep
        if seconds > 0:
            time.sleep(seconds)


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parse_robots(robots_body: str | None) -> urllib.robotparser.RobotFileParser | None:
    """Parse a robots.txt body into a reusable matcher.

    Returns ``None`` for a missing/empty robots.txt, which is the permissive
    convention (a site that ships none allows everything). The returned matcher
    is host-wide, so it can be cached per host for the duration of a run.
    """
    if not robots_body:
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots_body.splitlines())
    return parser


def _robots_allows(
    parser: urllib.robotparser.RobotFileParser | None, url: str, user_agent: str
) -> bool:
    """True if ``parser`` permits ``user_agent`` to fetch ``url``.

    No parser (missing robots.txt) is permissive — we only block on an explicit,
    parseable disallow.
    """
    if parser is None:
        return True
    return parser.can_fetch(user_agent, url)


# ----------------------------------------------------------------------------
# Extractor: CSS-selector-based, primary selector only (D1)
# ----------------------------------------------------------------------------


def _extract_field(html: str, spec: FieldSpec) -> str | None:
    """Extract one field's value using its **primary** selector (index 0).

    D1 evaluates ``spec.selectors[0]`` only. The ordered fallback chain — try
    each later selector, flag ``degraded: true`` and tick the drift counter when
    a fallback matches — is D11. The extension point is intentional: iterate
    ``spec.selectors`` here and thread a ``degraded`` flag back out.
    """
    try:
        from bs4 import BeautifulSoup
        from bs4.element import Tag
    except ImportError as exc:  # pragma: no cover - guards a misbuilt image
        raise RecipeError(
            "the recipe runner's HTML extractor needs beautifulsoup4 "
            "(a core dependency of civicsignals-api); install the package deps"
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    primary = spec.selectors[0]
    element = soup.select_one(primary)
    if element is None or not isinstance(element, Tag):
        return None
    if spec.attr is not None:
        value = element.get(spec.attr)
        if value is None:
            return None
        if isinstance(value, list):  # multi-valued attr (e.g. class)
            return " ".join(value)
        return str(value)
    return element.get_text(strip=True)


# ----------------------------------------------------------------------------
# The runner
# ----------------------------------------------------------------------------


class RecipeRunner:
    """Drives a recipe through ``discover -> fetch -> extract -> normalize``.

    The runner owns *orchestration and policy* (politeness, robots, version
    pinning); connectors own *source-type behavior* (D6+). Network access is
    fully behind the injected :class:`Fetcher`, so a run is reproducible offline.
    """

    def __init__(
        self,
        recipe: Recipe,
        fetcher: Fetcher,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.recipe = recipe
        self.fetcher = fetcher
        self.clock = clock if clock is not None else RealClock()
        # Per-host timestamp of the last fetch, for the politeness window.
        self._last_fetch_at: dict[str, float] = {}
        # Per-host parsed robots.txt, cached for the duration of this run so a
        # multi-page crawl of one host fetches robots.txt once, not per URL.
        # ``host in cache`` distinguishes "not yet looked up" from a cached
        # ``None`` (host ships no robots.txt — permissive).
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # -- discover -----------------------------------------------------------
    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Turn seed URLs into pointers (doc 18 §2.1).

        D1's discover is the trivial pass-through: a connector (D6+) computes the
        real candidate set (paginated listing, RSS items, sitemap, …). Keeping
        it a method makes that override a one-liner.
        """
        return [
            SourcePointer(
                recipe_id=self.recipe.recipe_id,
                connector=self.recipe.connector,
                url=url,
            )
            for url in seed_urls
        ]

    # -- politeness ---------------------------------------------------------
    def _apply_politeness(self, host: str) -> None:
        """Sleep so consecutive same-host fetches are >= politeness_seconds apart,
        plus up to ``jitter_seconds`` of random extra delay (doc 18 §2.2)."""
        policy = self.recipe.fetch
        last = self._last_fetch_at.get(host)
        if last is not None:
            elapsed = self.clock.monotonic() - last
            wait = policy.politeness_seconds - elapsed
            if wait > 0:
                self.clock.sleep(wait)
        if policy.jitter_seconds > 0:
            self.clock.sleep(random.uniform(0, policy.jitter_seconds))

    # -- robots -------------------------------------------------------------
    def _robots_for(
        self, host: str, url: str, user_agent: str
    ) -> urllib.robotparser.RobotFileParser | None:
        """Return the cached robots matcher for ``host``, fetching it once.

        robots.txt is host-wide, so a multi-page crawl consults the fetcher only
        on the first URL per host (doc 16 §18; avoids redundant load on the
        source). The cache lives for the duration of this run.
        """
        if host not in self._robots_cache:
            body = self.fetcher.robots_txt(url, user_agent=user_agent)
            self._robots_cache[host] = _parse_robots(body)
        return self._robots_cache[host]

    # -- fetch --------------------------------------------------------------
    def fetch(self, pointer: SourcePointer) -> RawDocument:
        """Retrieve raw bytes for one pointer (doc 18 §2.2).

        Enforces robots.txt + the politeness window, pins ``recipe_version``, and
        never parses. Storing to S3 (the ``raw_document`` row) is D3.
        """
        policy = self.recipe.fetch
        host = urlparse(pointer.url).netloc

        if policy.respect_robots_txt:
            parser = self._robots_for(host, pointer.url, policy.user_agent)
            if not _robots_allows(parser, pointer.url, policy.user_agent):
                raise RobotsDisallowedError(pointer.url)

        self._apply_politeness(host)
        status, body, headers = self.fetcher.fetch(
            pointer.url,
            user_agent=policy.user_agent,
            max_redirects=policy.max_redirects,
        )
        self._last_fetch_at[host] = self.clock.monotonic()

        return RawDocument(
            recipe_id=self.recipe.recipe_id,
            connector=self.recipe.connector,
            url=pointer.url,
            status_code=status,
            content=body,
            content_hash=_content_hash(body),
            recipe_version=self.recipe.version,
            headers=headers,
        )

    # -- extract ------------------------------------------------------------
    def extract(self, raw: RawDocument) -> ExtractedDocument:
        """Run the primary selectors over a raw doc (doc 18 §2.3, §3.1).

        Required fields (``required: true``) that yield nothing raise
        :class:`RequiredFieldMissingError` — the boundary contract (doc 18 §3.1):
        invalid extractions are rejected, not silently swallowed.
        """
        out: dict[str, str | None] = {}
        for name, spec in self.recipe.fields.items():
            value = _extract_field(raw.content, spec)
            if value is None and spec.required:
                raise RequiredFieldMissingError(name)
            out[name] = value

        signal_type = self.recipe.signal_types[0] if self.recipe.signal_types else None
        return ExtractedDocument(
            recipe_id=self.recipe.recipe_id,
            recipe_version=self.recipe.version,
            signal_type=signal_type,
            extraction_method="primary",
            degraded=False,
            fields=out,
        )

    # -- normalize ----------------------------------------------------------
    def normalize(self, extracted: ExtractedDocument, source_url: str) -> list[CanonicalRecord]:
        """Map an extraction to canonical records (doc 18 §2.4, doc 16 §17.3).

        D1 emits the skeleton record carrying the recipe's entity ref + extracted
        fields + provenance. Entity resolution and signal scoring are downstream
        (doc 14, doc 18 §2.4).
        """
        return [
            CanonicalRecord(
                record_type="Signal",
                recipe_id=self.recipe.recipe_id,
                recipe_version=self.recipe.version,
                source_url=source_url,
                entity=self.recipe.entity,
                signal_type=extracted.signal_type,
                degraded=extracted.degraded,
                fields=extracted.fields,
            )
        ]

    # -- full lifecycle -----------------------------------------------------
    def run(self, seed_urls: Sequence[str]) -> list[CanonicalRecord]:
        """Run the whole lifecycle for the given seed URLs."""
        records: list[CanonicalRecord] = []
        for pointer in self.discover(seed_urls):
            raw = self.fetch(pointer)
            extracted = self.extract(raw)
            records.extend(self.normalize(extracted, pointer.url))
        return records

    def extract_html(self, html: str, source_url: str = "fixture://local") -> ExtractedDocument:
        """Extract straight from an HTML string (used by golden-fixture replay).

        Skips fetch/discover so a committed fixture is exercised through the same
        ``extract`` path the live runner uses.
        """
        raw = RawDocument(
            recipe_id=self.recipe.recipe_id,
            connector=self.recipe.connector,
            url=source_url,
            status_code=200,
            content=html,
            content_hash=_content_hash(html),
            recipe_version=self.recipe.version,
        )
        return self.extract(raw)


__all__ = [
    "Clock",
    "Fetcher",
    "RealClock",
    "Recipe",
    "RecipeError",
    "RecipeRunner",
    "RecipeValidationError",
    "RequiredFieldMissingError",
    "RobotsDisallowedError",
    "load_recipe",
    "load_recipe_file",
    "parse_recipe",
    "recipe_schema_dir",
    "recipes_dir",
    "validate_recipe_data",
]
