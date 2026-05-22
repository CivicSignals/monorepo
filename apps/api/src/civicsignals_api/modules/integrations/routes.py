"""HTTP endpoints for the integrations module, mounted under ``/api/v1/integrations``.

K1 — the generic outbound-integration framework (doc 08 §3.6):

- ``GET /integrations/connections`` — list the workspace's connections (admin).
- ``POST /integrations/connections`` — create a connection; OAuth providers
  return a ``redirect_url`` to start consent (admin; doc 03 F2.4 "admin manages
  integrations").
- ``DELETE /integrations/connections/{id}`` — disconnect (admin).
- ``GET /integrations/oauth/callback`` — the provider redirects the browser
  here after consent. **Unauthenticated** by bearer: it is authenticated by the
  signed ``state`` (CSRF/replay guard) minted at create time, which binds the
  callback to the connection + workspace it started.
- ``GET /integrations/push-log`` — cursor-paginated push-log (admin).

K2 adds the Salesforce field-mapping surface (admin, same workspace scoping):

- ``GET .../connections/{id}/discover/objects`` — pushable provider objects.
- ``GET .../connections/{id}/discover/fields?object=`` — an object's writable fields.
- ``GET|PUT .../connections/{id}/field-mappings`` — list / upsert a mapping.
- ``DELETE .../connections/{id}/field-mappings/{target_object}`` — delete a mapping.
- ``POST .../connections/{id}/push`` — push a signal/pipeline-item to the provider.

L3 — Outbound webhook subscriber management (doc 08 §3.6 / §1.10):

- ``GET  /integrations/webhooks``             — list subscriptions (admin).
- ``POST /integrations/webhooks``             — create subscription; secret shown ONCE.
- ``GET  /integrations/webhooks/{id}``        — get one subscription (admin).
- ``PATCH /integrations/webhooks/{id}``       — update URL / events / active (admin).
- ``DELETE /integrations/webhooks/{id}``      — delete subscription (admin).
- ``GET  /integrations/webhooks/{id}/deliveries`` — delivery log for one subscription (admin).
- ``POST /integrations/webhooks/{id}/ping``   — test-ping the subscriber URL (admin).

Errors are RFC 7807 ``application/problem+json``. Connections and webhook endpoints are
workspace-scoped (``RequireAdmin`` resolves + role-gates the active workspace). Token
material and HMAC secrets are never returned after create, never logged.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api import events
from civicsignals_api.config import Settings, get_settings
from civicsignals_api.db import get_session
from civicsignals_api.modules.admin import services as admin_services
from civicsignals_api.modules.auth.dependencies import RequireAdmin, RequireMember
from civicsignals_api.problems import ProblemException

from . import services
from .models import Connection, IntegrationProviderKind, PushErrorCode, PushStatus
from .schemas import (
    ConnectionCreate,
    ConnectionCreated,
    ConnectionList,
    ConnectionOut,
    DiscoveredField,
    DiscoveredObject,
    FieldDiscoveryOut,
    FieldMappingList,
    FieldMappingOut,
    FieldMappingUpsert,
    ObjectDiscoveryOut,
    PushFailureOut,
    PushFailurePageOut,
    PushLogOut,
    PushLogPageOut,
    PushOut,
    PushRequestIn,
    SlackChannelListOut,
    SlackChannelOut,
    SlackChannelSelectIn,
    SlackChannelSelectionOut,
    WebhookDeliveryOut,
    WebhookDeliveryPageOut,
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreated,
    WebhookSubscriptionList,
    WebhookSubscriptionOut,
    WebhookSubscriptionUpdate,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _not_found() -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title="Connection not found",
        detail="No such integration connection in this workspace.",
    )


# --- Connections ------------------------------------------------------------


@router.get(
    "/connections",
    response_model=ConnectionList,
    summary="List integration connections for the active workspace (admin only)",
)
async def list_connections(ctx: RequireAdmin, session: SessionDep) -> ConnectionList:
    """List the workspace's integration connections (no token material)."""
    connections = await services.list_connections(session, ctx.workspace_id)
    return ConnectionList(data=[ConnectionOut.from_orm_connection(c) for c in connections])


@router.post(
    "/connections",
    response_model=ConnectionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Connect an integration (OAuth providers return a redirect_url)",
)
async def create_connection(
    body: ConnectionCreate,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> ConnectionCreated:
    """Create a connection; OAuth providers start the consent flow.

    All MVP providers (salesforce/hubspot/slack) are OAuth, so the response
    carries a ``redirect_url`` and the connection is ``pending_oauth`` until the
    callback completes. Returns ``422`` when the provider is not yet wired up
    (K2/K3/L1) or its OAuth client is unconfigured.
    """
    try:
        started = await services.start_oauth(
            session,
            workspace_id=ctx.workspace_id,
            provider=body.provider,
            name=body.name,
            created_by_user_id=ctx.user.id,
            settings=settings,
        )
    except services.ProviderNotRegisteredError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="provider_unavailable",
            title="Provider not available",
            detail=f"The '{body.provider.value}' provider is not available yet.",
        ) from exc
    except services.ProviderNotConfiguredError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="provider_not_configured",
            title="Provider not configured",
            detail=(
                f"The '{body.provider.value}' OAuth client is not configured on this deployment."
            ),
        ) from exc

    # Persist the default targets supplied at create time onto the connection.
    started.connection.default_targets = list(body.default_targets)
    await session.commit()
    await session.refresh(started.connection)

    # B9: record the connect intent as an audit event (best-effort).
    try:
        await events.publish(
            events.INTEGRATION_CONNECTION_CREATED,
            {
                "user_id": str(ctx.user.id),
                "workspace_id": str(ctx.workspace_id),
                "connection_id": str(started.connection.id),
                "provider": body.provider.value,
            },
        )
    except Exception:
        logger.warning("integration_connection_created_event_failed")

    response.headers["Location"] = f"/api/v1/integrations/connections/{started.connection.id}"
    return ConnectionCreated(
        id=started.connection.id,
        provider=started.connection.provider,
        status=started.connection.status,
        redirect_url=started.redirect_url,
    )


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect an integration (admin only)",
)
async def delete_connection(
    connection_id: uuid.UUID, ctx: RequireAdmin, session: SessionDep
) -> Response:
    """Disconnect (hard-delete) a connection. Push-log rows cascade-delete."""
    connection = await services.get_connection(session, ctx.workspace_id, connection_id)
    if connection is None:
        raise _not_found()
    provider = connection.provider.value
    await services.delete_connection(session, connection)
    await session.commit()

    try:
        await events.publish(
            events.INTEGRATION_CONNECTION_DELETED,
            {
                "user_id": str(ctx.user.id),
                "workspace_id": str(ctx.workspace_id),
                "connection_id": str(connection_id),
                "provider": provider,
            },
        )
    except Exception:
        logger.warning("integration_connection_deleted_event_failed")

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- OAuth callback ---------------------------------------------------------
# The provider redirects the browser here after consent. It is NOT bearer-
# authenticated: the signed ``state`` (minted at create) is the authenticator,
# binding the callback to the connection + workspace it started.


@router.get(
    "/oauth/callback",
    summary="OAuth2 authorization-code callback (authenticated by signed state)",
)
async def oauth_callback(
    session: SessionDep,
    settings: SettingsDep,
    state: Annotated[str, Query(description="Signed state minted at connect.")],
    code: Annotated[str | None, Query(description="Authorization code.")] = None,
    error: Annotated[str | None, Query(description="Provider-reported error.")] = None,
) -> RedirectResponse:
    """Complete the OAuth flow: validate state, exchange code, store tokens.

    On success (or a provider-reported consent error) the user is redirected
    back to the web app's integrations settings page. A tampered/expired state
    is ``400`` (RFC 7807) — we never trust an unsigned callback.
    """
    web_settings_url = f"{settings.web_base_url}/settings/integrations"

    if error is not None or code is None:
        # User declined consent or the provider returned an error: bounce back
        # with a flag so the UI can show "connection cancelled / failed".
        return RedirectResponse(
            url=f"{web_settings_url}?integration=error", status_code=status.HTTP_302_FOUND
        )

    try:
        connection = await services.complete_oauth(
            session, code=code, state=state, settings=settings
        )
        await session.commit()
    except services.OAuthStateError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="invalid_oauth_state",
            title="Invalid OAuth state",
            detail="The OAuth state is missing, tampered, or expired.",
        ) from exc
    except services.IntegrationError:
        await session.rollback()
        # Token exchange failed; bounce back so the UI can prompt a retry.
        logger.warning("oauth_callback_exchange_failed")
        return RedirectResponse(
            url=f"{web_settings_url}?integration=error", status_code=status.HTTP_302_FOUND
        )

    # B9: record the successful connect (best-effort). Committed in its own step
    # so an audit failure never rolls back the token write.
    try:
        await admin_services.record_audit_event(
            session,
            action="integration.connection.connected",
            workspace_id=connection.workspace_id,
            actor_user_id=connection.created_by_user_id,
            target_type="integration_connection",
            target_id=str(connection.id),
            metadata={"provider": connection.provider.value},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("oauth_callback_audit_failed")

    return RedirectResponse(
        url=f"{web_settings_url}?integration=connected&connection_id={connection.id}",
        status_code=status.HTTP_302_FOUND,
    )


# --- Object / field discovery (K2 field-mapping UI) -------------------------


async def _require_connection(
    ctx: RequireAdmin, session: AsyncSession, connection_id: uuid.UUID
) -> Connection:
    connection = await services.get_connection(session, ctx.workspace_id, connection_id)
    if connection is None:
        raise _not_found()
    return connection


def _discovery_problem(exc: services.DiscoveryFailedError) -> ProblemException:
    """Map a discovery failure to RFC 7807, branching on the scope-aware code."""
    if exc.code is PushErrorCode.AUTH:
        return ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="reauth_required",
            title="Reconnect required",
            detail="The connection's credentials are invalid; reconnect the integration.",
        )
    if exc.code is PushErrorCode.PERMISSION:
        return ProblemException(
            status=status.HTTP_403_FORBIDDEN,
            code="provider_permission",
            title="Insufficient provider permission",
            detail=exc.message,
        )
    return ProblemException(
        status=status.HTTP_502_BAD_GATEWAY,
        code="provider_error",
        title="Provider request failed",
        detail=exc.message,
    )


@router.get(
    "/connections/{connection_id}/discover/objects",
    response_model=ObjectDiscoveryOut,
    summary="List the provider objects this connection can push to (admin only)",
)
async def discover_objects(
    connection_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> ObjectDiscoveryOut:
    """List pushable provider objects (e.g. Salesforce Opportunity + custom) (K2).

    Populates the field-mapping UI's object dropdown. ``422`` if the provider
    has no discovery; ``409`` reauth / ``403`` permission / ``502`` on a provider
    error.
    """
    connection = await _require_connection(ctx, session, connection_id)
    try:
        objects = await services.discover_objects(session, connection, settings=settings)
        await session.commit()
    except services.DiscoveryNotSupportedError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="discovery_unsupported",
            title="Discovery not supported",
            detail="This provider does not support object discovery.",
        ) from exc
    except services.ConnectionNotConnectedError as exc:
        await session.commit()  # persist the needs_reauth transition
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="reauth_required",
            title="Reconnect required",
            detail="The connection has no usable credentials; reconnect the integration.",
        ) from exc
    except services.DiscoveryFailedError as exc:
        await session.rollback()
        raise _discovery_problem(exc) from exc
    return ObjectDiscoveryOut(
        data=[DiscoveredObject(name=o.name, label=o.label, custom=o.custom) for o in objects]
    )


@router.get(
    "/connections/{connection_id}/discover/fields",
    response_model=FieldDiscoveryOut,
    summary="List the writable fields on a provider object (admin only)",
)
async def discover_fields(
    connection_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    object_name: Annotated[str, Query(alias="object", description="Provider object name.")],
) -> FieldDiscoveryOut:
    """List the writable fields on ``object`` for the field-mapping rows (K2)."""
    connection = await _require_connection(ctx, session, connection_id)
    try:
        fields = await services.describe_object(
            session, connection, object_name=object_name, settings=settings
        )
        await session.commit()
    except services.DiscoveryNotSupportedError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="discovery_unsupported",
            title="Discovery not supported",
            detail="This provider does not support field discovery.",
        ) from exc
    except services.ConnectionNotConnectedError as exc:
        await session.commit()
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="reauth_required",
            title="Reconnect required",
            detail="The connection has no usable credentials; reconnect the integration.",
        ) from exc
    except services.DiscoveryFailedError as exc:
        await session.rollback()
        raise _discovery_problem(exc) from exc
    return FieldDiscoveryOut(
        object=object_name,
        data=[
            DiscoveredField(
                name=f.name,
                label=f.label,
                type=f.type,
                required=f.required,
                createable=f.createable,
                updateable=f.updateable,
            )
            for f in fields
        ],
    )


# --- Field mapping CRUD (K2) ------------------------------------------------


@router.get(
    "/connections/{connection_id}/field-mappings",
    response_model=FieldMappingList,
    summary="List a connection's field mappings (admin only)",
)
async def list_field_mappings(
    connection_id: uuid.UUID, ctx: RequireAdmin, session: SessionDep
) -> FieldMappingList:
    """List the connection's saved field mappings (one per target object) (K2)."""
    await _require_connection(ctx, session, connection_id)
    mappings = await services.list_field_mappings(
        session, workspace_id=ctx.workspace_id, connection_id=connection_id
    )
    return FieldMappingList(data=[FieldMappingOut.from_orm_mapping(m) for m in mappings])


@router.put(
    "/connections/{connection_id}/field-mappings",
    response_model=FieldMappingOut,
    summary="Create or update a connection's field mapping (admin only)",
)
async def upsert_field_mapping(
    connection_id: uuid.UUID,
    body: FieldMappingUpsert,
    ctx: RequireAdmin,
    session: SessionDep,
) -> FieldMappingOut:
    """Save the field mapping for one target object (upsert by object) (K2)."""
    await _require_connection(ctx, session, connection_id)
    mapping = await services.upsert_field_mapping(
        session,
        workspace_id=ctx.workspace_id,
        connection_id=connection_id,
        target_object=body.target_object,
        field_map=dict(body.field_map),
        constants=dict(body.constants),
    )
    await session.commit()
    await session.refresh(mapping)
    return FieldMappingOut.from_orm_mapping(mapping)


@router.delete(
    "/connections/{connection_id}/field-mappings/{target_object}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a connection's field mapping for a target object (admin only)",
)
async def delete_field_mapping(
    connection_id: uuid.UUID,
    target_object: str,
    ctx: RequireAdmin,
    session: SessionDep,
) -> Response:
    """Delete the saved field mapping for ``target_object`` (K2)."""
    await _require_connection(ctx, session, connection_id)
    mapping = await services.get_field_mapping(
        session,
        workspace_id=ctx.workspace_id,
        connection_id=connection_id,
        target_object=target_object,
    )
    if mapping is None:
        raise _not_found()
    await services.delete_field_mapping(session, mapping)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Push a signal / pipeline-item to the provider (K2) ---------------------


@router.post(
    "/connections/{connection_id}/push",
    response_model=PushOut,
    summary="Push a signal/pipeline-item to the connection's provider (admin only)",
)
async def push(
    connection_id: uuid.UUID,
    body: PushRequestIn,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> PushOut:
    """Push a mapped signal/pipeline-item to the provider (doc 08 §3.2, doc 04 J3).

    Applies the connection's saved field mapping (or an inline
    ``field_map_override``) to the supplied ``source`` and records the attempt to
    the push-log. The push never raises on a provider failure — the typed error
    lives in the returned push-log row so the K5 recovery UI can branch on it.
    """
    connection = await _require_connection(ctx, session, connection_id)
    log = await services.push_source(
        session,
        connection=connection,
        source=dict(body.source),
        target=body.target,
        field_map_override=dict(body.field_map_override) if body.field_map_override else None,
        signal_id=body.signal_id,
        pipeline_item_id=body.pipeline_item_id,
        idempotency_key=body.idempotency_key,
        settings=settings,
    )
    await session.commit()
    await session.refresh(log)

    try:
        await events.publish(
            events.INTEGRATION_PUSH_RECORDED,
            {
                "workspace_id": str(ctx.workspace_id),
                "connection_id": str(connection_id),
                "push_log_id": str(log.id),
                "status": log.status.value,
            },
        )
    except Exception:
        logger.warning("integration_push_recorded_event_failed")

    return PushOut(push_log=PushLogOut.from_orm_log(log))


# --- Push log ---------------------------------------------------------------


@router.get(
    "/push-log",
    response_model=PushLogPageOut,
    summary="List outbound push-log entries for the active workspace (admin only)",
)
async def list_push_log(
    ctx: RequireAdmin,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
    connection_id: Annotated[
        uuid.UUID | None, Query(description="Filter by connection id.")
    ] = None,
    status_filter: Annotated[
        PushStatus | None, Query(alias="status", description="Filter by push status.")
    ] = None,
) -> PushLogPageOut:
    """Return a cursor-paginated, newest-first page of push-log entries.

    Scoped to the active workspace. Optional ``connection_id`` / ``status``
    filters narrow the result. Token material is never included.
    """
    try:
        page = await services.list_push_log(
            session,
            ctx.workspace_id,
            cursor=cursor,
            limit=limit,
            connection_id=connection_id,
            status=status_filter,
        )
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc

    return PushLogPageOut(
        data=[PushLogOut.from_orm_log(log) for log in page.items],
        next_cursor=page.next_cursor,
    )


# --- Push-failure recovery (K5) ---------------------------------------------
# Inline diagnosis + retry, building on the push-log + the K4 idempotent push.
# These read/write workspace data (not workspace *administration*), so they gate
# on RequireMember rather than RequireAdmin.


@router.get(
    "/push-log/failures",
    response_model=PushFailurePageOut,
    summary="List recent failed pushes with inline diagnosis (member and up)",
)
async def list_push_failures(
    ctx: RequireMember,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
    connection_id: Annotated[
        uuid.UUID | None, Query(description="Filter by connection id.")
    ] = None,
) -> PushFailurePageOut:
    """Return a cursor-paginated, newest-first page of failed/dead-letter pushes (K5).

    The recovery surface only lists rows needing attention (``failed`` +
    ``dead_letter``). Each row carries a derived ``diagnosis`` (human-readable
    cause + recommended action) so the UI can branch its CTA (reconnect vs
    retry). Scoped to the active workspace; optional ``connection_id`` narrows.
    """
    try:
        page = await services.list_failed_pushes(
            session,
            ctx.workspace_id,
            cursor=cursor,
            limit=limit,
            connection_id=connection_id,
        )
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc

    return PushFailurePageOut(
        data=[PushFailureOut.from_orm_log_with_diagnosis(log) for log in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/push-log/{push_log_id}/retry",
    response_model=PushOut,
    summary="Retry a failed push (idempotent — won't duplicate) (member and up)",
)
async def retry_push(
    push_log_id: uuid.UUID,
    ctx: RequireMember,
    session: SessionDep,
    settings: SettingsDep,
) -> PushOut:
    """Re-attempt a failed push, routing through the K4 idempotent path (K5).

    Replays the stored push-log row's payload/target/idempotency-key against the
    connection's provider and appends a fresh push-log row (the original failed
    row is preserved as audit). Because it reuses the K4 idempotency registry, a
    retry of a push that actually succeeded externally routes through the
    provider's update path and does **not** create a duplicate CRM object.

    ``404`` when the push-log entry (or its connection) is not in this workspace,
    or when the entry is not in a failed/dead-letter state. The retry never
    raises on a provider failure — the typed error lives in the returned (new)
    push-log row so the recovery UI can re-diagnose it.
    """
    log = await services.get_push_log(session, ctx.workspace_id, push_log_id)
    if log is None:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Push-log entry not found",
            detail="No such push-log entry in this workspace.",
        )
    if log.status not in services.PUSH_FAILURE_STATUSES:
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="not_retryable",
            title="Push is not in a failed state",
            detail=(
                "Only failed or dead-letter pushes can be retried; this entry is "
                f"'{log.status.value}'."
            ),
        )

    connection = await services.get_connection(session, ctx.workspace_id, log.connection_id)
    if connection is None:
        # The connection was disconnected (push-log rows survive deletion).
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Connection not found",
            detail="The connection for this push was disconnected; reconnect it to retry.",
        )

    retry_log = await services.retry_push_log(
        session,
        connection=connection,
        log=log,
        settings=settings,
    )
    await session.commit()
    await session.refresh(retry_log)

    try:
        await events.publish(
            events.INTEGRATION_PUSH_RECORDED,
            {
                "workspace_id": str(ctx.workspace_id),
                "connection_id": str(connection.id),
                "push_log_id": str(retry_log.id),
                "retry_of": str(log.id),
                "status": retry_log.status.value,
            },
        )
    except Exception:
        logger.warning("integration_push_retry_recorded_event_failed")

    return PushOut(push_log=PushLogOut.from_orm_log(retry_log))


# ---------------------------------------------------------------------------
# L1: Slack channel listing + selection
# ---------------------------------------------------------------------------


def _not_slack_connection() -> ProblemException:
    return ProblemException(
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="not_slack_connection",
        title="Not a Slack connection",
        detail="This connection is not a Slack integration.",
    )


def _slack_list_channels_problem(exc: services.SlackListChannelsError) -> ProblemException:
    """Map a channel-list failure to RFC 7807, branching on the scope-aware code (L1)."""
    if exc.code is PushErrorCode.AUTH:
        return ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="reauth_required",
            title="Reconnect required",
            detail="The Slack connection's credentials are invalid; reconnect the integration.",
        )
    if exc.code is PushErrorCode.PERMISSION:
        return ProblemException(
            status=status.HTTP_403_FORBIDDEN,
            code="provider_permission",
            title="Insufficient Slack permission",
            detail=exc.message,
        )
    return ProblemException(
        status=status.HTTP_502_BAD_GATEWAY,
        code="provider_error",
        title="Slack request failed",
        detail=exc.message,
    )


@router.get(
    "/connections/{connection_id}/slack/channels",
    response_model=SlackChannelListOut,
    summary="List available Slack channels for a connected workspace (admin only, L1)",
)
async def list_slack_channels(
    connection_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> SlackChannelListOut:
    """List the public channels available to the Slack bot for this connection (L1).

    Calls ``conversations.list`` via the mocked-in-tests HTTP client.  Returns
    ``422`` when the connection is not a Slack connection, ``409`` on auth
    failure / ``403`` on permission / ``502`` on a provider error.

    The admin selects one channel from this list and persists it via the
    ``PUT .../slack/channels/select`` endpoint below.
    """
    connection = await _require_connection(ctx, session, connection_id)
    if connection.provider is not IntegrationProviderKind.SLACK:
        raise _not_slack_connection()

    if connection.access_token_encrypted is None:
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="reauth_required",
            title="Reconnect required",
            detail="The Slack connection has no credentials yet; complete OAuth first.",
        )

    try:
        channels = await services.list_slack_channels(session, connection, settings=settings)
        await session.commit()
    except services.ConnectionNotConnectedError as exc:
        await session.commit()
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="reauth_required",
            title="Reconnect required",
            detail="The connection has no usable credentials; reconnect the integration.",
        ) from exc
    except services.SlackListChannelsError as exc:
        await session.rollback()
        raise _slack_list_channels_problem(exc) from exc

    return SlackChannelListOut(
        data=[
            SlackChannelOut(
                id=ch.id,
                name=ch.name,
                is_private=ch.is_private,
                is_member=ch.is_member,
            )
            for ch in channels
        ]
    )


@router.put(
    "/connections/{connection_id}/slack/channels/select",
    response_model=SlackChannelSelectionOut,
    summary="Persist the selected Slack notification channel (admin only, L1)",
)
async def select_slack_channel(
    connection_id: uuid.UUID,
    body: SlackChannelSelectIn,
    ctx: RequireAdmin,
    session: SessionDep,
) -> SlackChannelSelectionOut:
    """Save the admin's Slack channel selection for a connection (L1).

    The channel id + name from the ``list_slack_channels`` response are
    persisted; the display name is stored for the UI (L2 uses the stable id
    for ``chat.postMessage``).  Upserts — re-saving replaces the prior choice.
    Returns ``422`` when the connection is not a Slack connection or ``404``
    when the connection is not found.

    # TODO L2: when the channel is (re)selected, enqueue a Slack message
    #   acknowledging the connection is set up (a "hello world" from the bot).
    """
    connection = await _require_connection(ctx, session, connection_id)
    if connection.provider is not IntegrationProviderKind.SLACK:
        raise _not_slack_connection()

    selection = await services.upsert_slack_channel_selection(
        session,
        workspace_id=ctx.workspace_id,
        connection_id=connection_id,
        channel_id=body.channel_id,
        channel_name=body.channel_name,
    )
    await session.commit()
    await session.refresh(selection)
    return SlackChannelSelectionOut.from_orm_selection(selection)


@router.get(
    "/connections/{connection_id}/slack/channels/selected",
    response_model=SlackChannelSelectionOut,
    summary="Get the currently selected Slack notification channel (admin only, L1)",
)
async def get_selected_slack_channel(
    connection_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
) -> SlackChannelSelectionOut:
    """Return the saved Slack channel selection for a connection (L1).

    Returns ``404`` when the connection has no saved channel selection yet.
    """
    connection = await _require_connection(ctx, session, connection_id)
    if connection.provider is not IntegrationProviderKind.SLACK:
        raise _not_slack_connection()

    selection = await services.get_slack_channel_selection(
        session, workspace_id=ctx.workspace_id, connection_id=connection_id
    )
    if selection is None:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="No channel selected",
            detail="No Slack notification channel has been selected for this connection yet.",
        )
    return SlackChannelSelectionOut.from_orm_selection(selection)


# ---------------------------------------------------------------------------
# L3: Webhook subscriber CRUD + delivery log + test-ping
# ---------------------------------------------------------------------------


def _webhook_not_found() -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title="Webhook subscription not found",
        detail="No such webhook subscription in this workspace.",
    )


def _webhook_event_invalid(detail: str) -> ProblemException:
    return ProblemException(
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation",
        title="Validation failed",
        detail=detail,
    )


@router.get(
    "/webhooks",
    response_model=WebhookSubscriptionList,
    summary="List webhook subscriptions for the active workspace (admin only)",
)
async def list_webhooks(ctx: RequireAdmin, session: SessionDep) -> WebhookSubscriptionList:
    """List webhook subscriptions — secrets are never included (L3)."""
    subs = await services.list_webhook_subscriptions(session, ctx.workspace_id)
    return WebhookSubscriptionList(data=[WebhookSubscriptionOut.from_orm(s) for s in subs])


@router.post(
    "/webhooks",
    response_model=WebhookSubscriptionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook subscription — secret shown ONCE (admin only, L3)",
)
async def create_webhook(
    body: WebhookSubscriptionCreate,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> WebhookSubscriptionCreated:
    """Create a webhook subscription.

    The HMAC secret (``whsec_...``) is returned **once** in the response. It is
    stored encrypted at rest and cannot be recovered via the API. If lost, the
    admin must delete and recreate the subscription.

    Returns ``409`` when a subscription with the same URL already exists in this
    workspace. Returns ``422`` for unknown event types.
    """
    # Validate event types before touching the DB.
    try:
        validated_events = body.validated_events()
    except ValueError as exc:
        raise _webhook_event_invalid(str(exc)) from exc

    sub, plaintext_secret = await services.create_webhook_subscription(
        session,
        workspace_id=ctx.workspace_id,
        url=str(body.url),
        subscribed_events=validated_events,
        description=body.description,
        created_by_user_id=ctx.user.id,
        settings=settings,
    )
    await session.commit()
    await session.refresh(sub)

    try:
        await events.publish(
            events.INTEGRATION_CONNECTION_CREATED,
            {
                "user_id": str(ctx.user.id),
                "workspace_id": str(ctx.workspace_id),
                "subscription_id": str(sub.id),
                "kind": "webhook",
            },
        )
    except Exception:
        logger.warning("webhook_subscription_created_event_failed")

    response.headers["Location"] = f"/api/v1/integrations/webhooks/{sub.id}"
    return WebhookSubscriptionCreated(
        id=sub.id,
        url=sub.url,
        secret=plaintext_secret,
        events=sub.subscribed_events,
        active=sub.active,
        description=sub.description,
        created_at=sub.created_at,
    )


@router.get(
    "/webhooks/{subscription_id}",
    response_model=WebhookSubscriptionOut,
    summary="Get a single webhook subscription (admin only, L3)",
)
async def get_webhook(
    subscription_id: uuid.UUID, ctx: RequireAdmin, session: SessionDep
) -> WebhookSubscriptionOut:
    """Return one webhook subscription (secret never included)."""
    sub = await services.get_webhook_subscription(session, ctx.workspace_id, subscription_id)
    if sub is None:
        raise _webhook_not_found()
    return WebhookSubscriptionOut.from_orm(sub)


@router.patch(
    "/webhooks/{subscription_id}",
    response_model=WebhookSubscriptionOut,
    summary="Update a webhook subscription (admin only, L3)",
)
async def update_webhook(
    subscription_id: uuid.UUID,
    body: WebhookSubscriptionUpdate,
    ctx: RequireAdmin,
    session: SessionDep,
) -> WebhookSubscriptionOut:
    """Partially update a webhook subscription (URL, events, active, description).

    Returns ``404`` if not found in this workspace. Returns ``422`` for unknown
    event types. Secrets cannot be rotated here — delete and recreate.
    """
    sub = await services.get_webhook_subscription(session, ctx.workspace_id, subscription_id)
    if sub is None:
        raise _webhook_not_found()

    # Validate events before touching the DB.
    validated_events: list[str] | None = None
    try:
        validated_events = body.validated_events()
    except ValueError as exc:
        raise _webhook_event_invalid(str(exc)) from exc

    await services.update_webhook_subscription(
        session,
        sub,
        url=str(body.url) if body.url is not None else None,
        subscribed_events=validated_events,
        active=body.active,
        description=body.description,
    )
    await session.commit()
    await session.refresh(sub)
    return WebhookSubscriptionOut.from_orm(sub)


@router.delete(
    "/webhooks/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook subscription (admin only, L3)",
)
async def delete_webhook(
    subscription_id: uuid.UUID, ctx: RequireAdmin, session: SessionDep
) -> Response:
    """Hard-delete a webhook subscription. Delivery rows cascade-delete."""
    sub = await services.get_webhook_subscription(session, ctx.workspace_id, subscription_id)
    if sub is None:
        raise _webhook_not_found()
    await services.delete_webhook_subscription(session, sub)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/webhooks/{subscription_id}/deliveries",
    response_model=WebhookDeliveryPageOut,
    summary="List delivery log for a webhook subscription (admin only, L3)",
)
async def list_webhook_deliveries(
    subscription_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> WebhookDeliveryPageOut:
    """Return cursor-paginated delivery attempts for one webhook subscription.

    Scoped to the active workspace. Newest first.
    """
    # Verify subscription exists in this workspace.
    sub = await services.get_webhook_subscription(session, ctx.workspace_id, subscription_id)
    if sub is None:
        raise _webhook_not_found()

    try:
        page = await services.list_webhook_deliveries(
            session,
            ctx.workspace_id,
            subscription_id=subscription_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc

    return WebhookDeliveryPageOut(
        data=[WebhookDeliveryOut.from_orm(d) for d in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/webhooks/{subscription_id}/ping",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a test ping to the subscriber URL (admin only, L3)",
)
async def ping_webhook(
    subscription_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    """POST a test ``ping`` event to the subscriber URL and return the delivery id.

    Useful for verifying the endpoint is reachable and accepting signed deliveries.
    The ping is recorded in the delivery log like any other delivery attempt.
    """
    sub = await services.get_webhook_subscription(session, ctx.workspace_id, subscription_id)
    if sub is None:
        raise _webhook_not_found()

    deliveries = await services.deliver_event_to_subscribers(
        session,
        event_type="ping",
        payload={"kind": "ping", "subscription_id": str(sub.id)},
        workspace_id=ctx.workspace_id,
        settings=settings,
    )
    await session.commit()

    # deliver_event_to_subscribers filters by active subscriptions that match
    # the event type. For ping we bypass filtering by calling directly.
    # Retry: call directly on the one subscription.
    if not deliveries:
        # The subscription may be inactive; still do the ping.
        from civicsignals_api.modules.integrations.models import (
            WebhookDelivery,
            WebhookDeliveryStatus,
        )

        delivery = WebhookDelivery(
            workspace_id=ctx.workspace_id,
            subscription_id=sub.id,
            event_type="ping",
            request_body={"kind": "ping", "subscription_id": str(sub.id)},
            status=WebhookDeliveryStatus.PENDING,
            attempt_count=0,
        )
        session.add(delivery)
        await session.flush()
        delivery = await services.execute_webhook_delivery(
            session,
            subscription=sub,
            delivery=delivery,
            settings=settings,
        )
        await session.commit()
        deliveries = [delivery]

    d = deliveries[0]
    return {
        "delivery_id": str(d.event_id),
        "status": d.status.value,
        "response_status": d.response_status,
    }
