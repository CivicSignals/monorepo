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

FOIA reminder rules (M5)
------------------------
:func:`get_reminder_config` — return the current reminder config for a request.
:func:`update_reminder_config` — patch reminder config (enabled/days/interval/max).
:func:`list_overdue_reminders` — scan for requests that need a nudge right now
  (called by the Celery beat task). Returns each request together with the
  requester's email so the task can send without an extra query.
:func:`mark_reminded` — record that a reminder was just sent (update
  ``last_reminded_at`` + ``reminder_count``). Idempotent within the same day
  (returns False if already sent today, True if the row was updated).

Logic
~~~~~
A request is *due for a reminder* when ALL of the following are true:
1. ``status == 'sent'`` (no ack or response yet).
2. ``reminder_enabled`` is True.
3. ``reminder_count < reminder_max`` (or ``reminder_max == 0`` for unlimited).
4. ``sent_at`` is not None and is older than ``reminder_days`` days (for the
   first reminder) or older than ``last_reminded_at + reminder_interval_days``
   days (for subsequent reminders).
5. ``last_reminded_at`` is None **or** more than ``reminder_interval_days`` days
   ago (prevents double-send on the same day even if the task re-runs).
"""

from __future__ import annotations

import base64
import binascii
import re
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ALLOWED_TRANSITIONS,
    DEFAULT_REMINDER_DAYS,
    DEFAULT_REMINDER_INTERVAL_DAYS,
    DEFAULT_REMINDER_MAX,
    FoiaAttachment,
    FoiaAttachmentExtractionStatus,
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


class FoiaReminderConfigError(ValueError):
    """Raised when reminder configuration values are invalid (M5)."""


# ---------------------------------------------------------------------------
# M5 data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReminderConfig:
    """Current reminder settings for a FOIA request (M5 response shape)."""

    reminder_enabled: bool
    reminder_days: int
    reminder_interval_days: int
    reminder_max: int
    last_reminded_at: datetime | None
    reminder_count: int


@dataclass(frozen=True)
class OverdueReminder:
    """A request that needs a reminder email, together with the requester's email (M5)."""

    request_id: uuid.UUID
    workspace_id: uuid.UUID
    subject: str
    requester_email: str
    sent_at: datetime
    reminder_count: int


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

    # M5: seed reminder_days from the template's statutory deadline when the
    # request is created from a jurisdiction template. Callers can override
    # this later via update_reminder_config.
    initial_reminder_days: int = DEFAULT_REMINDER_DAYS
    if jurisdiction is not None:
        try:
            tmpl = get_template(jurisdiction)
            if tmpl.deadline_days and tmpl.deadline_days > 0:
                initial_reminder_days = tmpl.deadline_days
        except TemplateNotFoundError:
            pass  # already raised above if body came from template render

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
        reminder_days=initial_reminder_days,
        reminder_interval_days=DEFAULT_REMINDER_INTERVAL_DAYS,
        reminder_max=DEFAULT_REMINDER_MAX,
        reminder_enabled=True,
        reminder_count=0,
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


# ---------------------------------------------------------------------------
# M5 — Reminder config get/set + beat-task helpers
# ---------------------------------------------------------------------------


async def get_reminder_config(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> ReminderConfig:
    """Return the current reminder configuration for a FOIA request (M5).

    Raises :exc:`FoiaRequestNotFoundError` if the request is not found in the
    workspace.
    """
    req = await get_request(session, request_id=request_id, workspace_id=workspace_id)
    return ReminderConfig(
        reminder_enabled=req.reminder_enabled,
        reminder_days=req.reminder_days,
        reminder_interval_days=req.reminder_interval_days,
        reminder_max=req.reminder_max,
        last_reminded_at=req.last_reminded_at,
        reminder_count=req.reminder_count,
    )


async def update_reminder_config(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: uuid.UUID,
    reminder_enabled: bool | None = None,
    reminder_days: int | None = None,
    reminder_interval_days: int | None = None,
    reminder_max: int | None = None,
) -> ReminderConfig:
    """Patch the reminder configuration for a FOIA request (M5).

    All parameters are optional; only supplied non-None values are applied.

    Raises:
        FoiaRequestNotFoundError: if the request is not found.
        FoiaReminderConfigError: if any supplied value is out of range
            (reminder_days < 1, reminder_interval_days < 1, reminder_max < 0).
    """
    if reminder_days is not None and reminder_days < 1:
        raise FoiaReminderConfigError("reminder_days must be >= 1")
    if reminder_interval_days is not None and reminder_interval_days < 1:
        raise FoiaReminderConfigError("reminder_interval_days must be >= 1")
    if reminder_max is not None and reminder_max < 0:
        raise FoiaReminderConfigError("reminder_max must be >= 0 (0 = unlimited)")

    req = await get_request(session, request_id=request_id, workspace_id=workspace_id)

    if reminder_enabled is not None:
        req.reminder_enabled = reminder_enabled
    if reminder_days is not None:
        req.reminder_days = reminder_days
    if reminder_interval_days is not None:
        req.reminder_interval_days = reminder_interval_days
    if reminder_max is not None:
        req.reminder_max = reminder_max

    await session.flush()
    return ReminderConfig(
        reminder_enabled=req.reminder_enabled,
        reminder_days=req.reminder_days,
        reminder_interval_days=req.reminder_interval_days,
        reminder_max=req.reminder_max,
        last_reminded_at=req.last_reminded_at,
        reminder_count=req.reminder_count,
    )


class _ReminderCheckable(Protocol):
    """Structural type accepted by :func:`is_reminder_due` (M5).

    Separating this from :class:`.models.FoiaRequest` lets tests pass a plain
    ``types.SimpleNamespace`` or ``dataclasses.dataclass`` without constructing
    a live SQLAlchemy-mapped row.
    """

    status: str
    reminder_enabled: bool
    sent_at: datetime | None
    reminder_max: int
    reminder_count: int
    last_reminded_at: datetime | None
    reminder_days: int
    reminder_interval_days: int


def is_reminder_due(req: _ReminderCheckable, *, now: datetime | None = None) -> bool:
    """Return True if *req* needs a reminder email right now.

    Pure function — no I/O.  Used by the beat task and by tests.

    A request is *due* when:
    1. ``status == 'sent'``.
    2. ``reminder_enabled`` is True.
    3. ``reminder_max == 0`` (unlimited) **or** ``reminder_count < reminder_max``.
    4. ``sent_at`` is not None.
    5. For the *first* reminder (``reminder_count == 0``):
       ``now - sent_at >= reminder_days``.
    6. For *subsequent* reminders:
       ``now - last_reminded_at >= reminder_interval_days``.
    7. Idempotency guard: ``last_reminded_at`` is None **or** its calendar
       *date* is before today (prevents double-send if the task re-runs on the
       same calendar day).
    """
    _now = now if now is not None else datetime.now(UTC)

    if req.status != FoiaRequestStatus.SENT.value:
        return False
    if not req.reminder_enabled:
        return False
    if req.sent_at is None:
        return False
    if req.reminder_max > 0 and req.reminder_count >= req.reminder_max:
        return False

    # Idempotency: don't re-send on the same calendar day.
    if req.last_reminded_at is not None:
        last_date: date = req.last_reminded_at.astimezone(UTC).date()
        today: date = _now.astimezone(UTC).date()
        if last_date >= today:
            return False

    if req.reminder_count == 0:
        # First reminder: check against initial threshold.
        threshold = req.sent_at + timedelta(days=req.reminder_days)
    else:
        # Subsequent reminders: check against last remind time.
        if req.last_reminded_at is None:
            # Defensive: treat as if first.
            threshold = req.sent_at + timedelta(days=req.reminder_days)
        else:
            threshold = req.last_reminded_at + timedelta(days=req.reminder_interval_days)

    return _now >= threshold


async def list_overdue_reminders(session: AsyncSession) -> list[OverdueReminder]:
    """Return all requests across all workspaces that need a reminder now (M5).

    Called by the Celery beat task.  Joins to ``accounts_user`` so the requester's
    email is available without a second query.  Only considers requests in ``sent``
    status with ``reminder_enabled = true``.
    """
    from civicsignals_api.modules.accounts.models import User

    stmt = (
        select(FoiaRequest, User.email)
        .join(User, User.id == FoiaRequest.created_by)
        .where(
            FoiaRequest.status == FoiaRequestStatus.SENT.value,
            FoiaRequest.reminder_enabled.is_(True),
        )
    )
    rows = list((await session.execute(stmt)).all())

    now = datetime.now(UTC)
    result: list[OverdueReminder] = []
    for req, requester_email in rows:
        if is_reminder_due(req, now=now):
            result.append(
                OverdueReminder(
                    request_id=req.id,
                    workspace_id=req.workspace_id,
                    subject=req.subject,
                    requester_email=requester_email,
                    sent_at=req.sent_at,
                    reminder_count=req.reminder_count,
                )
            )
    return result


async def mark_reminded(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Record that a reminder was just sent for *request_id* (M5).

    Updates ``last_reminded_at`` and increments ``reminder_count``.

    Idempotency: if ``last_reminded_at`` is already today's date (UTC), returns
    ``False`` without updating (caller should not re-send).  Returns ``True``
    when the row was updated.

    Note: this function fetches by ``request_id`` only — no workspace scope
    because it is called by the system beat task, not a user request.
    """
    _now = now if now is not None else datetime.now(UTC)

    stmt = select(FoiaRequest).where(FoiaRequest.id == request_id)
    req = (await session.execute(stmt)).scalar_one_or_none()
    if req is None:
        return False

    if req.last_reminded_at is not None:
        last_date = req.last_reminded_at.astimezone(UTC).date()
        today = _now.astimezone(UTC).date()
        if last_date >= today:
            return False

    req.last_reminded_at = _now
    req.reminder_count = req.reminder_count + 1
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# M3 — Attachment upload + extraction (foia_attachment)
# ---------------------------------------------------------------------------

#: Pseudo-recipe id used when a FOIA upload is stored via D3.  FOIA uploads
#: are not driven by a scraped recipe; this slug identifies the source type in
#: the provenance row so extraction / analytics can distinguish them.
FOIA_UPLOAD_RECIPE_ID = "foia_upload"


class FoiaAttachmentNotFoundError(LookupError):
    """Raised when a FOIA attachment does not exist or belongs to another request."""

    def __init__(self, attachment_id: uuid.UUID) -> None:
        self.attachment_id = attachment_id
        super().__init__(f"FOIA attachment {attachment_id} not found.")


async def upload_attachment(
    session: AsyncSession,
    storage: Any,
    *,
    foia_request_id: uuid.UUID,
    workspace_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    filename: str,
    content_type: str,
    content: bytes,
) -> FoiaAttachment:
    """Upload a FOIA response document and trigger extraction (M3).

    Steps:
    1. Validate the FOIA request exists and belongs to the workspace.
    2. Store the bytes via :func:`ingestion.services.store_raw_document` (D3).
       The provenance metadata carries ``source=foia_upload`` and the
       ``foia_request_id`` so extraction and analytics can trace signals back.
    3. Create a :class:`FoiaAttachment` row linking the request and raw document.
    4. Enqueue the extraction pipeline via
       :func:`extraction.services.enqueue_extraction` (E1).
    5. Record the extraction job id on the attachment.
    6. Transition the request to ``response`` status when it is in ``ack`` status
       (uploading a response doc implies a response has arrived).  Transitions from
       other statuses are left for the user to advance manually.

    Raises :exc:`FoiaRequestNotFoundError` if the request is not found.
    """
    from civicsignals_api.modules.extraction import services as extraction_services
    from civicsignals_api.modules.ingestion import services as ingestion_services

    # 1. Validate the request exists and is workspace-scoped.
    req = await get_request(session, request_id=foia_request_id, workspace_id=workspace_id)

    # 2. Store raw bytes via D3.  Provenance metadata links back to the request.
    raw_doc = await ingestion_services.store_raw_document(
        session,
        storage,
        content=content,
        recipe_id=FOIA_UPLOAD_RECIPE_ID,
        connector="manual_upload",
        source_url=f"foia_upload://{foia_request_id}/{filename}",
        content_type=content_type,
        metadata={
            "source": "foia_upload",
            "foia_request_id": str(foia_request_id),
            "filename": filename,
        },
    )

    # 3. Create the attachment row (extraction_status starts as ``pending``).
    attachment = FoiaAttachment(
        foia_request_id=foia_request_id,
        raw_document_id=raw_doc.id,
        filename=filename,
        content_type=content_type,
        uploaded_by=uploaded_by,
        extraction_status=FoiaAttachmentExtractionStatus.PENDING.value,
    )
    session.add(attachment)
    await session.flush()  # give the attachment its PK before enqueuing

    # 4. Enqueue extraction (E1 seam).
    job = await extraction_services.enqueue_extraction(
        session,
        raw_doc.id,
        recipe_id=FOIA_UPLOAD_RECIPE_ID,
    )

    # 5. Record the extraction job id on the attachment.
    attachment.extraction_job_id = job.id
    await session.flush()

    # 6. Auto-transition ack → response when a response document is uploaded.
    import contextlib

    if req.status == FoiaRequestStatus.ACK.value:
        with contextlib.suppress(FoiaIllegalTransitionError):
            await transition_request(
                session,
                request_id=foia_request_id,
                workspace_id=workspace_id,
                actor_id=uploaded_by,
                new_status=FoiaRequestStatus.RESPONSE,
                response_notes=f"Response document uploaded: {filename}",
            )

    return attachment


async def list_attachments(
    session: AsyncSession,
    *,
    foia_request_id: uuid.UUID,
    workspace_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[FoiaAttachment], str | None]:
    """Cursor-paginated list of attachments for a FOIA request (M3).

    Validates workspace access before listing. Ordered by uploaded_at ascending
    (oldest first — attachments arrive in document order).

    Raises :exc:`FoiaRequestNotFoundError` if the request is not found.
    """
    # Ownership check.
    await get_request(session, request_id=foia_request_id, workspace_id=workspace_id)

    limit = max(1, min(limit, MAX_LIMIT))

    stmt = select(FoiaAttachment).where(FoiaAttachment.foia_request_id == foia_request_id)

    if cursor is not None:
        after_id = _decode_cursor(cursor)
        stmt = stmt.where(FoiaAttachment.id > after_id)

    stmt = stmt.order_by(FoiaAttachment.id).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None
    return items, next_cursor


async def get_attachment(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    foia_request_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> FoiaAttachment:
    """Fetch one attachment, verifying it belongs to the workspace/request (M3).

    Raises :exc:`FoiaRequestNotFoundError` if the request is not found.
    Raises :exc:`FoiaAttachmentNotFoundError` if the attachment is not found.
    """
    # Workspace check.
    await get_request(session, request_id=foia_request_id, workspace_id=workspace_id)

    stmt = select(FoiaAttachment).where(
        FoiaAttachment.id == attachment_id,
        FoiaAttachment.foia_request_id == foia_request_id,
    )
    att = (await session.execute(stmt)).scalar_one_or_none()
    if att is None:
        raise FoiaAttachmentNotFoundError(attachment_id)
    return att


async def list_attachment_signals(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    foia_request_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[Any]:
    """Return the signals produced by a FOIA attachment (M3).

    Signals link back to the attachment via ``signals_signal.raw_document_ids``
    which contains the attachment's ``raw_document_id``.  This avoids a
    cross-module FK by querying the JSONB array with ``@>`` containment (the
    ``ingestion_raw_document_content_hash_idx`` + the signals entity index keeps
    this cheap for the typical case of a handful of signals per upload).

    Returns a list of :class:`signals.models.Signal` ORM rows.  The route layer
    converts them to :class:`FoiaAttachmentSignalRef` response shapes.
    """
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    from civicsignals_api.modules.signals.models import Signal

    att = await get_attachment(
        session,
        attachment_id=attachment_id,
        foia_request_id=foia_request_id,
        workspace_id=workspace_id,
    )
    raw_doc_id_str = str(att.raw_document_id)

    # Filter signals whose ``raw_document_ids`` JSONB array contains the
    # attachment's raw_document_id (stored as a UUID string in the array).
    stmt = (
        select(Signal)
        .where(
            Signal.raw_document_ids.cast(JSONB).contains(
                cast([raw_doc_id_str], JSONB)
            )
        )
        .order_by(Signal.observed_at.desc())
        .limit(100)  # cap; pagination not needed for the typical count
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return rows


async def update_attachment_extraction_status(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    new_status: FoiaAttachmentExtractionStatus,
) -> bool:
    """Update the extraction_status on a FoiaAttachment (M3 worker callback).

    Called by the extraction worker (or a future webhook) when the pipeline
    completes or fails.  No workspace check — this is a system-level update.

    Returns True if the row was found and updated, False if not found.
    """
    att = await session.get(FoiaAttachment, attachment_id)
    if att is None:
        return False
    att.extraction_status = new_status.value
    await session.flush()
    return True
