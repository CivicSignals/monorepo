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

Errors are RFC 7807 ``application/problem+json``. Connections are workspace-
scoped (``RequireAdmin`` resolves + role-gates the active workspace). Token
material is never returned and never logged.
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
from civicsignals_api.modules.auth.dependencies import RequireAdmin
from civicsignals_api.problems import ProblemException

from . import services
from .models import Connection, PushErrorCode, PushStatus
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
    PushLogOut,
    PushLogPageOut,
    PushOut,
    PushRequestIn,
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
