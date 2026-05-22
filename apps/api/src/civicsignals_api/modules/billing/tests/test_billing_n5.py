"""Tests for N5: Self-serve plan changes + Customer Portal.

Coverage:
  1.  change_plan — upgrade SOLO → STARTER calls Stripe modify with proration + updates sub.
  2.  change_plan — downgrade STARTER → SOLO calls Stripe modify with proration + updates sub.
  3.  change_plan — invalid target (SELF_HOSTED) raises PlanChangeError.
  4.  change_plan — invalid target (ENTERPRISE) raises PlanChangeError.
  5.  change_plan — no subscription row raises PlanChangeError.
  6.  change_plan — no stripe_subscription_id raises PlanChangeError.
  7.  change_plan — already on the target plan raises PlanChangeError.
  8.  change_plan — no Stripe price id configured raises PlanChangeError.
  9.  create_portal_session — returns URL from Stripe portal session.
  10. create_portal_session — no customer raises PlanChangeError.
  11. POST /billing/change-plan — 200 on valid upgrade (HTTP).
  12. POST /billing/change-plan — 422 on invalid target plan (HTTP).
  13. POST /billing/change-plan — 403 when caller is not admin (HTTP).
  14. POST /billing/portal-session — 200 returns URL (HTTP).
  15. POST /billing/portal-session — 403 when caller is not admin (HTTP).
  16. POST /billing/portal-session — 422 when no Stripe customer (HTTP).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.ids import uuid7
from civicsignals_api.modules.billing import services as billing_services
from civicsignals_api.modules.billing.models import (
    BillingCustomer,
    BillingSubscription,
    SubscriptionPlan,
    SubscriptionStatus,
)
from civicsignals_api.modules.billing.services import PlanChangeError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_stripe_client(
    stripe_customer_id: str = "cus_test_n5",
    subscription_item_id: str = "si_test_n5",
    portal_url: str = "https://billing.stripe.com/session/test_abc",
) -> MagicMock:
    """Build a MagicMock satisfying StripeClient for N5 tests."""
    mock = MagicMock()
    mock.customers.create.return_value = {"id": stripe_customer_id}
    mock.customers.retrieve.return_value = {"id": stripe_customer_id}
    mock.subscriptions.retrieve.return_value = {
        "id": "sub_n5_test",
        "status": "active",
        "items": {"data": [{"id": subscription_item_id, "price": {"id": "price_old"}}]},
    }
    mock.subscriptions.modify.return_value = {
        "id": "sub_n5_test",
        "status": "active",
        "items": {"data": [{"id": subscription_item_id, "price": {"id": "price_starter"}}]},
    }
    mock.billing_portal.sessions.create.return_value = {"url": portal_url}
    return mock


async def _seed_subscription(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    plan: SubscriptionPlan = SubscriptionPlan.SOLO,
    stripe_subscription_id: str = "sub_n5_test",
    stripe_price_id: str = "price_solo",
) -> BillingSubscription:
    """Insert a billing_subscription row for testing."""
    sub = BillingSubscription(
        id=uuid7(),
        workspace_id=workspace_id,
        plan=plan,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id=stripe_subscription_id,
        stripe_price_id=stripe_price_id,
    )
    session.add(sub)
    await session.flush()
    return sub


async def _seed_customer(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    stripe_customer_id: str = "cus_test_n5",
) -> BillingCustomer:
    """Insert a billing_customer row for testing."""
    customer = BillingCustomer(
        id=uuid7(),
        workspace_id=workspace_id,
        stripe_customer_id=stripe_customer_id,
    )
    session.add(customer)
    await session.flush()
    return customer


# ---------------------------------------------------------------------------
# 1. change_plan — upgrade calls Stripe modify + updates sub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_upgrade_calls_stripe_modify(session: AsyncSession) -> None:
    """Upgrading SOLO → STARTER calls subscriptions.modify with proration and updates the sub."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, plan=SubscriptionPlan.SOLO)

    with patch(
        "civicsignals_api.modules.billing.plans.stripe_price_id_for_plan",
        return_value="price_starter",
    ):
        sub = await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.STARTER,
            stripe_client=mock_client,
        )

    mock_client.subscriptions.modify.assert_called_once()
    call_kwargs = mock_client.subscriptions.modify.call_args
    assert call_kwargs.kwargs.get("proration_behavior") == "create_prorations"

    assert sub.plan == SubscriptionPlan.STARTER
    assert sub.stripe_price_id == "price_starter"


# ---------------------------------------------------------------------------
# 2. change_plan — downgrade calls Stripe modify + updates sub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_downgrade_calls_stripe_modify(session: AsyncSession) -> None:
    """Downgrading PRO → SOLO also calls subscriptions.modify with proration."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, plan=SubscriptionPlan.PRO)

    with patch(
        "civicsignals_api.modules.billing.plans.stripe_price_id_for_plan",
        return_value="price_solo",
    ):
        sub = await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.SOLO,
            stripe_client=mock_client,
        )

    mock_client.subscriptions.modify.assert_called_once()
    assert sub.plan == SubscriptionPlan.SOLO
    assert sub.stripe_price_id == "price_solo"


# ---------------------------------------------------------------------------
# 3. change_plan — SELF_HOSTED target raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_rejects_self_hosted(session: AsyncSession) -> None:
    """SELF_HOSTED is not a valid self-serve target; PlanChangeError raised."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, plan=SubscriptionPlan.SOLO)

    with pytest.raises(PlanChangeError) as exc_info:
        await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.SELF_HOSTED,
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "invalid_target_plan"
    mock_client.subscriptions.modify.assert_not_called()


# ---------------------------------------------------------------------------
# 4. change_plan — ENTERPRISE target raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_rejects_enterprise(session: AsyncSession) -> None:
    """ENTERPRISE is quote-driven; PlanChangeError raised."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, plan=SubscriptionPlan.PRO)

    with pytest.raises(PlanChangeError) as exc_info:
        await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.ENTERPRISE,
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "invalid_target_plan"
    mock_client.subscriptions.modify.assert_not_called()


# ---------------------------------------------------------------------------
# 5. change_plan — no subscription raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_no_subscription_raises(session: AsyncSession) -> None:
    """Missing subscription row → PlanChangeError(no_subscription)."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    # No subscription seeded.
    with pytest.raises(PlanChangeError) as exc_info:
        await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.STARTER,
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "no_subscription"


# ---------------------------------------------------------------------------
# 6. change_plan — no stripe_subscription_id raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_no_stripe_sub_id_raises(session: AsyncSession) -> None:
    """Sub row without stripe_subscription_id → PlanChangeError(no_stripe_subscription)."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, stripe_subscription_id="")

    with pytest.raises(PlanChangeError) as exc_info:
        await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.STARTER,
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "no_stripe_subscription"


# ---------------------------------------------------------------------------
# 7. change_plan — already on target plan raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_already_on_plan_raises(session: AsyncSession) -> None:
    """Changing to the current plan → PlanChangeError(already_on_plan)."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, plan=SubscriptionPlan.STARTER)

    with pytest.raises(PlanChangeError) as exc_info:
        await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.STARTER,
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "already_on_plan"
    mock_client.subscriptions.modify.assert_not_called()


# ---------------------------------------------------------------------------
# 8. change_plan — no price id configured raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_plan_no_price_id_raises(session: AsyncSession) -> None:
    """Missing Stripe price id for target plan → PlanChangeError(price_not_configured)."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    await _seed_subscription(session, ws, plan=SubscriptionPlan.SOLO)

    with (
        patch(
            "civicsignals_api.modules.billing.plans.stripe_price_id_for_plan",
            return_value=None,  # price not configured
        ),
        pytest.raises(PlanChangeError) as exc_info,
    ):
        await billing_services.change_plan(
            session,
            ws,
            SubscriptionPlan.STARTER,
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "price_not_configured"
    mock_client.subscriptions.modify.assert_not_called()


# ---------------------------------------------------------------------------
# 9. create_portal_session — returns URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_portal_session_returns_url(session: AsyncSession) -> None:
    """create_portal_session returns the Stripe portal URL."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client(portal_url="https://billing.stripe.com/p/test")

    await _seed_customer(session, ws)

    url = await billing_services.create_portal_session(
        session,
        ws,
        return_url="https://app.example.com/settings/billing",
        stripe_client=mock_client,
    )

    assert url == "https://billing.stripe.com/p/test"
    mock_client.billing_portal.sessions.create.assert_called_once()
    call_kwargs = mock_client.billing_portal.sessions.create.call_args
    assert call_kwargs.kwargs.get("return_url") == "https://app.example.com/settings/billing"
    assert call_kwargs.kwargs.get("customer") == "cus_test_n5"


# ---------------------------------------------------------------------------
# 10. create_portal_session — no customer raises PlanChangeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_portal_session_no_customer_raises(session: AsyncSession) -> None:
    """Missing BillingCustomer row → PlanChangeError(no_stripe_customer)."""
    ws = uuid.uuid4()
    mock_client = _make_mock_stripe_client()

    # No customer seeded.
    with pytest.raises(PlanChangeError) as exc_info:
        await billing_services.create_portal_session(
            session,
            ws,
            return_url="https://app.example.com/settings/billing",
            stripe_client=mock_client,
        )

    assert exc_info.value.code == "no_stripe_customer"
    mock_client.billing_portal.sessions.create.assert_not_called()


# ---------------------------------------------------------------------------
# HTTP endpoint tests (11-16)
# ---------------------------------------------------------------------------


def _build_admin_ctx(ws_id: uuid.UUID) -> Any:
    """Build a fake RequireAdmin WorkspaceContext."""
    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = ws_id
    fake_ws.name = "Test"
    fake_user = MagicMock()
    fake_user.id = uuid.uuid4()
    fake_user.email = "admin@example.com"
    fake_user.name = "Admin"
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.ADMIN
    return WorkspaceContext(workspace=fake_ws, user=fake_user, membership=fake_membership)


def _build_member_ctx(ws_id: uuid.UUID) -> Any:
    """Build a fake member (non-admin) WorkspaceContext."""
    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = ws_id
    fake_ws.name = "Test"
    fake_user = MagicMock()
    fake_user.id = uuid.uuid4()
    fake_user.email = "member@example.com"
    fake_user.name = "Member"
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.MEMBER
    return WorkspaceContext(workspace=fake_ws, user=fake_user, membership=fake_membership)


# 11. POST /billing/change-plan — 200 on valid upgrade


@pytest.mark.asyncio
async def test_change_plan_http_200_on_valid_upgrade(client: Any) -> None:
    """POST /billing/change-plan returns 200 with ChangePlanOut on a valid upgrade."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_admin_ctx(ws_id)
    app.dependency_overrides[require_workspace] = lambda: ctx

    mock_sub = MagicMock(spec=BillingSubscription)
    mock_sub.workspace_id = ws_id
    mock_sub.plan = SubscriptionPlan.STARTER
    mock_sub.status = SubscriptionStatus.ACTIVE
    mock_sub.stripe_subscription_id = "sub_n5_test"
    mock_sub.stripe_price_id = "price_starter"

    try:
        with patch(
            "civicsignals_api.modules.billing.services.change_plan",
            new=AsyncMock(return_value=mock_sub),
        ):
            resp = client.post(
                "/api/v1/billing/change-plan",
                json={"target_plan": "starter"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "starter"
        assert data["workspace_id"] == str(ws_id)
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# 12. POST /billing/change-plan — 422 on invalid target plan


@pytest.mark.asyncio
async def test_change_plan_http_422_invalid_plan(client: Any) -> None:
    """POST /billing/change-plan returns 422 when target plan is invalid."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_admin_ctx(ws_id)
    app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        with patch(
            "civicsignals_api.modules.billing.services.change_plan",
            new=AsyncMock(
                side_effect=PlanChangeError("invalid_target_plan", "Not a self-serve plan.")
            ),
        ):
            resp = client.post(
                "/api/v1/billing/change-plan",
                json={"target_plan": "self_hosted"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["status"] == 422
        assert "invalid_target_plan" in body["type"]
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# 13. POST /billing/change-plan — 403 for non-admin


@pytest.mark.asyncio
async def test_change_plan_http_403_for_member(client: Any) -> None:
    """POST /billing/change-plan returns 403 when caller is not admin."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_member_ctx(ws_id)
    # Override require_workspace but let require_role enforce the 403.
    # We simulate the RBAC guard raising a 403.
    app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        # Patch require_role to raise 403 for non-admin (simulating RBAC).
        with patch(
            "civicsignals_api.modules.auth.dependencies.role_satisfies",
            return_value=False,
        ):
            resp = client.post(
                "/api/v1/billing/change-plan",
                json={"target_plan": "starter"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# 14. POST /billing/portal-session — 200 returns URL


@pytest.mark.asyncio
async def test_portal_session_http_200_returns_url(client: Any) -> None:
    """POST /billing/portal-session returns 200 with a portal URL."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_admin_ctx(ws_id)
    app.dependency_overrides[require_workspace] = lambda: ctx

    portal_url = "https://billing.stripe.com/p/test_session_abc"

    try:
        with patch(
            "civicsignals_api.modules.billing.services.create_portal_session",
            new=AsyncMock(return_value=portal_url),
        ):
            resp = client.post(
                "/api/v1/billing/portal-session",
                json={"return_url": "https://app.example.com/settings/billing"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == portal_url
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# 15. POST /billing/portal-session — 403 for non-admin


@pytest.mark.asyncio
async def test_portal_session_http_403_for_member(client: Any) -> None:
    """POST /billing/portal-session returns 403 when caller is not admin."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_member_ctx(ws_id)
    app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        with patch(
            "civicsignals_api.modules.auth.dependencies.role_satisfies",
            return_value=False,
        ):
            resp = client.post(
                "/api/v1/billing/portal-session",
                json={"return_url": "https://app.example.com/settings/billing"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# 16. POST /billing/portal-session — 422 when no Stripe customer


@pytest.mark.asyncio
async def test_portal_session_http_422_no_customer(client: Any) -> None:
    """POST /billing/portal-session returns 422 when no Stripe customer exists."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_admin_ctx(ws_id)
    app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        with patch(
            "civicsignals_api.modules.billing.services.create_portal_session",
            new=AsyncMock(
                side_effect=PlanChangeError(
                    "no_stripe_customer",
                    "No Stripe customer found for this workspace.",
                )
            ),
        ):
            resp = client.post(
                "/api/v1/billing/portal-session",
                json={"return_url": "https://app.example.com/settings/billing"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["status"] == 422
        assert "no_stripe_customer" in body["type"]
    finally:
        app.dependency_overrides.pop(require_workspace, None)
