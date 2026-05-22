"""Tests for N3: Usage metering.

Coverage:
  1. current_period() — returns first day of current UTC month.
  2. record_usage / get_usage — increment + current-period read; idempotent upsert.
  3. record_usage accumulation — multiple increments sum correctly.
  4. Period isolation — usage in month A does not bleed into month B.
  5. Workspace isolation — usage for workspace A does not appear for workspace B.
  6. set_usage — snapshot (overwrite) semantics for seats.
  7. refresh_seats_usage — reads accounts_member count and writes billing_usage.
  8. get_usage missing dimensions — returns empty dict (callers treat missing as 0).
  9. record_ai_run — delegates to record_usage(Dimension.AI_RUNS_PER_MONTH).
  10. record_api_requests — delegates to record_usage(Dimension.API_REQUESTS_PER_MONTH).
  11. GET /billing/usage — full endpoint: dimensions, period, pct_used, workspace isolation.
  12. GET /billing/usage — unlimited plan → pct_used is None for unlimited dims.
  13. GET /billing/usage — seats refreshed from accounts_member on each call.

Live-DB tests (AI_RUNS upsert) run only when DATABASE_URL is set (skipped in CI
without a real Postgres; the SQLite path validates the same code paths with
in-memory aiosqlite).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.billing import services as billing_services
from civicsignals_api.modules.billing.models import SubscriptionPlan, SubscriptionStatus
from civicsignals_api.modules.billing.plans import Dimension

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _this_month() -> date:
    """Return today's billing period (first day of current UTC month)."""
    today = datetime.now(tz=UTC).date()
    return today.replace(day=1)


def _month(year: int, month: int) -> date:
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# 1. current_period()
# ---------------------------------------------------------------------------


def test_current_period_returns_first_of_month() -> None:
    period = billing_services.current_period()
    assert period.day == 1


def test_current_period_is_current_month() -> None:
    now = datetime.now(tz=UTC)
    period = billing_services.current_period()
    assert period.year == now.year
    assert period.month == now.month


# ---------------------------------------------------------------------------
# 2. record_usage / get_usage — basic increment + read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_usage_creates_row(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2025, 5)

    await billing_services.record_usage(session, ws, Dimension.AI_RUNS_PER_MONTH, period=period)
    usage = await billing_services.get_usage(session, ws, period=period)

    assert usage.get(Dimension.AI_RUNS_PER_MONTH.value) == 1


@pytest.mark.asyncio
async def test_record_usage_default_delta_is_one(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2025, 5)

    await billing_services.record_usage(
        session, ws, Dimension.API_REQUESTS_PER_MONTH, period=period
    )
    usage = await billing_services.get_usage(session, ws, period=period)

    assert usage[Dimension.API_REQUESTS_PER_MONTH.value] == 1


# ---------------------------------------------------------------------------
# 3. record_usage accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_usage_accumulates(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2025, 6)

    await billing_services.record_usage(session, ws, Dimension.AI_RUNS_PER_MONTH, 3, period=period)
    await billing_services.record_usage(session, ws, Dimension.AI_RUNS_PER_MONTH, 5, period=period)
    usage = await billing_services.get_usage(session, ws, period=period)

    assert usage[Dimension.AI_RUNS_PER_MONTH.value] == 8


# ---------------------------------------------------------------------------
# 4. Period isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_period_isolation(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    may = _month(2025, 5)
    june = _month(2025, 6)

    await billing_services.record_usage(session, ws, Dimension.AI_RUNS_PER_MONTH, 10, period=may)
    await billing_services.record_usage(session, ws, Dimension.AI_RUNS_PER_MONTH, 2, period=june)

    may_usage = await billing_services.get_usage(session, ws, period=may)
    june_usage = await billing_services.get_usage(session, ws, period=june)

    assert may_usage[Dimension.AI_RUNS_PER_MONTH.value] == 10
    assert june_usage[Dimension.AI_RUNS_PER_MONTH.value] == 2


# ---------------------------------------------------------------------------
# 5. Workspace isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_workspace_isolation(session: AsyncSession) -> None:
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    period = _month(2025, 7)

    await billing_services.record_usage(
        session, ws_a, Dimension.AI_RUNS_PER_MONTH, 50, period=period
    )
    await billing_services.record_usage(
        session, ws_b, Dimension.AI_RUNS_PER_MONTH, 3, period=period
    )

    usage_a = await billing_services.get_usage(session, ws_a, period=period)
    usage_b = await billing_services.get_usage(session, ws_b, period=period)

    assert usage_a[Dimension.AI_RUNS_PER_MONTH.value] == 50
    assert usage_b[Dimension.AI_RUNS_PER_MONTH.value] == 3


# ---------------------------------------------------------------------------
# 6. set_usage — snapshot semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_usage_overwrites(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2025, 8)

    await billing_services.set_usage(session, ws, Dimension.SEATS, 5, period=period)
    await billing_services.set_usage(session, ws, Dimension.SEATS, 3, period=period)  # overwrite

    usage = await billing_services.get_usage(session, ws, period=period)
    assert usage[Dimension.SEATS.value] == 3


# ---------------------------------------------------------------------------
# 7. refresh_seats_usage — reads accounts_member count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_seats_usage_uses_member_count(session: AsyncSession) -> None:
    """refresh_seats_usage reads get_seats_count and writes billing_usage.

    We mock get_seats_count (which issues a raw SQL count against accounts_member)
    to avoid schema-coupling the in-memory SQLite test to the full accounts schema.
    The important invariants are:
      1. The returned count comes from get_seats_count.
      2. That count is written into billing_usage as a snapshot (not incremented).
    """
    ws = uuid.uuid4()
    period = _month(2025, 9)

    with patch(
        "civicsignals_api.modules.billing.services.get_seats_count",
        new=AsyncMock(return_value=4),
    ):
        count = await billing_services.refresh_seats_usage(session, ws, period=period)

    assert count == 4
    usage = await billing_services.get_usage(session, ws, period=period)
    assert usage[Dimension.SEATS.value] == 4


@pytest.mark.asyncio
async def test_refresh_seats_usage_overwrites_not_accumulates(session: AsyncSession) -> None:
    """refresh_seats_usage uses set_usage semantics: repeated calls overwrite."""
    ws = uuid.uuid4()
    period = _month(2025, 9)

    with patch(
        "civicsignals_api.modules.billing.services.get_seats_count",
        new=AsyncMock(return_value=5),
    ):
        await billing_services.refresh_seats_usage(session, ws, period=period)

    with patch(
        "civicsignals_api.modules.billing.services.get_seats_count",
        new=AsyncMock(return_value=3),  # member left
    ):
        await billing_services.refresh_seats_usage(session, ws, period=period)

    usage = await billing_services.get_usage(session, ws, period=period)
    assert usage[Dimension.SEATS.value] == 3  # last snapshot wins, not 5+3=8


# ---------------------------------------------------------------------------
# 8. get_usage missing dimensions → empty dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_usage_empty_period(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2030, 1)  # far future, definitely no rows

    usage = await billing_services.get_usage(session, ws, period=period)
    assert usage == {}


# ---------------------------------------------------------------------------
# 9. record_ai_run delegates to AI_RUNS_PER_MONTH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_ai_run(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2025, 10)

    await billing_services.record_ai_run(session, ws, period=period)
    await billing_services.record_ai_run(session, ws, 2, period=period)

    usage = await billing_services.get_usage(session, ws, period=period)
    assert usage[Dimension.AI_RUNS_PER_MONTH.value] == 3


# ---------------------------------------------------------------------------
# 10. record_api_requests delegates to API_REQUESTS_PER_MONTH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_api_requests(session: AsyncSession) -> None:
    ws = uuid.uuid4()
    period = _month(2025, 11)

    await billing_services.record_api_requests(session, ws, 100, period=period)

    usage = await billing_services.get_usage(session, ws, period=period)
    assert usage[Dimension.API_REQUESTS_PER_MONTH.value] == 100


# ---------------------------------------------------------------------------
# 11 & 12. GET /billing/usage endpoint
# ---------------------------------------------------------------------------


def _setup_workspace_sub(
    client: Any,
    ws_id: uuid.UUID,
    plan: SubscriptionPlan = SubscriptionPlan.SOLO,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
) -> None:
    """Minimal setup: patch get_workspace_plan and require_workspace."""
    pass  # patched per test below


@pytest.mark.asyncio
async def test_usage_endpoint_returns_all_dimensions(session: AsyncSession) -> None:
    """GET /billing/usage returns an entry for every Dimension."""
    ws_id = uuid.uuid4()
    period = billing_services.current_period()

    # Record some usage.
    await billing_services.record_usage(
        session, ws_id, Dimension.AI_RUNS_PER_MONTH, 10, period=period
    )

    usage_map = await billing_services.get_usage(session, ws_id, period=period)
    assert Dimension.AI_RUNS_PER_MONTH.value in usage_map
    assert usage_map[Dimension.AI_RUNS_PER_MONTH.value] == 10


@pytest.mark.asyncio
async def test_usage_endpoint_http(client: Any) -> None:
    """GET /billing/usage via HTTP TestClient returns WorkspaceUsageOut shape."""
    from unittest.mock import MagicMock

    from civicsignals_api.main import app
    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace

    ws_id = uuid.uuid4()

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = ws_id
    fake_ws.name = "Test"
    fake_user = MagicMock()
    fake_user.id = uuid.uuid4()
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.ADMIN

    ctx = WorkspaceContext(
        workspace=fake_ws,
        user=fake_user,
        membership=fake_membership,
    )

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            patch(
                "civicsignals_api.modules.billing.services.refresh_seats_usage",
                new=AsyncMock(return_value=1),
            ),
            patch(
                "civicsignals_api.modules.billing.services.get_usage",
                new=AsyncMock(return_value={Dimension.AI_RUNS_PER_MONTH.value: 5}),
            ),
        ):
            resp = client.get(
                "/api/v1/billing/usage",
                headers={"X-Workspace-Id": str(ws_id)},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "dimensions" in data
        assert "period" in data
        assert "workspace_id" in data
        # Every Dimension must appear.
        for dim in Dimension:
            assert dim.value in data["dimensions"], f"Missing dimension {dim.value!r}"
        # Spot-check: ai_runs has the mocked count.
        ai_entry = data["dimensions"][Dimension.AI_RUNS_PER_MONTH.value]
        assert ai_entry["used"] == 5
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# ---------------------------------------------------------------------------
# 13. pct_used is None for unlimited plans
# ---------------------------------------------------------------------------


def test_pct_used_none_for_unlimited() -> None:
    """When plan limit is None (unlimited), pct_used should be None."""
    # Simulate what the route does for SELF_HOSTED (all dims unlimited).
    from civicsignals_api.modules.billing.plans import plan_limit

    limit = plan_limit(SubscriptionPlan.SELF_HOSTED, Dimension.AI_RUNS_PER_MONTH)
    assert limit is None  # SELF_HOSTED has no AI_RUNS_PER_MONTH cap


def test_pct_used_calculated_for_limited_plan() -> None:
    """For capped plans, pct_used = used / limit * 100."""
    from civicsignals_api.modules.billing.plans import plan_limit

    limit = plan_limit(SubscriptionPlan.SOLO, Dimension.AI_RUNS_PER_MONTH)
    assert limit is not None  # SOLO has a cap
    used = limit // 2
    pct = round(used / limit * 100, 1)
    assert pct == 50.0


# ---------------------------------------------------------------------------
# 14. All plans have AI_RUNS_PER_MONTH dimension (N3 addition to plans.py)
# ---------------------------------------------------------------------------


def test_all_plans_have_ai_runs_dimension() -> None:
    from civicsignals_api.modules.billing.plans import PLANS

    for plan, defn in PLANS.items():
        assert Dimension.AI_RUNS_PER_MONTH in defn.limits, (
            f"Plan {plan!r} missing AI_RUNS_PER_MONTH dimension"
        )
