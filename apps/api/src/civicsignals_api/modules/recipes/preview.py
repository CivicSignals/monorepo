"""Recipe authoring preview (doc 18 §3; TODO D5).

Dry-run a recipe against a *sample input* — pasted HTML or a live URL — and
return a rich, per-field view of what the runner extracted, plus the
``degraded`` flag and any diagnostics an author needs to iterate on a recipe.

This is the engine behind both the authoring CLI ``preview`` subcommand and the
staff ``POST /recipes/preview`` endpoint. It is **additive**: it consumes the
runner only through its public lifecycle (``extract_html`` / ``run``) and never
touches the extraction internals (``_extract_field`` / the selector chain) or
the DSL schema — those belong to D11.

Two input modes:

* **HTML** — paste/upload a snapshot; goes straight through ``extract`` (no
  network, no robots/politeness). This is the inner loop for selector authoring.
* **URL** — a real dry-run fetch through the runner's ``fetch`` step, so the
  recipe's robots.txt + politeness posture (doc 18 §2.2) is honored exactly as
  in production. Requires a :class:`Fetcher`; the CLI/endpoint pass an
  httpx-backed one.
"""

from __future__ import annotations

from collections.abc import Sequence

from .runner import (
    Fetcher,
    Recipe,
    RecipeError,
    RecipeRunner,
    RequiredFieldMissingError,
    _extract_field,
    _parse_html,
)
from .schemas import (
    CanonicalRecord,
    FieldPreview,
    PreviewResult,
)

# A pasted-HTML preview has no real source URL; flag it so downstream provenance
# (and the UI) can tell a snapshot from a live fetch.
HTML_SOURCE_URL = "preview://pasted-html"


class _PreviewNoopFetcher:
    """A :class:`Fetcher` that never touches the network.

    HTML-mode preview only exercises ``extract``/``normalize``, so this just
    satisfies the :class:`RecipeRunner` constructor (mirrors the fixture
    replay's no-op fetcher).
    """

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:  # pragma: no cover - never called
        raise RecipeError("HTML-mode preview must not fetch over the network")

    def robots_txt(
        self, url: str, *, user_agent: str
    ) -> str | None:  # pragma: no cover - never called
        return None


def _field_previews(recipe: Recipe, html: str) -> list[FieldPreview]:
    """Compute a per-field diagnostic view over ``html``.

    We re-evaluate each field's *primary* selector (index 0) the same way the
    runner does — via the runner's own ``_extract_field`` over a shared soup —
    so the preview can attribute a missing value to the selector that produced
    it without duplicating extraction logic. Fallback-selector attribution is
    D11's concern; until then we surface the full ordered selector list so an
    author can see what *would* be tried.
    """
    soup = _parse_html(html)
    previews: list[FieldPreview] = []
    for name, spec in recipe.fields.items():
        value = _extract_field(soup, spec)
        previews.append(
            FieldPreview(
                name=name,
                value=value,
                matched=value is not None,
                required=spec.required,
                attr=spec.attr,
                selectors=list(spec.selectors),
                missing_required=value is None and spec.required,
            )
        )
    return previews


def preview_html(recipe: Recipe, html: str, *, source_url: str = HTML_SOURCE_URL) -> PreviewResult:
    """Dry-run ``recipe`` against a snapshot of ``html`` (no network).

    Returns a :class:`PreviewResult` regardless of whether a required field is
    missing — a failed extraction is the *most* useful thing for an author to
    see, so we capture the :class:`RequiredFieldMissingError` into the result's
    ``error`` rather than raising. ``ok`` is ``False`` in that case.
    """
    fields = _field_previews(recipe, html)
    runner = RecipeRunner(recipe, _PreviewNoopFetcher())

    error: str | None = None
    records: list[CanonicalRecord] = []
    degraded = False
    extraction_method: str | None = None
    try:
        extracted = runner.extract_html(html, source_url=source_url)
        degraded = extracted.degraded
        extraction_method = extracted.extraction_method
        records = runner.normalize(extracted, source_url)
    except RequiredFieldMissingError as exc:
        error = str(exc)

    return PreviewResult(
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        source=source_url,
        ok=error is None,
        degraded=degraded,
        extraction_method=extraction_method,
        signal_types=list(recipe.signal_types),
        fields=fields,
        records=records,
        error=error,
    )


def preview_url(recipe: Recipe, url: str, fetcher: Fetcher) -> PreviewResult:
    """Dry-run ``recipe`` against a live ``url``, honoring robots + politeness.

    Fetches through the runner's ``fetch`` step (so the recipe's robots.txt and
    politeness posture apply, doc 18 §2.2), then extracts from the fetched body.
    Any fetch-time policy error (robots disallow, etc.) propagates to the caller
    — those are recipe-posture issues, not extraction diagnostics.
    """
    runner = RecipeRunner(recipe, fetcher)
    pointer = runner.discover([url])[0]
    raw = runner.fetch(pointer)
    return preview_html(recipe, raw.content, source_url=url)


def preview_records(records: Sequence[CanonicalRecord]) -> list[dict[str, object]]:
    """Project canonical records to plain dicts (for CLI/JSON output)."""
    return [r.model_dump() for r in records]


__all__ = [
    "HTML_SOURCE_URL",
    "preview_html",
    "preview_records",
    "preview_url",
]
