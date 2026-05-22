"""In-process event-bus listeners that persist domain events as audit rows (B9).

This module subscribes to the well-known event names defined in
:mod:`civicsignals_api.events` and converts each published payload into an
:class:`~civicsignals_api.modules.admin.models.AuditEvent` row via
:func:`~civicsignals_api.modules.admin.services.record_audit_event`.

**Isolation:** Each listener opens its own short-lived async session and commits
independently so that a DB failure in the audit write never rolls back the
domain transaction that emitted the event. Failures are logged at WARNING level
(not re-raised) so a broken audit sink never fails an authenticated user action.

**Registration:** :func:`register_listeners` is called once at application
startup (from :mod:`civicsignals_api.main`).  Tests that need a live DB can call
it themselves; tests that do not can skip it entirely.
"""

from __future__ import annotations

import contextlib

import structlog

from civicsignals_api import events
from civicsignals_api.db import SessionLocal

from . import services as admin_services

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Generic helper
# ---------------------------------------------------------------------------


async def _persist(
    *,
    action: str,
    payload: dict[str, object],
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """Open a fresh session, write one audit row, and commit.

    ``actor_user_id`` and ``workspace_id`` are extracted from ``payload`` when
    present (both optional — system events may not carry them).
    """
    from uuid import UUID  # local import to avoid heavy top-level circularity

    actor_user_id: UUID | None = None
    raw_uid = payload.get("user_id")
    if raw_uid is not None:
        with contextlib.suppress(ValueError, AttributeError):
            actor_user_id = UUID(str(raw_uid))

    workspace_id: UUID | None = None
    raw_wid = payload.get("workspace_id")
    if raw_wid is not None:
        with contextlib.suppress(ValueError, AttributeError):
            workspace_id = UUID(str(raw_wid))

    # Build metadata from remaining payload keys (strip out keys we already
    # mapped to first-class columns so we don't double-store them).
    _skip = {"user_id", "workspace_id"}
    metadata = {k: v for k, v in payload.items() if k not in _skip}

    try:
        async with SessionLocal() as session:
            await admin_services.record_audit_event(
                session,
                action=action,
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
            )
            await session.commit()
    except Exception:
        logger.warning(
            "audit_persist_failed",
            action=action,
            actor_user_id=str(actor_user_id) if actor_user_id else None,
        )


# ---------------------------------------------------------------------------
# Per-event handlers
# ---------------------------------------------------------------------------


async def _on_password_reset_requested(payload: dict[str, object]) -> None:
    # Closes TODO B9 in auth/routes.py — persist the event B3 emits.
    await _persist(action="auth.password_reset.requested", payload=payload)


async def _on_password_reset_completed(payload: dict[str, object]) -> None:
    await _persist(action="auth.password_reset.completed", payload=payload)


async def _on_login(payload: dict[str, object]) -> None:
    await _persist(action="auth.login", payload=payload)


async def _on_logout(payload: dict[str, object]) -> None:
    await _persist(action="auth.logout", payload=payload)


async def _on_member_invited(payload: dict[str, object]) -> None:
    await _persist(
        action="member.invited",
        payload=payload,
        target_type="user",
        target_id=str(payload.get("invitee_user_id", "")),
    )


async def _on_member_role_changed(payload: dict[str, object]) -> None:
    await _persist(
        action="member.role_changed",
        payload=payload,
        target_type="user",
        target_id=str(payload.get("target_user_id", "")),
    )


async def _on_signal_created(payload: dict[str, object]) -> None:
    await _persist(
        action="signal.created",
        payload=payload,
        target_type="signal",
        target_id=str(payload.get("signal_id", "")),
    )


async def _on_integration_connection_created(payload: dict[str, object]) -> None:
    # K1: an admin started connecting an integration (doc 03 F16.1).
    await _persist(
        action="integration.connection.created",
        payload=payload,
        target_type="integration_connection",
        target_id=str(payload.get("connection_id", "")),
    )


async def _on_integration_connection_deleted(payload: dict[str, object]) -> None:
    await _persist(
        action="integration.connection.deleted",
        payload=payload,
        target_type="integration_connection",
        target_id=str(payload.get("connection_id", "")),
    )


# ---------------------------------------------------------------------------
# Registration (called once at app startup)
# ---------------------------------------------------------------------------


def register_listeners() -> None:
    """Subscribe all admin audit listeners to the in-process event bus.

    Idempotent: calling this twice is safe — :func:`~civicsignals_api.events.subscribe`
    appends handlers, so tests that reset module state should call
    :func:`unregister_listeners` first if isolation is needed.
    """
    events.subscribe(events.AUTH_PASSWORD_RESET_REQUESTED, _on_password_reset_requested)
    events.subscribe(events.AUTH_PASSWORD_RESET_COMPLETED, _on_password_reset_completed)
    events.subscribe(events.AUTH_LOGIN, _on_login)
    events.subscribe(events.AUTH_LOGOUT, _on_logout)
    events.subscribe(events.MEMBER_INVITED, _on_member_invited)
    events.subscribe(events.MEMBER_ROLE_CHANGED, _on_member_role_changed)
    events.subscribe(events.SIGNAL_CREATED, _on_signal_created)
    events.subscribe(events.INTEGRATION_CONNECTION_CREATED, _on_integration_connection_created)
    events.subscribe(events.INTEGRATION_CONNECTION_DELETED, _on_integration_connection_deleted)
    logger.info("admin_audit_listeners_registered")


def unregister_listeners() -> None:
    """Remove all audit listeners (test teardown helper)."""
    events.unsubscribe(events.AUTH_PASSWORD_RESET_REQUESTED, _on_password_reset_requested)
    events.unsubscribe(events.AUTH_PASSWORD_RESET_COMPLETED, _on_password_reset_completed)
    events.unsubscribe(events.AUTH_LOGIN, _on_login)
    events.unsubscribe(events.AUTH_LOGOUT, _on_logout)
    events.unsubscribe(events.MEMBER_INVITED, _on_member_invited)
    events.unsubscribe(events.MEMBER_ROLE_CHANGED, _on_member_role_changed)
    events.unsubscribe(events.SIGNAL_CREATED, _on_signal_created)
    events.unsubscribe(events.INTEGRATION_CONNECTION_CREATED, _on_integration_connection_created)
    events.unsubscribe(events.INTEGRATION_CONNECTION_DELETED, _on_integration_connection_deleted)
