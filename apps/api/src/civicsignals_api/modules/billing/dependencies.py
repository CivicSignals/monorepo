"""FastAPI dependency factories for plan-based feature gating (N2).

These dependencies are the seam N4 (limit enforcement) and feature-gated routes
use. They compose on top of ``require_workspace`` (B5) so:

  1. Authentication + workspace membership are already verified.
  2. The workspace's effective plan is resolved from ``billing_subscription``
     via ``billing.services.get_workspace_plan``.
  3. The requested feature / dimension is checked; if not included, a
     ``402 Payment Required`` RFC 7807 Problem is raised (N2 decision: 402 for
     plan-gated features, 403 for role-gated actions, 429 for quota/rate limits).

Usage in a route::

    from civicsignals_api.modules.billing.dependencies import require_feature
    from civicsignals_api.modules.billing.plans import Feature

    @router.get("/smart-search")
    async def smart_search(
        ctx: RequireMember,
        _: Annotated[None, Depends(require_feature(Feature.SMART_SEARCH))],
        ...
    ) -> ...:
        ...

The dependency returns ``None`` on success so routes ignore the return value.
Leave a ``# TODO N4`` comment on routes that should also enforce metered quotas
once N4 lands (the feature gate is necessary but not sufficient for quota enforcement).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import CurrentWorkspace, WorkspaceContext
from civicsignals_api.modules.billing import services as billing_services
from civicsignals_api.modules.billing.plans import Feature
from civicsignals_api.problems import ProblemException


def _plan_feature_required(feature: Feature) -> ProblemException:
    """RFC 7807 402 raised when a workspace's plan lacks ``feature``."""
    return ProblemException(
        status=402,
        code="plan_feature_required",
        title="Plan upgrade required",
        detail=(
            f"Your current plan does not include '{feature.value}'. "
            "Upgrade at /billing/upgrade to unlock this feature."
        ),
    )


def require_feature(
    feature: Feature,
) -> Callable[[WorkspaceContext, AsyncSession], Awaitable[None]]:
    """Build a FastAPI dependency that enforces a plan feature gate.

    Raises ``402 Payment Required`` (RFC 7807 ``plan_feature_required``) when
    the workspace's effective plan does not include ``feature``.

    The dependency only checks *feature availability* on the plan; quota
    (e.g. "20 smart searches / month") is enforced separately by N4.

    Example::

        require_smart_search = require_feature(Feature.SMART_SEARCH)

        @router.get("/smart-search")
        async def smart_search(
            ctx: RequireMember,
            _gate: Annotated[None, Depends(require_smart_search)],
        ) -> ...: ...
    """

    async def _check(
        ctx: CurrentWorkspace,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        plan = await billing_services.get_workspace_plan(session, ctx.workspace_id)
        if not billing_services.plan_allows(plan, feature):
            raise _plan_feature_required(feature)

    return _check
