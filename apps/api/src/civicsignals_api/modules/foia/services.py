"""Public service interface for the foia module.

Other modules call foia only through the functions defined here — never by
importing foia's models or routes directly (doc 06 §3).

Template library (M1)
---------------------
:func:`list_templates` — all registered templates (global reference data, not
workspace-scoped — templates are shared across every workspace like recipes).
:func:`get_template` — fetch one template by its ``jurisdiction`` code.
:func:`render_template` — substitute placeholders; raises :exc:`MissingPlaceholderError`
on missing required variables. Optional placeholders (``requester_phone``,
``requester_organization``, ``records_officer_name``) default to an empty string
so callers may omit them.

The module-level :data:`_REGISTRY` is populated at import time from the YAML
files in the ``templates/`` sibling directory. Malformed files raise
:exc:`TemplateLoadError` immediately (fail-fast) so startup surfaces config
problems rather than hiding them until a request arrives.

FOIA request CRUD (M2)
-----------------------
:func:`create_request` — create a new FOIA request (optionally from a
template via :func:`render_template`). The target entity is validated against
the global entity directory (:func:`entities.services.get_entity`).
:func:`get_request` — fetch one request by id, workspace-scoped.
:func:`list_requests` — cursor-paginated list, workspace-scoped.
:func:`update_request` — patch a ``draft`` request; raises
:exc:`FoiaDraftOnlyError` for non-draft requests.
:func:`transition_request` — advance the state machine (draft→sent→ack→response);
raises :exc:`FoiaIllegalTransitionError` for disallowed moves.
:func:`list_request_events` — full transition history for a request.

State machine
~~~~~~~~~~~~~
Allowed transitions (enforced here; see :data:`.models.ALLOWED_TRANSITIONS`):

    draft → sent → ack → response    (all terminal after response)

Manual send = marking the request as ``sent`` (no automated email/portal send
at MVP).

# TODO M-assisted-send: v2 will add an ``assisted_send`` function here that
#   calls the integration layer to send the rendered body via email / portal.
#   At that point ``submission_method`` drives routing. This function is the
#   seam — callers today call transition(req, SENT); v2 replaces or wraps this.
"""

from __future__ import annotations

import base64
import binascii
import re
import string
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ALLOWED_TRANSITIONS,
    FoiaRequest,
    FoiaRequestEvent,
    FoiaRequestStatus,
)

# ---------------------------------------------------------------------------
# Constants
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

# Template-owned keys that may appear as ``{key}`` in the body or in
# template-owned field strings (e.g. ``fee_waiver_language``) and are
# substituted automatically by :func:`render_template` from the template's own
# fields, without requiring the caller to supply them in ``context``.
# These keys MUST NOT appear in ``placeholders`` (which contains user-supplied
# context keys only).
_TEMPLATE_OWNED_KEYS: frozenset[str] = frozenset({"fee_waiver_language"})

# Placeholders that are present in every template body but whose values are
# optional: callers may omit them and they will be rendered as empty string.
# MUST be a subset of the ``placeholders`` list in each YAML file.
OPTIONAL_PLACEHOLDERS: frozenset[str] = frozenset(
    {"requester_phone", "requester_organization", "records_officer_name"}
)

# Cursor pagination defaults (doc 06 §5).
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


# ---------------------------------------------------------------------------
# Exceptions — template layer (M1)
# ---------------------------------------------------------------------------


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
# Exceptions — request CRUD (M2)
# ---------------------------------------------------------------------------


class FoiaRequestNotFoundError(LookupError):
    """Raised when a FOIA request does not exist or is not in the caller's workspace."""


class FoiaDraftOnlyError(ValueError):
    """Raised when an edit is attempted on a non-draft FOIA request.

    ``current_status`` carries the actual status for the error message.
    """

    def __init__(self, request_id: uuid.UUID, current_status: str) -> None:
        self.request_id = request_id
        self.current_status = current_status
        super().__init__(
            f"FOIA request {request_id} is in status {current_status!r}; "
            "only 'draft' requests can be edited."
        )


class FoiaIllegalTransitionError(ValueError):
    """Raised when a state-machine transition is not allowed.

    ``from_status`` and ``to_status`` carry the attempted transition for the
    RFC 7807 error payload.
    """

    def __init__(self, request_id: uuid.UUID, from_status: str, to_status: str) -> None:
        self.request_id = request_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"FOIA request {request_id}: transition from {from_status!r} to "
            f"{to_status!r} is not allowed. "
            f"Allowed next statuses: "
            f"{[s.value for s in ALLOWED_TRANSITIONS.get(FoiaRequestStatus(from_status), set())]!r}"
        )


class FoiaEntityNotFoundError(LookupError):
    """Raised when the target entity_id does not exist in the global directory."""

    def __init__(self, entity_id: uuid.UUID) -> None:
        self.entity_id = entity_id
        super().__init__(f"Entity {entity_id} not found in the entity directory.")


# ---------------------------------------------------------------------------
# Data class (template library — kept light, no SQLAlchemy, no Pydantic)
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


def _extract_placeholders(text: str) -> set[str]:
    """Return the set of named placeholder keys found in *text*.

    Uses :mod:`string`'s ``Formatter`` to parse ``{name}`` tokens; ignores
    positional ``{}`` and format specs so ``{date:%Y-%m-%d}`` would yield
    ``"date"``.

    Raises :exc:`TemplateLoadError` on malformed format strings (e.g., unmatched
    ``{``) so errors are surfaced at import time with file context.
    """
    formatter = string.Formatter()
    result: set[str] = set()
    try:
        for _, field_name, _, _ in formatter.parse(text):
            if field_name is not None:
                # strip attribute access / item access (e.g. "obj.attr" -> "obj")
                base = re.split(r"[.\[]", field_name)[0]
                if base:
                    result.add(base)
    except ValueError as exc:
        raise TemplateLoadError(f"Malformed format string: {exc}") from exc
    return result


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
            f"{path.name}: 'deadline_days' must be an integer, "
            f"got {type(raw.get('deadline_days')).__name__}"
        )

    if not isinstance(raw.get("placeholders"), list):
        raise TemplateLoadError(
            f"{path.name}: 'placeholders' must be a list, "
            f"got {type(raw.get('placeholders')).__name__}"
        )

    body: str = str(raw.get("body", ""))
    fee_waiver_text: str = str(raw.get("fee_waiver_language", ""))
    declared: set[str] = {str(p) for p in raw.get("placeholders", [])}

    # Reject template-owned keys if mistakenly listed in ``placeholders``.
    # They are injected automatically and must not be required from callers.
    owned_in_declared = declared & _TEMPLATE_OWNED_KEYS
    if owned_in_declared:
        raise TemplateLoadError(
            f"{path.name}: template-owned keys must not appear in 'placeholders': "
            f"{sorted(owned_in_declared)}. Remove them from the 'placeholders' list."
        )

    # Placeholders allowed in template-owned fields: they must either be in
    # ``placeholders`` (user-supplied) or be other template-owned keys.
    # We do NOT allow new undeclared placeholders inside fee_waiver_language.
    try:
        fwl_placeholders = _extract_placeholders(fee_waiver_text)
    except TemplateLoadError as exc:
        raise TemplateLoadError(f"{path.name} fee_waiver_language: {exc}") from exc

    undeclared_in_fwl = fwl_placeholders - declared - _TEMPLATE_OWNED_KEYS
    if undeclared_in_fwl:
        raise TemplateLoadError(
            f"{path.name}: fee_waiver_language contains undeclared placeholders: "
            f"{sorted(undeclared_in_fwl)}. Add them to the 'placeholders' list."
        )

    # Verify body placeholders: user-supplied OR template-owned keys are allowed.
    try:
        body_placeholders = _extract_placeholders(body)
    except TemplateLoadError as exc:
        raise TemplateLoadError(f"{path.name} body: {exc}") from exc

    undeclared_in_body = body_placeholders - declared - _TEMPLATE_OWNED_KEYS
    if undeclared_in_body:
        raise TemplateLoadError(
            f"{path.name}: body contains undeclared placeholders: {sorted(undeclared_in_body)}. "
            "Add them to the 'placeholders' list or verify they are template-owned keys "
            f"(currently: {sorted(_TEMPLATE_OWNED_KEYS)})."
        )

    # Verify that all declared placeholders are actually used in body or
    # fee_waiver_language (prevents silent dead entries that inflate required set).
    all_used = body_placeholders | (fwl_placeholders - _TEMPLATE_OWNED_KEYS)
    unused_declared = declared - all_used
    if unused_declared:
        raise TemplateLoadError(
            f"{path.name}: 'placeholders' contains keys not used in body or "
            f"fee_waiver_language: {sorted(unused_declared)}. Remove unused entries."
        )

    return FoiaTemplate(raw)


def _build_registry() -> dict[str, FoiaTemplate]:
    """Load all ``*.yaml`` files from the templates directory into a registry.

    Called once at module import. Raises :exc:`TemplateLoadError` on the first
    bad file so startup fails loudly rather than silently skipping templates.
    Also raises if the templates directory is missing or contains no YAML files
    (fail-fast: the API must not start with an empty template library).
    """
    if not TEMPLATES_DIR.is_dir():
        raise TemplateLoadError(
            f"FOIA templates directory not found: {TEMPLATES_DIR}. "
            "Ensure the 'templates/' directory is included in the deployed artifact."
        )

    registry: dict[str, FoiaTemplate] = {}
    yaml_files = sorted(TEMPLATES_DIR.glob("*.yaml"))
    if not yaml_files:
        raise TemplateLoadError(
            f"No YAML template files found in {TEMPLATES_DIR}. "
            "At least one template is required for the FOIA module to operate."
        )

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
# Public API — template library (M1)
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
        MissingPlaceholderError: if any *required* placeholder is absent from
            *context* or has an empty string value. Optional placeholders
            (``requester_phone``, ``requester_organization``,
            ``records_officer_name``) may be omitted or empty — they default
            to an empty string in the rendered output.

    Template-owned fields (``fee_waiver_language``) are substituted from the
    template definition first, then the caller's context is applied. Placeholders
    *within* ``fee_waiver_language`` (e.g. ``{fee_waiver_basis}``) are resolved
    from the caller's context in the same pass.

    The substitution uses :meth:`str.format_map` for simplicity and safety
    (no ``eval``, no shell expansion).
    """
    tmpl = get_template(jurisdiction)

    required = [p for p in tmpl.placeholders if p not in OPTIONAL_PLACEHOLDERS]
    missing = [p for p in required if not context.get(p)]
    if missing:
        raise MissingPlaceholderError(jurisdiction, missing)

    # Fill optional placeholders with empty string when not supplied.
    full_context: dict[str, str] = dict.fromkeys(OPTIONAL_PLACEHOLDERS, "")
    full_context.update(context)

    # Render template-owned fields (e.g. fee_waiver_language) against the
    # full caller context so that placeholders within those fields
    # (e.g. {fee_waiver_basis}, {entity_name}) are resolved in the same pass.
    class _SafeMap(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"  # leave unknown placeholders intact

    rendered_fwl = tmpl.fee_waiver_language.format_map(_SafeMap(full_context))

    # Inject the rendered template-owned field so the body's {fee_waiver_language}
    # token is replaced with the fully-substituted text.
    full_context["fee_waiver_language"] = rendered_fwl

    rendered = tmpl.body.format_map(_SafeMap(full_context))
    return rendered


# ---------------------------------------------------------------------------
# Cursor helpers (M2) — same keyset-on-UUID pattern as entities.services
# ---------------------------------------------------------------------------


def _encode_cursor(request_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(request_id.bytes).decode("ascii")


def _decode_cursor(cursor: str) -> uuid.UUID:
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Public API — FOIA request CRUD + state machine (M2)
# ---------------------------------------------------------------------------


async def create_request(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    entity_id: uuid.UUID,
    subject: str,
    body: str | None = None,
    jurisdiction: str | None = None,
    template_context: dict[str, str] | None = None,
    submission_method: str = "manual",
    submission_target: str | None = None,
) -> FoiaRequest:
    """Create and persist a new FOIA request.

    Body resolution order:
    1. If ``body`` is explicitly supplied, use it as-is.
    2. If ``jurisdiction`` + ``template_context`` are supplied, call
       :func:`render_template` and use the result.
    3. Raise ``ValueError`` if neither produces a body.

    The target ``entity_id`` is validated against the global entity directory
    via :func:`entities.services.get_entity`; raises
    :exc:`FoiaEntityNotFoundError` if not found.

    Raises:
        FoiaEntityNotFoundError: if ``entity_id`` is not in the entity directory.
        TemplateNotFoundError: if ``jurisdiction`` is provided but unknown.
        MissingPlaceholderError: if template rendering fails due to missing
            required placeholder values.
        ValueError: if neither ``body`` nor ``jurisdiction``+``template_context``
            are supplied.
    """
    from civicsignals_api.modules.entities import services as entity_services

    # Validate entity exists in the global directory (C1 requirement).
    entity = await entity_services.get_entity(session, entity_id)
    if entity is None:
        raise FoiaEntityNotFoundError(entity_id)

    # Resolve the body.
    resolved_body: str
    if body is not None:
        resolved_body = body
    elif jurisdiction is not None and template_context is not None:
        resolved_body = render_template(jurisdiction, template_context)
    else:
        raise ValueError(
            "Either 'body' or both 'jurisdiction' and 'template_context' must be supplied."
        )

    req = FoiaRequest(
        workspace_id=workspace_id,
        created_by=created_by,
        entity_id=entity_id,
        jurisdiction=jurisdiction,
        subject=subject,
        body=resolved_body,
        submission_method=submission_method,
        submission_target=submission_target,
        status=FoiaRequestStatus.DRAFT.value,
    )
    session.add(req)
    await session.flush()
    return req


async def get_request(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> FoiaRequest:
    """Return a FOIA request by id, workspace-scoped.

    Raises :exc:`FoiaRequestNotFoundError` if the request does not exist or
    belongs to a different workspace (404-style: workspace existence not leaked
    to non-members).
    """
    stmt = select(FoiaRequest).where(
        FoiaRequest.id == request_id,
        FoiaRequest.workspace_id == workspace_id,
    )
    req = (await session.execute(stmt)).scalar_one_or_none()
    if req is None:
        raise FoiaRequestNotFoundError(
            f"FOIA request {request_id} not found in workspace {workspace_id}."
        )
    return req


async def list_requests(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    status: str | None = None,
    entity_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[FoiaRequest], str | None]:
    """Cursor-paginated list of FOIA requests for a workspace.

    Returns ``(items, next_cursor)`` where ``next_cursor`` is ``None`` on the
    last page. Ordered by id (UUID v7, time-ordered).

    Optional filters:
    - ``status`` — filter to a single status value.
    - ``entity_id`` — filter to a specific target entity.
    """
    limit = max(1, min(limit, MAX_LIMIT))

    stmt = select(FoiaRequest).where(FoiaRequest.workspace_id == workspace_id)

    if status is not None:
        stmt = stmt.where(FoiaRequest.status == status)
    if entity_id is not None:
        stmt = stmt.where(FoiaRequest.entity_id == entity_id)

    if cursor is not None:
        after_id = _decode_cursor(cursor)
        stmt = stmt.where(FoiaRequest.id > after_id)

    stmt = stmt.order_by(FoiaRequest.id).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None
    return items, next_cursor


async def update_request(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: uuid.UUID,
    subject: str | None = None,
    body: str | None = None,
    submission_method: str | None = None,
    submission_target: str | None = None,
) -> FoiaRequest:
    """Patch a FOIA request — only allowed on ``draft`` status.

    Raises :exc:`FoiaRequestNotFoundError` if the request is not found.
    Raises :exc:`FoiaDraftOnlyError` if the request is not in ``draft`` status.
    Only fields with non-``None`` values are updated.
    """
    req = await get_request(session, request_id=request_id, workspace_id=workspace_id)

    if req.status != FoiaRequestStatus.DRAFT.value:
        raise FoiaDraftOnlyError(request_id, req.status)

    if subject is not None:
        req.subject = subject
    if body is not None:
        req.body = body
    if submission_method is not None:
        req.submission_method = submission_method
    if submission_target is not None:
        req.submission_target = submission_target

    await session.flush()
    return req


async def transition_request(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    new_status: FoiaRequestStatus,
    response_notes: str | None = None,
) -> FoiaRequest:
    """Advance the FOIA request state machine to ``new_status``.

    Validates the transition against :data:`.models.ALLOWED_TRANSITIONS`.
    Records a :class:`.models.FoiaRequestEvent` row for the history. Sets the
    appropriate timestamp field (``sent_at`` / ``ack_at`` / ``response_at``).

    ``response_notes`` is stored only on the ``ack → response`` transition;
    it is ignored on other transitions.

    Raises:
        FoiaRequestNotFoundError: if the request is not found.
        FoiaIllegalTransitionError: if the transition is not in the allowed set.

    # TODO M-assisted-send: when the ``sent`` transition is triggered via the
    #   v2 assisted-send path, this function will dispatch to the integration
    #   layer *before* persisting the status change, so a delivery failure rolls
    #   back cleanly.
    """
    req = await get_request(session, request_id=request_id, workspace_id=workspace_id)

    current = FoiaRequestStatus(req.status)
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise FoiaIllegalTransitionError(request_id, req.status, new_status.value)

    now = datetime.now(UTC)

    # Update the transition timestamp.
    if new_status == FoiaRequestStatus.SENT:
        req.sent_at = now
    elif new_status == FoiaRequestStatus.ACK:
        req.ack_at = now
    elif new_status == FoiaRequestStatus.RESPONSE:
        req.response_at = now
        if response_notes is not None:
            req.response_notes = response_notes

    from_status = req.status
    req.status = new_status.value

    # Append the transition event.
    event = FoiaRequestEvent(
        request_id=req.id,
        actor_id=actor_id,
        from_status=from_status,
        to_status=new_status.value,
        occurred_at=now,
    )
    session.add(event)

    await session.flush()
    return req


async def list_request_events(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[FoiaRequestEvent]:
    """Return the full status-transition history for a FOIA request.

    First verifies the request exists and belongs to the workspace (raises
    :exc:`FoiaRequestNotFoundError` otherwise), then returns all events ordered
    by ``occurred_at`` ascending.
    """
    # Ownership check (raises FoiaRequestNotFoundError if not found).
    await get_request(session, request_id=request_id, workspace_id=workspace_id)

    stmt = (
        select(FoiaRequestEvent)
        .where(FoiaRequestEvent.request_id == request_id)
        .order_by(FoiaRequestEvent.occurred_at)
    )
    return list((await session.execute(stmt)).scalars().all())
