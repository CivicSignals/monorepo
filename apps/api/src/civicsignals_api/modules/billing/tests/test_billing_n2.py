"""Tests for N2: Plan definitions & feature flags.

Coverage:
  1. Plan registry — every SubscriptionPlan has a PlanDefinition entry.
  2. plan_allows — correct features per plan.
  3. plan_limit — correct limits per plan, None when unlimited.
  4. get_workspace_plan — active sub → its plan; no sub → SELF_HOSTED;
     cancelled/suspended → SELF_HOSTED; past_due / read_only → plan preserved.
  5. Feature-gate dependency — 402 when plan lacks feature, 200 when it includes it.
  6. GET /billing/plan — returns effective plan object for the workspace.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.billing import services as billing_services
from civicsignals_api.modules.billing.models import SubscriptionPlan, SubscriptionStatus
from civicsignals_api.modules.billing.plans import (
    PLANS,
    Dimension,
    Feature,
    get_plan,
    plan_allows,
    plan_limit,
    stripe_price_id_for_plan,
)

# ---------------------------------------------------------------------------
# 1. Plan registry completeness
# ---------------------------------------------------------------------------


def test_all_subscription_plans_have_definitions() -> None:
    """Every SubscriptionPlan value must have an entry in PLANS."""
    for plan in SubscriptionPlan:
        assert plan in PLANS, f"Missing PlanDefinition for {plan!r}"


def test_plan_registry_display_names_are_nonempty() -> None:
    for plan, defn in PLANS.items():
        assert defn.display_name, f"Empty display_name for {plan!r}"


def test_plan_registry_all_dimensions_present() -> None:
    """Every plan must specify limits for every Dimension."""
    for plan, defn in PLANS.items():
        for dim in Dimension:
            assert dim in defn.limits, f"Missing dimension {dim!r} in plan {plan!r}"


# ---------------------------------------------------------------------------
# 2. plan_allows — spot-check key feature gates
# ---------------------------------------------------------------------------


class TestPlanAllows:
    def test_self_hosted_has_foia(self) -> None:
        assert plan_allows(SubscriptionPlan.SELF_HOSTED, Feature.FOIA)

    def test_self_hosted_has_api_access(self) -> None:
        assert plan_allows(SubscriptionPlan.SELF_HOSTED, Feature.API_ACCESS)

    def test_self_hosted_lacks_managed_scrapers(self) -> None:
        assert not plan_allows(SubscriptionPlan.SELF_HOSTED, Feature.MANAGED_SCRAPERS)

    def test_self_hosted_lacks_crm_integrations(self) -> None:
        assert not plan_allows(SubscriptionPlan.SELF_HOSTED, Feature.CRM_INTEGRATIONS)

    def test_solo_has_managed_scrapers(self) -> None:
        assert plan_allows(SubscriptionPlan.SOLO, Feature.MANAGED_SCRAPERS)

    def test_solo_lacks_contact_enrichment(self) -> None:
        assert not plan_allows(SubscriptionPlan.SOLO, Feature.CONTACT_ENRICHMENT)

    def test_solo_lacks_crm_integrations(self) -> None:
        assert not plan_allows(SubscriptionPlan.SOLO, Feature.CRM_INTEGRATIONS)

    def test_starter_has_contact_enrichment(self) -> None:
        assert plan_allows(SubscriptionPlan.STARTER, Feature.CONTACT_ENRICHMENT)

    def test_starter_has_crm_integrations(self) -> None:
        assert plan_allows(SubscriptionPlan.STARTER, Feature.CRM_INTEGRATIONS)

    def test_starter_lacks_advanced_scoring(self) -> None:
        assert not plan_allows(SubscriptionPlan.STARTER, Feature.ADVANCED_SCORING)

    def test_pro_has_advanced_scoring(self) -> None:
        assert plan_allows(SubscriptionPlan.PRO, Feature.ADVANCED_SCORING)

    def test_pro_has_sso(self) -> None:
        assert plan_allows(SubscriptionPlan.PRO, Feature.SSO)

    def test_pro_lacks_vpc_deploy(self) -> None:
        assert not plan_allows(SubscriptionPlan.PRO, Feature.VPC_DEPLOY)

    def test_enterprise_has_all_features(self) -> None:
        for feature in Feature:
            assert plan_allows(SubscriptionPlan.ENTERPRISE, feature), (
                f"Enterprise should include {feature!r}"
            )

    def test_smart_search_on_all_cloud_plans(self) -> None:
        cloud_plans = [
            SubscriptionPlan.SOLO,
            SubscriptionPlan.STARTER,
            SubscriptionPlan.PRO,
            SubscriptionPlan.ENTERPRISE,
        ]
        for plan in cloud_plans:
            assert plan_allows(plan, Feature.SMART_SEARCH), f"{plan!r} should include SMART_SEARCH"


# ---------------------------------------------------------------------------
# 3. plan_limit — spot-check limits and None for unlimited
# ---------------------------------------------------------------------------


class TestPlanLimit:
    def test_self_hosted_unlimited_seats(self) -> None:
        assert plan_limit(SubscriptionPlan.SELF_HOSTED, Dimension.SEATS) is None

    def test_solo_one_seat(self) -> None:
        assert plan_limit(SubscriptionPlan.SOLO, Dimension.SEATS) == 1

    def test_starter_ten_seats(self) -> None:
        assert plan_limit(SubscriptionPlan.STARTER, Dimension.SEATS) == 10

    def test_pro_thirty_seats(self) -> None:
        assert plan_limit(SubscriptionPlan.PRO, Dimension.SEATS) == 30

    def test_enterprise_unlimited_seats(self) -> None:
        assert plan_limit(SubscriptionPlan.ENTERPRISE, Dimension.SEATS) is None

    def test_solo_tracked_entities_500(self) -> None:
        assert plan_limit(SubscriptionPlan.SOLO, Dimension.TRACKED_ENTITIES) == 500

    def test_starter_tracked_entities_5000(self) -> None:
        assert plan_limit(SubscriptionPlan.STARTER, Dimension.TRACKED_ENTITIES) == 5_000

    def test_pro_tracked_entities_unlimited(self) -> None:
        assert plan_limit(SubscriptionPlan.PRO, Dimension.TRACKED_ENTITIES) is None

    def test_solo_smart_searches_20(self) -> None:
        assert plan_limit(SubscriptionPlan.SOLO, Dimension.SMART_SEARCHES_PER_MONTH) == 20

    def test_starter_smart_searches_100(self) -> None:
        assert plan_limit(SubscriptionPlan.STARTER, Dimension.SMART_SEARCHES_PER_MONTH) == 100

    def test_pro_smart_searches_1000(self) -> None:
        assert plan_limit(SubscriptionPlan.PRO, Dimension.SMART_SEARCHES_PER_MONTH) == 1_000

    def test_enterprise_smart_searches_unlimited(self) -> None:
        assert plan_limit(SubscriptionPlan.ENTERPRISE, Dimension.SMART_SEARCHES_PER_MONTH) is None

    def test_starter_saved_searches_50(self) -> None:
        assert plan_limit(SubscriptionPlan.STARTER, Dimension.SAVED_SEARCHES) == 50

    def test_pro_saved_searches_200(self) -> None:
        assert plan_limit(SubscriptionPlan.PRO, Dimension.SAVED_SEARCHES) == 200


# ---------------------------------------------------------------------------
# 4. get_workspace_plan — resolution logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_workspace_plan_no_subscription_returns_self_hosted(
    session: AsyncSession,
) -> None:
    """No subscription row → defaults to SELF_HOSTED."""
    workspace_id = uuid.uuid4()
    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.SELF_HOSTED


@pytest.mark.asyncio
async def test_get_workspace_plan_active_sub_returns_plan(
    session: AsyncSession,
) -> None:
    """Active subscription → returns the plan on the row."""
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.STARTER,
        status=SubscriptionStatus.ACTIVE,
    )
    await session.flush()

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.STARTER


@pytest.mark.asyncio
async def test_get_workspace_plan_trialing_sub_returns_plan(
    session: AsyncSession,
) -> None:
    """Trialing subscription → plan is still the sub's plan (not SELF_HOSTED)."""
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.PRO,
        status=SubscriptionStatus.TRIALING,
    )
    await session.flush()

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.PRO


@pytest.mark.asyncio
async def test_get_workspace_plan_past_due_preserves_plan(
    session: AsyncSession,
) -> None:
    """Past-due subscription → workspace retains its plan (read access preserved)."""
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.STARTER,
        status=SubscriptionStatus.PAST_DUE,
    )
    await session.flush()

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.STARTER


@pytest.mark.asyncio
async def test_get_workspace_plan_cancelled_returns_self_hosted(
    session: AsyncSession,
) -> None:
    """Cancelled subscription → falls through to SELF_HOSTED."""
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.PRO,
        status=SubscriptionStatus.CANCELLED,
    )
    await session.flush()

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.SELF_HOSTED


@pytest.mark.asyncio
async def test_get_workspace_plan_suspended_returns_self_hosted(
    session: AsyncSession,
) -> None:
    """Suspended subscription → falls through to SELF_HOSTED."""
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.ENTERPRISE,
        status=SubscriptionStatus.SUSPENDED,
    )
    await session.flush()

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.SELF_HOSTED


@pytest.mark.asyncio
async def test_get_workspace_plan_read_only_preserves_plan(
    session: AsyncSession,
) -> None:
    """Read-only subscription → plan preserved (workspace still has data access)."""
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.SOLO,
        status=SubscriptionStatus.READ_ONLY,
    )
    await session.flush()

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    assert plan == SubscriptionPlan.SOLO


# ---------------------------------------------------------------------------
# 5. Feature-gate dependency — HTTP-level 402 / 200 via demo endpoint
# ---------------------------------------------------------------------------

PLAN_URL = "/api/v1/billing/plan"
DEMO_URL = "/api/v1/billing/plan/demo-smart-search"


def _make_mock_workspace(workspace_id: uuid.UUID) -> Any:
    """Return a minimal WorkspaceContext-shaped mock for CurrentWorkspace override."""
    from unittest.mock import MagicMock

    from civicsignals_api.modules.accounts.models import MembershipRole

    mock_ctx = MagicMock()
    mock_ctx.workspace_id = workspace_id
    mock_ctx.user.email = "test@example.com"
    mock_ctx.user.name = "Test User"
    mock_ctx.workspace.name = "Test Workspace"
    mock_ctx.role = MembershipRole.ADMIN
    return mock_ctx


def test_feature_gate_allows_plan_with_smart_search(client: Any) -> None:
    """A workspace on STARTER (which has SMART_SEARCH) gets 200 on the demo endpoint."""
    from civicsignals_api.modules.auth.dependencies import require_workspace

    workspace_id = uuid.uuid4()
    mock_ctx = _make_mock_workspace(workspace_id)

    # Override workspace resolution and the billing plan lookup.
    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.STARTER),
    ):
        from civicsignals_api.main import app

        app.dependency_overrides[require_workspace] = lambda: mock_ctx
        try:
            resp = client.get(DEMO_URL)
        finally:
            app.dependency_overrides.pop(require_workspace, None)

    assert resp.status_code == 200


def test_feature_gate_blocks_plan_without_smart_search(client: Any) -> None:
    """A workspace on SELF_HOSTED (no SMART_SEARCH... wait, SELF_HOSTED has it) — use a plan
    that does NOT have SMART_SEARCH.

    SELF_HOSTED actually includes SMART_SEARCH (BYO LLM). We need to simulate a scenario
    where the feature is absent. We do this by patching plan_allows to return False.
    """
    from civicsignals_api.modules.auth.dependencies import require_workspace

    workspace_id = uuid.uuid4()
    mock_ctx = _make_mock_workspace(workspace_id)

    # Patch plan resolution to return SOLO (which HAS smart_search).
    # To simulate a missing feature, we'll patch plan_allows in the dependency module.
    with (
        patch(
            "civicsignals_api.modules.billing.services.get_workspace_plan",
            new=AsyncMock(return_value=SubscriptionPlan.SOLO),
        ),
        patch(
            "civicsignals_api.modules.billing.dependencies.billing_services.plan_allows",
            return_value=False,
        ),
    ):
        from civicsignals_api.main import app

        app.dependency_overrides[require_workspace] = lambda: mock_ctx
        try:
            resp = client.get(DEMO_URL)
        finally:
            app.dependency_overrides.pop(require_workspace, None)

    assert resp.status_code == 402
    body = resp.json()
    assert body["status"] == 402
    assert body["title"] == "Plan upgrade required"
    # RFC 7807 — content-type must be application/problem+json
    assert "application/problem+json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 6. GET /billing/plan — returns effective plan for workspace
# ---------------------------------------------------------------------------


def test_get_billing_plan_returns_workspace_plan(client: Any) -> None:
    """GET /billing/plan returns effective plan, features, and limits."""
    from civicsignals_api.modules.auth.dependencies import require_workspace

    workspace_id = uuid.uuid4()
    mock_ctx = _make_mock_workspace(workspace_id)

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.STARTER),
    ):
        from civicsignals_api.main import app

        app.dependency_overrides[require_workspace] = lambda: mock_ctx
        try:
            resp = client.get(PLAN_URL)
        finally:
            app.dependency_overrides.pop(require_workspace, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["effective_plan"]["plan"] == "starter"
    assert body["effective_plan"]["display_name"] == "Starter"
    # STARTER should have CRM_INTEGRATIONS
    assert "crm_integrations" in body["effective_plan"]["features"]
    # STARTER should have limits
    limits = body["effective_plan"]["limits"]
    assert limits["seats"] == 10
    assert limits["tracked_entities"] == 5000
    assert limits["smart_searches_per_month"] == 100


def test_get_billing_plan_self_hosted_defaults(client: Any) -> None:
    """GET /billing/plan with no subscription returns SELF_HOSTED with unlimited limits."""
    from civicsignals_api.modules.auth.dependencies import require_workspace

    workspace_id = uuid.uuid4()
    mock_ctx = _make_mock_workspace(workspace_id)

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SELF_HOSTED),
    ):
        from civicsignals_api.main import app

        app.dependency_overrides[require_workspace] = lambda: mock_ctx
        try:
            resp = client.get(PLAN_URL)
        finally:
            app.dependency_overrides.pop(require_workspace, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_plan"]["plan"] == "self_hosted"
    # All limits should be null (unlimited)
    for val in body["effective_plan"]["limits"].values():
        assert val is None, f"Expected None (unlimited) but got {val!r}"


# ---------------------------------------------------------------------------
# 7. stripe_price_id_for_plan — returns None when env vars unset
# ---------------------------------------------------------------------------


def test_stripe_price_id_self_hosted_is_none() -> None:
    assert stripe_price_id_for_plan(SubscriptionPlan.SELF_HOSTED) is None


def test_stripe_price_id_enterprise_is_none() -> None:
    assert stripe_price_id_for_plan(SubscriptionPlan.ENTERPRISE) is None


def test_stripe_price_id_solo_none_when_unconfigured() -> None:
    """stripe_price_id_for_plan returns None when STRIPE_PRICE_ID_SOLO is not set."""
    from civicsignals_api.config import get_settings

    settings = get_settings()
    expected = settings.stripe_price_id_solo  # None in test env
    assert stripe_price_id_for_plan(SubscriptionPlan.SOLO) == expected


# ---------------------------------------------------------------------------
# 8. get_plan helper
# ---------------------------------------------------------------------------


def test_get_plan_returns_correct_definition() -> None:
    defn = get_plan(SubscriptionPlan.PRO)
    assert defn.plan == SubscriptionPlan.PRO
    assert defn.display_name == "Pro"
    assert Feature.ADVANCED_SCORING in defn.features


# ---------------------------------------------------------------------------
# 9. services.plan_allows / plan_limit wrappers match plans module
# ---------------------------------------------------------------------------


def test_services_plan_allows_delegates_to_plans() -> None:
    """billing.services.plan_allows is a thin wrapper around plans.plan_allows."""
    from civicsignals_api.modules.billing import services as svc

    assert svc.plan_allows(SubscriptionPlan.ENTERPRISE, Feature.VPC_DEPLOY) is True
    assert svc.plan_allows(SubscriptionPlan.SOLO, Feature.VPC_DEPLOY) is False


def test_services_plan_limit_delegates_to_plans() -> None:
    """billing.services.plan_limit is a thin wrapper around plans.plan_limit."""
    from civicsignals_api.modules.billing import services as svc

    assert svc.plan_limit(SubscriptionPlan.STARTER, Dimension.SEATS) == 10
    assert svc.plan_limit(SubscriptionPlan.ENTERPRISE, Dimension.SEATS) is None
