"""Recipe authoring preview (doc 18 §3; TODO D5).

Dry-run a recipe against a *sample input* — pasted HTML or a live URL — and
return a rich, per-field view of what the runner extracted, plus the
``degraded`` flag and any diagnostics an author needs to iterate on a recipe.

This is the engine behind both the authoring CLI ``preview`` subcommand and the
staff ``POST /recipes/preview`` endpoint. It is **additive**: it consumes the
runner only through its public lifecycle (``extract_html`` /
``preview_field_extractions`` / ``run``) and never touches the extraction
internals (``_extract_field`` / ``_resolve_field`` / the selector chain) or
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
)
from .schemas import (
    CanonicalRecord,
    FieldExtraction,
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


def _field_previews(recipe: Recipe, extractions: list[FieldExtraction]) -> list[FieldPreview]:
    """Build the per-field diagnostic view from full-chain :class:`FieldExtraction` results.

    ``extractions`` come from :meth:`RecipeRunner.preview_field_extractions`
    (the public non-raising surface that covers the full selector→LLM chain),
    so the per-field picture is consistent with the records produced by the full
    extraction: a field that the LLM rung fills will show ``matched=True`` here
    just as it appears in the canonical record, rather than ``False`` (which
    would happen if we only ran the selector chain). We pair each extraction
    with its :class:`FieldSpec` (which the recipe exposes publicly) to surface
    required-ness, the read attribute, and the ordered selector list an author
    would debug against.
    """
    by_name = {fe.name: fe for fe in extractions}
    previews: list[FieldPreview] = []
    for name, spec in recipe.fields.items():
        fe = by_name.get(name)
        value = fe.value if fe is not None else None
        previews.append(
            FieldPreview(
                name=name,
                value=value,
                matched=value is not None,
                required=spec.required,
                attr=spec.attr,
                selectors=[s.selector for s in spec.selectors],
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

    Per-field results are derived from
    :meth:`RecipeRunner.preview_field_extractions` (full selector→LLM chain,
    never raises) so they are consistent with the records produced by
    ``extract_html`` + ``normalize``.
    """
    runner = RecipeRunner(recipe, _PreviewNoopFetcher())
    # Run the full chain (selector → LLM) for every field without raising on
    # required-field misses; this is the source of truth for FieldPreview.
    field_extractions = runner.preview_field_extractions(html, source_url=source_url)
    fields = _field_previews(recipe, field_extractions)

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
