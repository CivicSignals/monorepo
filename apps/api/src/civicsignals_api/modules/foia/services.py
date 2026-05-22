"""Public service interface for the foia module.

Other modules call foia only through the functions defined here — never by
importing foia's models or routes directly (doc 06 §3).

Template library (M1)
---------------------
:func:`list_templates` — all registered templates (global reference data, not
workspace-scoped — templates are shared across every workspace like recipes).
:func:`get_template` — fetch one template by its ``jurisdiction`` code.
:func:`render_template` — substitute placeholders; raises :exc:`MissingPlaceholderError`
on missing required variables.

The module-level :data:`_REGISTRY` is populated at import time from the YAML
files in the ``templates/`` sibling directory. Malformed files raise
:exc:`TemplateLoadError` immediately (fail-fast) so startup surfaces config
problems rather than hiding them until a request arrives.

M2 note: FOIA request CRUD will use :func:`get_template` to resolve the
template for a new request and :func:`render_template` to pre-fill the body.
"""

from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Required top-level keys in every template YAML file (README.md schema).
_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "jurisdiction",
        "jurisdiction_name",
        "state",
        "statute",
        "deadline_days",
        "deadline_note",
        "fee_waiver_language",
        "submission_method_hint",
        "status",
        "placeholders",
        "body",
    }
)

# Template-owned keys that may appear as ``{key}`` in the body and are
# substituted automatically by :func:`render_template` from the template's own
# fields, without requiring the caller to supply them in ``context``.
# These keys are NOT listed in ``placeholders`` (which only contains
# user-supplied context keys).
_TEMPLATE_OWNED_KEYS: frozenset[str] = frozenset({"fee_waiver_language"})


class TemplateLoadError(Exception):
    """Raised when a template file fails schema validation at import time."""


class TemplateNotFoundError(KeyError):
    """Raised when a requested jurisdiction code does not exist in the registry."""


class MissingPlaceholderError(ValueError):
    """Raised when :func:`render_template` is called with incomplete context."""

    def __init__(self, jurisdiction: str, missing: list[str]) -> None:
        self.jurisdiction = jurisdiction
        self.missing = missing
        super().__init__(
            f"Template {jurisdiction!r} requires placeholder(s) {missing!r} "
            "that were not supplied in the context."
        )


# ---------------------------------------------------------------------------
# Data class (kept light — no SQLAlchemy, no Pydantic, just a dict-backed view)
# ---------------------------------------------------------------------------


class FoiaTemplate:
    """An in-memory representation of a parsed FOIA template YAML file.

    Attributes correspond 1-to-1 with the YAML schema in README.md. All string
    attributes are stripped of leading/trailing whitespace from the YAML scalars
    so callers receive clean text.

    ``status`` is always ``"draft"`` for now (pending counsel review). M2 may
    extend to ``"reviewed"`` once an attorney sign-off workflow is implemented.
    """

    __slots__ = (
        "body",
        "deadline_days",
        "deadline_note",
        "fee_waiver_language",
        "jurisdiction",
        "jurisdiction_name",
        "placeholders",
        "state",
        "status",
        "statute",
        "submission_method_hint",
    )

    def __init__(self, data: dict[str, Any]) -> None:
        self.jurisdiction: str = data["jurisdiction"]
        self.jurisdiction_name: str = data["jurisdiction_name"]
        self.state: str | None = data.get("state")
        self.statute: str = data["statute"]
        self.deadline_days: int = int(data["deadline_days"])
        self.deadline_note: str = str(data["deadline_note"]).strip()
        self.fee_waiver_language: str = str(data["fee_waiver_language"]).strip()
        self.submission_method_hint: str = str(data["submission_method_hint"]).strip()
        self.status: str = data["status"]
        self.placeholders: list[str] = list(data["placeholders"])
        self.body: str = str(data["body"])

    def as_dict(self) -> dict[str, Any]:
        """Return a plain-dict copy suitable for JSON serialization."""
        return {
            "jurisdiction": self.jurisdiction,
            "jurisdiction_name": self.jurisdiction_name,
            "state": self.state,
            "statute": self.statute,
            "deadline_days": self.deadline_days,
            "deadline_note": self.deadline_note,
            "fee_waiver_language": self.fee_waiver_language,
            "submission_method_hint": self.submission_method_hint,
            "status": self.status,
            "placeholders": self.placeholders,
            "body": self.body,
        }


# ---------------------------------------------------------------------------
# Registry — loaded once at module import; never reloaded at runtime
# ---------------------------------------------------------------------------


def _load_template_file(path: Path) -> FoiaTemplate:
    """Parse and validate a single template YAML file.

    Raises :exc:`TemplateLoadError` if required keys are missing, the status is
    not "draft", or the listed placeholders don't match what's in the body.
    """
    raw: Any
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except Exception as exc:
        raise TemplateLoadError(f"Failed to parse {path.name}: {exc}") from exc

    if not isinstance(raw, dict):
        raise TemplateLoadError(
            f"{path.name}: YAML root must be a mapping, got {type(raw).__name__}"
        )

    missing_keys = _REQUIRED_KEYS - raw.keys()
    if missing_keys:
        raise TemplateLoadError(f"{path.name}: missing required keys: {sorted(missing_keys)}")

    if raw.get("status") != "draft":
        raise TemplateLoadError(
            f"{path.name}: 'status' must be 'draft' (all templates require counsel review); "
            f"got {raw.get('status')!r}"
        )

    if not isinstance(raw.get("deadline_days"), int):
        raise TemplateLoadError(
            f"{path.name}: 'deadline_days' must be an integer, got {type(raw.get('deadline_days')).__name__}"
        )

    if not isinstance(raw.get("placeholders"), list):
        raise TemplateLoadError(
            f"{path.name}: 'placeholders' must be a list, got {type(raw.get('placeholders')).__name__}"
        )

    # Verify declared placeholders match the body using string.Formatter parsing.
    # Template-owned fields (e.g. ``fee_waiver_language``) may also appear in the
    # body — they are substituted automatically by render_template from the
    # template object itself, so they do not need to appear in ``placeholders``.
    body: str = str(raw.get("body", ""))
    declared: set[str] = {str(p) for p in raw.get("placeholders", [])}
    body_placeholders = _extract_placeholders(body)
    undeclared = body_placeholders - declared - _TEMPLATE_OWNED_KEYS
    if undeclared:
        raise TemplateLoadError(
            f"{path.name}: body contains undeclared placeholders: {sorted(undeclared)}. "
            "Add them to the 'placeholders' list or verify they are template-owned keys "
            f"(currently: {sorted(_TEMPLATE_OWNED_KEYS)})."
        )

    return FoiaTemplate(raw)


def _extract_placeholders(text: str) -> set[str]:
    """Return the set of named placeholder keys found in *text*.

    Uses :mod:`string`'s ``Formatter`` to parse ``{name}`` tokens; ignores
    positional ``{}`` and format specs so ``{date:%Y-%m-%d}`` would yield
    ``"date"``.
    """
    formatter = string.Formatter()
    result: set[str] = set()
    for _, field_name, _, _ in formatter.parse(text):
        if field_name is not None:
            # strip attribute access / item access (e.g. "obj.attr" -> "obj")
            base = re.split(r"[.\[]", field_name)[0]
            if base:
                result.add(base)
    return result


def _build_registry() -> dict[str, FoiaTemplate]:
    """Load all ``*.yaml`` files from the templates directory into a registry.

    Called once at module import. Raises :exc:`TemplateLoadError` on the first
    bad file so startup fails loudly rather than silently skipping templates.
    """
    registry: dict[str, FoiaTemplate] = {}
    yaml_files = sorted(TEMPLATES_DIR.glob("*.yaml"))
    for path in yaml_files:
        tmpl = _load_template_file(path)
        if tmpl.jurisdiction in registry:
            raise TemplateLoadError(
                f"Duplicate jurisdiction {tmpl.jurisdiction!r} found in {path.name}"
            )
        registry[tmpl.jurisdiction] = tmpl
    return registry


# Module-level singleton; fails fast on bad templates (see docstring above).
_REGISTRY: dict[str, FoiaTemplate] = _build_registry()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_templates() -> list[FoiaTemplate]:
    """Return all registered FOIA templates, sorted by jurisdiction code.

    Templates are **global reference data** — not workspace-scoped. Any
    authenticated (or public, by design) caller can enumerate them. M2 FOIA
    request CRUD uses this to populate the template picker.
    """
    return sorted(_REGISTRY.values(), key=lambda t: t.jurisdiction)


def get_template(jurisdiction: str) -> FoiaTemplate:
    """Return the template for *jurisdiction*, or raise :exc:`TemplateNotFoundError`.

    ``jurisdiction`` is the short code, e.g. ``"US-FOIA"``, ``"CA-PRA"``.
    Case-sensitive to avoid ambiguity; callers should use the codes returned
    by :func:`list_templates`.
    """
    try:
        return _REGISTRY[jurisdiction]
    except KeyError:
        raise TemplateNotFoundError(jurisdiction) from None


def render_template(jurisdiction: str, context: dict[str, str]) -> str:
    """Render a template by substituting *context* values for ``{placeholder}`` tokens.

    Returns the rendered body string.

    Raises:
        TemplateNotFoundError: if *jurisdiction* is not in the registry.
        MissingPlaceholderError: if any placeholder declared in the template's
            ``placeholders`` list is absent from *context* (or empty string).

    Optional placeholders (``requester_phone``, ``requester_organization``,
    ``records_officer_name``) are omitted from the required set only if the
    template body contains them — the ``placeholders`` list is the source of
    truth. Callers may pass any extra keys in *context*; they are ignored.

    The substitution uses :meth:`str.format_map` for simplicity and safety
    (no ``eval``, no shell expansion).
    """
    tmpl = get_template(jurisdiction)

    missing = [p for p in tmpl.placeholders if not context.get(p)]
    if missing:
        raise MissingPlaceholderError(jurisdiction, missing)

    # Build the full substitution map: user-supplied context merged with
    # template-owned fields (e.g. ``fee_waiver_language``). Template-owned
    # values take the template's value unless the caller overrides them —
    # this allows render to work without the caller knowing which fields
    # are pre-populated from the template definition.
    full_context: dict[str, str] = {
        "fee_waiver_language": tmpl.fee_waiver_language,
    }
    full_context.update(context)

    # Use format_map with a DefaultDict-like fallback so extra keys in context
    # are silently ignored and only declared placeholders are required.
    class _SafeMap(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"  # leave unknown placeholders intact

    rendered = tmpl.body.format_map(_SafeMap(full_context))
    return rendered
