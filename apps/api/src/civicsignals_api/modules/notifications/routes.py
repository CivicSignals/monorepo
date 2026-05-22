"""HTTP endpoints for the notifications module, mounted under ``/api/v1/notifications``.

H3 exposes the saved-search digest schedule for the calling member:

  GET /notifications/digests/{saved_search_id}  — read the caller's digest config
  PUT /notifications/digests/{saved_search_id}  — set it (off / daily / weekly)

Both are workspace-scoped through ``RequireMember`` (a viewer cannot set a
digest). The digest hangs off a saved search the caller can *see* (own or shared,
the H1 rule); the subscription is always per-recipient, so a member can subscribe
to a search shared by someone else. A search the caller cannot see is ``404``.

# TODO H5: an unauthenticated one-click unsubscribe endpoint (token-signed) lands
#   here so a digest email's footer can flip ``frequency`` to ``off`` without login.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireMember
from civicsignals_api.modules.searches import services as searches_services
from civicsignals_api.problems import ProblemException

from . import services
from .digest import DigestFrequency
from .schemas import DigestSubscriptionOut, DigestSubscriptionUpsert

router = APIRouter(prefix="/notifications", tags=["notifications"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _search_not_found() -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title="Saved search not found",
        detail="No such saved search in this workspace.",
    )


def _no_subscription() -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title="No digest configured",
        detail="You have not configured a digest for this saved search.",
    )


async def _require_visible_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    saved_search_id: uuid.UUID,
) -> None:
    """404 unless the caller can see the saved search (own or shared, H1)."""
    search = await searches_services.get_saved_search(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        search_id=saved_search_id,
    )
    if search is None:
        raise _search_not_found()


@router.get(
    "/digests/{saved_search_id}",
    response_model=DigestSubscriptionOut,
    summary="Get the caller's digest schedule for a saved search",
)
async def get_digest(
    saved_search_id: uuid.UUID,
    ctx: RequireMember,
    session: SessionDep,
) -> DigestSubscriptionOut:
    """Return the caller's digest subscription for one saved search (H3)."""
    await _require_visible_search(
        session,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        saved_search_id=saved_search_id,
    )
    sub = await services.get_digest_subscription(
        session, saved_search_id=saved_search_id, user_id=ctx.user.id
    )
    if sub is None:
        raise _no_subscription()
    return DigestSubscriptionOut.model_validate(sub)


@router.put(
    "/digests/{saved_search_id}",
    response_model=DigestSubscriptionOut,
    summary="Set the caller's digest schedule for a saved search",
)
async def set_digest(
    saved_search_id: uuid.UUID,
    body: DigestSubscriptionUpsert,
    ctx: RequireMember,
    session: SessionDep,
) -> DigestSubscriptionOut:
    """Create or update the caller's digest schedule for a saved search (H3).

    Idempotent per (search, caller). Setting ``frequency`` to ``off`` keeps the
    row (remembering the recipient's last choice) but stops dispatch.
    """
    await _require_visible_search(
        session,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        saved_search_id=saved_search_id,
    )
    sub = await services.upsert_digest_subscription(
        session,
        saved_search_id=saved_search_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        frequency=DigestFrequency(body.frequency),
        send_hour=body.send_hour,
        weekday=body.weekday,
        timezone=body.timezone,
    )
    await session.commit()
    return DigestSubscriptionOut.model_validate(sub)
