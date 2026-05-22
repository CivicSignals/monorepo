"""HTTP endpoints for the notifications module, mounted under ``/api/v1/notifications``.

H3 exposes the saved-search digest schedule for the calling member:

  GET /notifications/digests/{saved_search_id}  — read the caller's digest config
  PUT /notifications/digests/{saved_search_id}  — set it (off / daily / weekly)

Both are workspace-scoped through ``RequireMember`` (a viewer cannot set a
digest). The digest hangs off a saved search the caller can *see* (own or shared,
the H1 rule); the subscription is always per-recipient, so a member can subscribe
to a search shared by someone else. A search the caller cannot see is ``404``.

H5 adds the unsubscribe + preferences surface:

  GET  /notifications/digests                    — list the caller's subscriptions (authed)
  GET  /notifications/digests/unsubscribe?token= — confirm landing (public, no auth)
  POST /notifications/digests/unsubscribe?token= — one-click unsubscribe (public, RFC 8058)

The unsubscribe endpoints take the signed one-click token minted into each digest
email; they require no login. The **GET** is deliberately non-mutating — it only
validates the token and reports the saved-search name so the web confirm page can
ask "really unsubscribe?"; a mail-client link prefetch or security scanner that
follows the GET therefore cannot silently unsubscribe anyone. Only the **POST**
(RFC 8058 one-click) flips that one subscription to ``off``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireMember
from civicsignals_api.modules.searches import services as searches_services
from civicsignals_api.problems import ProblemException

from . import services
from .digest import DigestFrequency
from .schemas import (
    DigestSubscriptionList,
    DigestSubscriptionListItem,
    DigestSubscriptionOut,
    DigestSubscriptionUpsert,
    UnsubscribeResult,
)
from .unsubscribe import InvalidUnsubscribeToken, verify_unsubscribe_token

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


def _bad_token() -> ProblemException:
    return ProblemException(
        status=status.HTTP_400_BAD_REQUEST,
        code="invalid_token",
        title="Invalid unsubscribe link",
        detail="This unsubscribe link is invalid or has expired.",
    )


def _verify_token(token: str) -> uuid.UUID:
    """Decode a one-click token to a subscription id, or raise ``400`` (H5).

    A bad, tampered, expired, or wrong-scope token is a ``400``. The token alone
    is the authorization — no login, no workspace header.
    """
    try:
        return verify_unsubscribe_token(token)
    except InvalidUnsubscribeToken as exc:
        raise _bad_token() from exc


async def _peek_unsubscribe(session: AsyncSession, token: str) -> UnsubscribeResult:
    """Validate the token and report the digest WITHOUT changing state (GET, H5).

    Non-mutating: the GET confirm landing only checks the token is good and looks
    up the saved-search name so the web page can name the digest before the user
    confirms. A link prefetch / scanner that follows this GET therefore cannot
    silently unsubscribe — the destructive flip lives in the POST. An unknown
    subscription (CASCADE-deleted) is a ``400`` so the endpoint never reveals which
    ids exist. ``unsubscribed=False`` signals "not yet acted on" to the confirm page.
    """
    subscription_id = _verify_token(token)
    name = await services.peek_subscription_name(session, subscription_id=subscription_id)
    if isinstance(name, services.SubscriptionMissing):
        # Valid signature but no such subscription (e.g. the saved search was
        # deleted, cascading the subscription away). Treat as a bad link.
        raise _bad_token()
    # ``name`` is now ``str | None`` (the sentinel is ruled out above).
    return UnsubscribeResult(unsubscribed=False, saved_search_name=name)


async def _do_unsubscribe(session: AsyncSession, token: str) -> UnsubscribeResult:
    """Verify the token and flip the identified subscription to ``off`` (POST, H5).

    The POST one-click path (RFC 8058) — the only side-effecting unsubscribe. An
    unknown subscription (CASCADE-deleted) is a ``400`` so the endpoint never
    reveals which ids exist. The token alone is the authorization.
    """
    subscription_id = _verify_token(token)

    outcome = await services.unsubscribe_by_id(session, subscription_id=subscription_id)
    if not outcome.unsubscribed:
        # Valid signature but no such subscription (e.g. the saved search was
        # deleted, cascading the subscription away). Treat as a bad link.
        raise _bad_token()
    await session.commit()
    return UnsubscribeResult(unsubscribed=True, saved_search_name=outcome.saved_search_name)


@router.get(
    "/digests",
    response_model=DigestSubscriptionList,
    summary="List the caller's digest subscriptions in the active workspace",
)
async def list_digests(
    ctx: RequireMember,
    session: SessionDep,
) -> DigestSubscriptionList:
    """The caller's digest subscriptions (any frequency) in the active workspace (H5).

    Powers the consolidated ``/settings/notifications`` preferences page: one row per
    saved search the member has a subscription for, with the search name and the
    current frequency so they can change or unsubscribe per row.
    """
    rows = await services.list_user_subscriptions(
        session, workspace_id=ctx.workspace_id, user_id=ctx.user.id
    )
    items = [
        DigestSubscriptionListItem(
            **DigestSubscriptionOut.model_validate(row.subscription).model_dump(),
            saved_search_name=row.saved_search_name,
        )
        for row in rows
    ]
    return DigestSubscriptionList(items=items)


@router.get(
    "/digests/unsubscribe",
    response_model=UnsubscribeResult,
    summary="One-click unsubscribe — confirm landing (public, non-mutating)",
)
async def unsubscribe_landing(
    session: SessionDep,
    token: Annotated[str, Query(description="The signed one-click unsubscribe token.")],
) -> UnsubscribeResult:
    """Validate a one-click token and name the digest, WITHOUT changing state (H5).

    Public (no auth): the signed token is the authorization. Deliberately
    non-mutating — the web confirm page calls this to check the link is valid and
    show *which* digest will be turned off; the user then POSTs to actually
    unsubscribe. A link prefetch / scanner following this GET cannot silently
    unsubscribe (the side effect is POST-only, per RFC 8058 one-click).
    """
    return await _peek_unsubscribe(session, token)


@router.post(
    "/digests/unsubscribe",
    response_model=UnsubscribeResult,
    summary="One-click unsubscribe — RFC 8058 List-Unsubscribe-Post (public)",
)
async def unsubscribe_one_click(
    session: SessionDep,
    token: Annotated[str, Query(description="The signed one-click unsubscribe token.")],
) -> UnsubscribeResult:
    """RFC 8058 one-click unsubscribe target for the ``List-Unsubscribe`` header (H5).

    Mail clients POST here (``List-Unsubscribe=One-Click``) with no session; the
    signed token flips exactly that one subscription to ``off``.
    """
    return await _do_unsubscribe(session, token)


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
