"""Recipe DSL loader + lifecycle runner (doc 18 §2, §3; TODO D1, D11).

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
* extract fields through the **ordered fallback chain** (doc 18 §3.4): primary
  selector → fallback selector(s) → optional **LLM-assisted** extraction →
  **dead-letter**. The first non-empty result wins; a non-primary win flags the
  document ``degraded: true`` and ticks the per-recipe drift counters that E7
  (drift detection) will consume. See ``_extract_field``.

The public surface other modules call is in ``services.py``; this module is the
implementation it delegates to.
"""

from __future__ import annotations

import asyncio
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
from bs4 import BeautifulSoup
from bs4.element import Tag
from jsonschema import Draft7Validator
from jsonschema import exceptions as js_exceptions

from civicsignals_api.config import get_settings

from .schemas import (
    CanonicalRecord,
    DeadLetterEntry,
    DriftCounters,
    ExtractedDocument,
    ExtractionMethod,
    FieldExtraction,
    FieldSpec,
    RawDocument,
    Recipe,
    Selector,
    SelectorType,
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


class RecipeNotFoundError(RecipeError):
    """No recipe exists for the requested id/path (distinct so callers can map
    it to a 404 without parsing message text)."""


class FetchFailedError(RecipeError):
    """A fetch could not be completed (network/transport error). Distinct so
    callers can map it to a 502 without parsing message text."""

    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        self.detail = detail
        super().__init__(f"failed to fetch {url!r}: {detail}")


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


def parse_recipe_yaml(text: str, *, recipe_ref: str = "<inline>") -> Recipe:
    """Validate + parse a recipe from a raw YAML string (doc 18 §3).

    The authoring tooling (TODO D5) accepts an inline recipe (pasted into the
    staff UI, or piped to the CLI) without it living on disk yet. A YAML parse
    error is surfaced as a :class:`RecipeError` (not a bare ``yaml`` exception)
    so callers handle one recipe-domain error type.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RecipeError(f"recipe {recipe_ref!r} is not valid YAML: {exc}") from exc
    return parse_recipe(data, recipe_ref=recipe_ref)


def load_recipe_file(path: str | Path) -> Recipe:
    """Load + validate + parse a recipe YAML file."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem error
        raise RecipeError(f"cannot read recipe {path}: {exc}") from exc
    return parse_recipe_yaml(raw, recipe_ref=str(path))


def load_recipe(recipe_id: str) -> Recipe:
    """Load the recipe ``recipes/<recipe_id>/recipe.yml`` by id."""
    path = recipes_dir() / recipe_id / "recipe.yml"
    if not path.exists():
        raise RecipeNotFoundError(f"no recipe {recipe_id!r} at {path}")
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


# ----------------------------------------------------------------------------
# LLM-assisted extraction seam (doc 18 §2.3, §3.4)
# ----------------------------------------------------------------------------
# The third rung of the fallback chain: when every CSS/XPath selector misses on a
# field flagged ``llm_assisted``, ask the LLM gateway to pull the value out of the
# raw text. The seam is a Protocol so tests inject a deterministic extractor
# (backed by the gateway's FakeBackend) and the runner stays offline; production
# wires the real gateway via :class:`GatewayFieldExtractor`.


@runtime_checkable
class LLMFieldExtractor(Protocol):
    """Extract one field's value from raw text via the LLM gateway.

    Returns the extracted string, or ``None`` when the model produced nothing
    usable (which dead-letters the field). Implementations must route through
    ``civicsignals_api.llm_gateway`` — never a vendor SDK directly (doc 06 §7).
    """

    def extract_field(
        self,
        *,
        field_name: str,
        text: str,
        recipe_id: str,
        prompt_name: str | None,
    ) -> str | None: ...


# Default LLM-assisted extraction prompt. The prompt registry (TODO E3) will
# resolve ``prompt_name`` to a versioned prompt; until it lands we inline this
# sensible default and thread ``prompt_name`` through for accounting/provenance.
_DEFAULT_LLM_EXTRACTION_SYSTEM = (
    "You extract a single field value from a web page's visible text. "
    "Return only the field's value as plain text, with no labels, quotes, or "
    "commentary. If the value is not present, return an empty string."
)
# Cap the raw text we hand the model so a pathological page can't blow the token
# budget; the meaningful content for a single field is near the top of the doc.
_LLM_TEXT_CHAR_BUDGET = 12_000


class GatewayFieldExtractor:
    """Default :class:`LLMFieldExtractor` backed by the shared LLM gateway.

    Uses the gateway's ``extraction`` task policy (a small/cheap model first, per
    doc 18 §6.6) and the inline default prompt. The gateway is imported lazily so
    the recipes module imports cleanly in images without the extraction extra; a
    missing/disabled backend surfaces as ``None`` (field dead-letters) rather
    than crashing the whole run.
    """

    def __init__(self, *, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def extract_field(
        self,
        *,
        field_name: str,
        text: str,
        recipe_id: str,
        prompt_name: str | None,
    ) -> str | None:
        # Lazy import: only the extract worker carries the gateway's deps.
        from civicsignals_api.llm_gateway import TASK_EXTRACTION, LLMError, get_gateway

        gateway = get_gateway()
        prompt = (
            f"# TODO E3: replace with the versioned registry prompt "
            f"{prompt_name or '<default>'!r}.\n"
            f"Recipe: {recipe_id}\n"
            f"Extract the value of the field {field_name!r} from this page text:\n\n"
            f"{text[:_LLM_TEXT_CHAR_BUDGET]}"
        )
        try:
            result = asyncio.run(
                gateway.complete(
                    prompt=prompt,
                    task=TASK_EXTRACTION,
                    system=_DEFAULT_LLM_EXTRACTION_SYSTEM,
                    max_tokens=256,
                    workspace_id=self._workspace_id,
                    prompt_name=prompt_name,
                )
            )
        except LLMError:
            # Provider down / no backend / permanent error -> dead-letter the
            # field. Drift counters + the dead-letter sink make this visible.
            return None
        value = result.text.strip()
        return value or None


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
# Extractor: ordered selector fallback chain (CSS/XPath), then LLM, then
# dead-letter (D11; doc 18 §3.4)
# ----------------------------------------------------------------------------


def _parse_html(html: str) -> BeautifulSoup:
    """Parse ``html`` into a single soup, parsed once per document.

    beautifulsoup4 is a core dependency of ``civicsignals-api`` (see pyproject),
    so a missing import here is a build/packaging error, surfaced at import time.
    """
    return BeautifulSoup(html, "html.parser")


def _value_from_element(element: Tag, attr: str | None) -> str | None:
    """Read ``attr`` (or the text content) off a matched element.

    Returns ``None`` for an empty/absent value so the caller treats it as a miss
    and advances to the next selector (first non-empty result wins, doc 18 §3.4).
    """
    if attr is not None:
        value = element.get(attr)
        if value is None:
            return None
        text = " ".join(value) if isinstance(value, list) else str(value)
    else:
        text = element.get_text(strip=True)
    return text or None


def _eval_css(soup: BeautifulSoup, selector: str, attr: str | None) -> str | None:
    element = soup.select_one(selector)
    if element is None or not isinstance(element, Tag):
        return None
    return _value_from_element(element, attr)


class ParsedDocument:
    """A document parsed once, shared across every field's selector chain.

    Holds the BeautifulSoup DOM (for CSS) and lazily parses an lxml tree (for
    XPath) **at most once per document** — so a recipe with several XPath
    selectors/fields does not re-parse the whole HTML each time. lxml ships only
    with the ``ingestion`` extra, so it is imported lazily and only when a recipe
    actually uses an XPath selector.
    """

    def __init__(self, soup: BeautifulSoup, html: str) -> None:
        self.soup = soup
        self.html = html
        self._lxml_tree: object | None = None
        self._lxml_parsed = False

    def _xpath_tree(self) -> object:
        if not self._lxml_parsed:
            try:
                from lxml import html as lxml_html  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - depends on install extra
                raise RecipeError(
                    "xpath selectors require lxml (install the 'ingestion' extra)"
                ) from exc
            self._lxml_tree = lxml_html.fromstring(self.html)
            self._lxml_parsed = True
        return self._lxml_tree

    def eval_xpath(self, selector: str, attr: str | None) -> str | None:
        tree = self._xpath_tree()
        matches = tree.xpath(selector)  # type: ignore[attr-defined]
        if not matches:
            return None
        first = matches[0]
        if attr is not None:
            # An element node -> read the attribute; a string result (the xpath
            # already selected `@attr`/`text()`) -> use it directly.
            value = first.get(attr) if hasattr(first, "get") else None
            text = "" if value is None else str(value)
        elif hasattr(first, "text_content"):
            text = str(first.text_content()).strip()
        else:
            text = str(first).strip()
        return text or None

    def eval_selector(self, selector: Selector, attr: str | None) -> str | None:
        """Evaluate one selector (CSS or XPath) and return its value or ``None``."""
        if selector.type is SelectorType.XPATH:
            return self.eval_xpath(selector.selector, attr)
        return _eval_css(self.soup, selector.selector, attr)


def _extract_field(doc: ParsedDocument, name: str, spec: FieldSpec) -> FieldExtraction:
    """Run the ordered *selector* chain for one field (doc 18 §3.4).

    Tries ``spec.selectors`` in order; the first non-empty match wins. Index 0 is
    the primary (``method=primary``); any later hit is a fallback
    (``method=fallback``, which flags the document degraded). Takes the shared
    :class:`ParsedDocument` so ``extract()`` parses the document once (CSS and,
    lazily, XPath) regardless of field count. The LLM-assisted and dead-letter
    rungs are applied by the caller (:meth:`RecipeRunner._resolve_field`) because
    they need the raw text and the injected LLM extractor.
    """
    for index, selector in enumerate(spec.selectors):
        value = doc.eval_selector(selector, spec.attr)
        if value is not None:
            return FieldExtraction(
                name=name,
                value=value,
                method=ExtractionMethod.PRIMARY if index == 0 else ExtractionMethod.FALLBACK,
                selector_index=index,
                selector=selector.selector,
            )
    # No selector matched — the caller decides between the LLM rung and
    # dead-letter. Report the miss as dead-letter; the caller may upgrade it.
    return FieldExtraction(name=name, value=None, method=ExtractionMethod.DEAD_LETTER)


# Document-level escalation severity, used to derive the high-water mark
# ``extraction_method`` from the per-field results.
_METHOD_SEVERITY: dict[ExtractionMethod, int] = {
    ExtractionMethod.PRIMARY: 0,
    ExtractionMethod.FALLBACK: 1,
    ExtractionMethod.LLM_ASSISTED: 2,
    ExtractionMethod.DEAD_LETTER: 3,
}


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
        llm_extractor: LLMFieldExtractor | None = None,
    ) -> None:
        self.recipe = recipe
        self.fetcher = fetcher
        self.clock = clock if clock is not None else RealClock()
        # LLM-assisted fallback rung (doc 18 §3.4). Lazily defaulted to the
        # gateway-backed extractor only when a field actually opts in via
        # ``llm_assisted: true`` — tests inject a deterministic one, and a
        # selector-only recipe never constructs the gateway.
        self._llm_extractor = llm_extractor
        # Per-host timestamp of the last fetch, for the politeness window.
        self._last_fetch_at: dict[str, float] = {}
        # Per-host parsed robots.txt, cached for the duration of this run so a
        # multi-page crawl of one host fetches robots.txt once, not per URL.
        # ``host in cache`` distinguishes "not yet looked up" from a cached
        # ``None`` (host ships no robots.txt — permissive).
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _llm(self) -> LLMFieldExtractor:
        """Return the LLM extractor, defaulting to the gateway-backed one.

        Constructed lazily so a recipe with no ``llm_assisted`` field never
        touches the gateway (and so tests that inject one win).
        """
        if self._llm_extractor is None:
            self._llm_extractor = GatewayFieldExtractor()
        return self._llm_extractor

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
        """Space same-host fetches >= ``politeness_seconds`` apart, then add up to
        ``jitter_seconds`` of random delay before *every* request (independent of
        the politeness wait, including the first) to avoid lockstep patterns
        (doc 18 §2.2)."""
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
    def _resolve_field(
        self,
        *,
        doc: ParsedDocument,
        page_text: str,
        name: str,
        spec: FieldSpec,
        raw: RawDocument,
    ) -> tuple[FieldExtraction, DeadLetterEntry | None]:
        """Resolve one field through the full chain (doc 18 §3.4).

        Selector chain → (if ``llm_assisted``) LLM rung → dead-letter. Returns the
        winning :class:`FieldExtraction` and, when the field could not be
        extracted at all, a :class:`DeadLetterEntry` describing the miss.
        """
        result = _extract_field(doc, name, spec)
        if result.method is not ExtractionMethod.DEAD_LETTER:
            return result, None  # primary or fallback selector hit

        # All selectors missed. Try the LLM rung iff this field opted in.
        if spec.llm_assisted:
            value = self._llm().extract_field(
                field_name=name,
                text=page_text,
                recipe_id=self.recipe.recipe_id,
                prompt_name=spec.prompt_name,
            )
            if value is not None:
                return (
                    FieldExtraction(name=name, value=value, method=ExtractionMethod.LLM_ASSISTED),
                    None,
                )
            reason = "all selectors missed; llm-assisted extraction returned nothing"
        else:
            reason = "all selectors missed; llm-assisted extraction disabled for this field"

        # Dead-letter: nothing produced a value. Record why + which selectors we
        # tried so a human (and E7) can fix the recipe, then replay (doc 18 §3.6).
        entry = DeadLetterEntry(
            recipe_id=self.recipe.recipe_id,
            recipe_version=self.recipe.version,
            source_url=raw.url,
            content_hash=raw.content_hash,
            field_name=name,
            reason=reason,
            tried_selectors=[s.selector for s in spec.selectors],
        )
        return result, entry

    def extract(self, raw: RawDocument) -> ExtractedDocument:
        """Run the ordered fallback chain over a raw doc (doc 18 §2.3, §3.1, §3.4).

        Each field is resolved primary → fallback(s) → optional LLM-assisted →
        dead-letter; the first non-empty result wins. A non-primary win flags the
        document ``degraded: true`` and ticks the per-recipe drift counters
        (consumed later by E7). A ``required: true`` field that produces nothing —
        even after the LLM rung — raises :class:`RequiredFieldMissingError`: the
        boundary contract (doc 18 §3.1) rejects an invalid extraction rather than
        silently swallowing it. Because that aborts the whole document, the
        dead-letter list returned here covers only *optional* fields that missed;
        a required-field miss is signalled by the exception itself.
        """
        doc = ParsedDocument(_parse_html(raw.content), raw.content)  # parsed once
        # Visible text for the LLM rung; computed once and only when needed.
        page_text = doc.soup.get_text(" ", strip=True) if self._any_llm_assisted() else ""

        out: dict[str, str | None] = {}
        field_extractions: list[FieldExtraction] = []
        degraded_fields: list[str] = []
        dead_letters: list[DeadLetterEntry] = []
        drift = DriftCounters(fields_total=len(self.recipe.fields))

        for name, spec in self.recipe.fields.items():
            result, dead_letter = self._resolve_field(
                doc=doc, page_text=page_text, name=name, spec=spec, raw=raw
            )
            field_extractions.append(result)
            out[name] = result.value

            if result.method is ExtractionMethod.FALLBACK:
                drift.selector_fallbacks += 1
                drift.fallback_by_field[name] = drift.fallback_by_field.get(name, 0) + 1
            elif result.method is ExtractionMethod.LLM_ASSISTED:
                drift.llm_fallbacks += 1
                drift.llm_by_field[name] = drift.llm_by_field.get(name, 0) + 1
            if result.degraded:
                degraded_fields.append(name)
            if dead_letter is not None:
                drift.dead_letters += 1
                drift.dead_letter_by_field[name] = drift.dead_letter_by_field.get(name, 0) + 1
                dead_letters.append(dead_letter)

            if result.value is None and spec.required:
                raise RequiredFieldMissingError(name)

        # Document escalation high-water mark across all fields.
        method = ExtractionMethod.PRIMARY
        for result in field_extractions:
            if _METHOD_SEVERITY[result.method] > _METHOD_SEVERITY[method]:
                method = result.method

        # Carry the recipe's full declared signal-type set through — the runner
        # does not guess which one a given record is. Selecting/assigning a
        # concrete signal_type per record is a connector/normalize concern
        # (doc 16 §17.3: connectors produce canonical records; the matcher decides
        # signals). The runner must not silently drop the recipe's other types.
        return ExtractedDocument(
            recipe_id=self.recipe.recipe_id,
            recipe_version=self.recipe.version,
            signal_types=list(self.recipe.signal_types),
            extraction_method=method,
            degraded=bool(degraded_fields),
            fields=out,
            field_extractions=field_extractions,
            degraded_fields=degraded_fields,
            dead_letters=dead_letters,
            drift=drift,
        )

    def _any_llm_assisted(self) -> bool:
        return any(spec.llm_assisted for spec in self.recipe.fields.values())

    # -- normalize ----------------------------------------------------------
    def normalize(self, extracted: ExtractedDocument, source_url: str) -> list[CanonicalRecord]:
        """Map an extraction to canonical records (doc 18 §2.4, doc 16 §17.3).

        Emits the skeleton record carrying the recipe's entity ref + extracted
        fields + provenance (degraded flag, degraded field names, dead-letter
        entries). Entity resolution and signal scoring are downstream (doc 14,
        doc 18 §2.4).
        """
        return [
            CanonicalRecord(
                record_type="Signal",
                recipe_id=self.recipe.recipe_id,
                recipe_version=self.recipe.version,
                source_url=source_url,
                entity=self.recipe.entity,
                signal_types=list(extracted.signal_types),
                degraded=extracted.degraded,
                degraded_fields=list(extracted.degraded_fields),
                dead_letters=list(extracted.dead_letters),
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

    def field_values(self, html: str) -> dict[str, str | None]:
        """Per-field extracted values for ``html`` — public, never raises.

        A *non-raising* sibling of :meth:`extract`: it returns every declared
        field's value (``None`` for a miss, including a missing required field)
        rather than rejecting at the boundary. The authoring preview (D5) needs
        the full field-level picture even when a required field is absent, so
        this gives it a supported surface instead of reaching into the
        extraction internals. Uses the same parse + extraction path as
        :meth:`extract`; selecting the matching selector / fallback handling is
        the runner's concern (D11), not the caller's.
        """
        doc = ParsedDocument(_parse_html(html), html)
        return {
            name: _extract_field(doc, name, spec).value
            for name, spec in self.recipe.fields.items()
        }

    def preview_field_extractions(
        self, html: str, *, source_url: str = "preview://pasted-html"
    ) -> list[FieldExtraction]:
        """Per-field :class:`FieldExtraction` results — public, never raises.

        Like :meth:`field_values` but returns the full :class:`FieldExtraction`
        objects (including method, selector_index, and selector) so the authoring
        preview (D5) can build a faithful ``FieldPreview`` list that is consistent
        with the records produced by the full extraction chain (selectors + LLM
        rung). Required-field misses are captured in the returned
        :class:`FieldExtraction` (value ``None``, method ``dead_letter``) rather
        than raised as :class:`RequiredFieldMissingError`, so the preview always
        shows the full field picture regardless of extraction outcome.
        """
        doc = ParsedDocument(_parse_html(html), html)
        page_text = doc.soup.get_text(" ", strip=True) if self._any_llm_assisted() else ""
        # Build a minimal RawDocument shell so _resolve_field can construct
        # DeadLetterEntry objects (the entries are discarded by the preview, but
        # the method signature requires them).
        raw = RawDocument(
            recipe_id=self.recipe.recipe_id,
            connector=self.recipe.connector,
            url=source_url,
            status_code=200,
            content=html,
            content_hash=_content_hash(html),
            recipe_version=self.recipe.version,
        )
        extractions: list[FieldExtraction] = []
        for name, spec in self.recipe.fields.items():
            result, _dead_letter = self._resolve_field(
                doc=doc, page_text=page_text, name=name, spec=spec, raw=raw
            )
            extractions.append(result)
        return extractions


__all__ = [
    "Clock",
    "FetchFailedError",
    "Fetcher",
    "GatewayFieldExtractor",
    "LLMFieldExtractor",
    "RealClock",
    "Recipe",
    "RecipeError",
    "RecipeNotFoundError",
    "RecipeRunner",
    "RecipeValidationError",
    "RequiredFieldMissingError",
    "RobotsDisallowedError",
    "load_recipe",
    "load_recipe_file",
    "parse_recipe",
    "parse_recipe_yaml",
    "recipe_schema_dir",
    "recipes_dir",
    "validate_recipe_data",
]
