"""Tests for N4: Soft + hard limit enforcement.

Coverage:
  1.  enforce_limit — unlimited dimension → always allows.
  2.  enforce_limit — under-limit usage → allows.
  3.  enforce_limit — exactly at limit (100%) → raises 429 ProblemException.
  4.  enforce_limit — over limit → raises 429 ProblemException.
  5.  enforce_limit — 429 body is RFC 7807 with code "limit_exceeded".
  6.  get_limit_states — unlimited plan → all states "ok".
  7.  get_limit_states — usage at 0% → "ok".
  8.  get_limit_states — usage at 80% → "warning".
  9.  get_limit_states — usage at 100% → "exceeded".
  10. GET /billing/limits — HTTP endpoint returns WorkspaceLimitsOut shape.
  11. GET /billing/limits — state "ok" when usage < 80%.
  12. GET /billing/limits — state "warning" when usage ≥ 80% but < 100%.
  13. GET /billing/limits — state "exceeded" when usage ≥ 100%.
  14. Demo smart-search gate — 402 when feature not on plan.
  15. Demo smart-search gate — 429 when quota exhausted.
  16. Demo smart-search gate — 200 when feature + quota both ok.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.billing import services as billing_services
from civicsignals_api.modules.billing.models import SubscriptionPlan
from civicsignals_api.modules.billing.plans import Dimension
from civicsignals_api.problems import ProblemException


def _month(year: int, month: int) -> date:
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# 1. enforce_limit — unlimited dimension allows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_limit_unlimited_allows(session: AsyncSession) -> None:
    """SELF_HOSTED plan has unlimited AI runs — enforce_limit must allow."""
    ws = uuid.uuid4()
    period = _month(2025, 5)

    # SELF_HOSTED has no subscription row → resolves to SELF_HOSTED (unlimited).
    # Record a lot of usage — enforce_limit should still pass.
    await billing_services.record_usage(
        session, ws, Dimension.AI_RUNS_PER_MONTH, 99_999, period=period
    )
    # Should not raise.
    await billing_services.enforce_limit(session, ws, Dimension.AI_RUNS_PER_MONTH, period=period)


# ---------------------------------------------------------------------------
# 2. enforce_limit — under-limit usage allows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_limit_under_limit_allows(session: AsyncSession) -> None:
    """Usage below the cap should not raise."""
    ws = uuid.uuid4()
    period = _month(2025, 5)

    # Use SOLO plan: AI_RUNS_PER_MONTH cap = 500.
    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        await billing_services.record_usage(
            session, ws, Dimension.AI_RUNS_PER_MONTH, 499, period=period
        )
        # 499/500 = 99.8% — just under the hard limit.
        await billing_services.enforce_limit(
            session, ws, Dimension.AI_RUNS_PER_MONTH, period=period
        )


# ---------------------------------------------------------------------------
# 3. enforce_limit — at limit (100%) raises 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_limit_at_limit_raises_429(session: AsyncSession) -> None:
    """Usage exactly at the cap triggers hard enforcement."""
    ws = uuid.uuid4()
    period = _month(2025, 5)

    # SOLO plan: AI_RUNS cap = 500.
    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        await billing_services.record_usage(
            session, ws, Dimension.AI_RUNS_PER_MONTH, 500, period=period
        )
        with pytest.raises(ProblemException) as exc_info:
            await billing_services.enforce_limit(
                session, ws, Dimension.AI_RUNS_PER_MONTH, period=period
            )

    assert exc_info.value.status == 429


# ---------------------------------------------------------------------------
# 4. enforce_limit — over limit raises 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_limit_over_limit_raises_429(session: AsyncSession) -> None:
    """Usage above the cap also triggers hard enforcement."""
    ws = uuid.uuid4()
    period = _month(2025, 6)

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        await billing_services.record_usage(
            session, ws, Dimension.AI_RUNS_PER_MONTH, 600, period=period
        )
        with pytest.raises(ProblemException) as exc_info:
            await billing_services.enforce_limit(
                session, ws, Dimension.AI_RUNS_PER_MONTH, period=period
            )

    assert exc_info.value.status == 429


# ---------------------------------------------------------------------------
# 5. enforce_limit — 429 body is RFC 7807
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_limit_problem_shape(session: AsyncSession) -> None:
    """The raised ProblemException must carry code 'limit_exceeded' and detail."""
    ws = uuid.uuid4()
    period = _month(2025, 7)

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        await billing_services.record_usage(
            session, ws, Dimension.SMART_SEARCHES_PER_MONTH, 20, period=period
        )
        with pytest.raises(ProblemException) as exc_info:
            await billing_services.enforce_limit(
                session, ws, Dimension.SMART_SEARCHES_PER_MONTH, period=period
            )

    exc = exc_info.value
    assert exc.status == 429
    assert exc.code == "limit_exceeded"
    assert exc.detail is not None
    assert "upgrade" in exc.detail.lower()
    assert Dimension.SMART_SEARCHES_PER_MONTH.value in exc.detail


# ---------------------------------------------------------------------------
# 6. get_limit_states — unlimited plan → all "ok"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_limit_states_unlimited_all_ok(session: AsyncSession) -> None:
    """SELF_HOSTED (all unlimited) → every dimension state is 'ok'."""
    ws = uuid.uuid4()
    period = _month(2025, 5)

    # No subscription row → SELF_HOSTED (unlimited).
    states = await billing_services.get_limit_states(session, ws, period=period)

    for dim_key, (_, limit, pct, state) in states.items():
        assert state == "ok", f"Expected 'ok' for {dim_key!r}, got {state!r}"
        assert limit is None, f"Expected None limit for {dim_key!r}"
        assert pct is None, f"Expected None pct for {dim_key!r}"


# ---------------------------------------------------------------------------
# 7. get_limit_states — usage at 0% → "ok"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_limit_states_zero_usage_ok(session: AsyncSession) -> None:
    """No usage yet → ok state for all capped dimensions."""
    ws = uuid.uuid4()
    period = _month(2025, 5)

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        states = await billing_services.get_limit_states(session, ws, period=period)

    for dim_key, (used, _, _pct, state) in states.items():
        assert state == "ok", f"Expected 'ok' for {dim_key!r}, got {state!r}"
        assert used == 0


# ---------------------------------------------------------------------------
# 8. get_limit_states — usage at 80% → "warning"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_limit_states_warning_at_80pct(session: AsyncSession) -> None:
    """Usage at exactly 80% of cap → 'warning' state."""
    ws = uuid.uuid4()
    period = _month(2025, 8)
    # SOLO plan: AI_RUNS_PER_MONTH cap = 500 → 80% = 400.
    limit = 500
    used_80pct = int(limit * 0.8)  # 400

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        await billing_services.record_usage(
            session, ws, Dimension.AI_RUNS_PER_MONTH, used_80pct, period=period
        )
        states = await billing_services.get_limit_states(session, ws, period=period)

    _, _, pct, state = states[Dimension.AI_RUNS_PER_MONTH.value]
    assert state == "warning", f"Expected 'warning' at 80%, got {state!r} (pct={pct})"
    assert pct == 80.0


# ---------------------------------------------------------------------------
# 9. get_limit_states — usage at 100% → "exceeded"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_limit_states_exceeded_at_100pct(session: AsyncSession) -> None:
    """Usage at exactly 100% of cap → 'exceeded' state."""
    ws = uuid.uuid4()
    period = _month(2025, 9)
    # SOLO plan: SMART_SEARCHES cap = 20 → 100% = 20.

    with patch(
        "civicsignals_api.modules.billing.services.get_workspace_plan",
        new=AsyncMock(return_value=SubscriptionPlan.SOLO),
    ):
        await billing_services.record_usage(
            session, ws, Dimension.SMART_SEARCHES_PER_MONTH, 20, period=period
        )
        states = await billing_services.get_limit_states(session, ws, period=period)

    _, _, pct, state = states[Dimension.SMART_SEARCHES_PER_MONTH.value]
    assert state == "exceeded", f"Expected 'exceeded' at 100%, got {state!r} (pct={pct})"
    assert pct == 100.0


# ---------------------------------------------------------------------------
# 10-13. GET /billing/limits - HTTP endpoint tests
# ---------------------------------------------------------------------------


def _build_limits_http_ctx(ws_id: uuid.UUID) -> Any:
    """Build a fake WorkspaceContext for the limits endpoint."""
    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = ws_id
    fake_ws.name = "Test"
    fake_user = MagicMock()
    fake_user.id = uuid.uuid4()
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.ADMIN
    return WorkspaceContext(workspace=fake_ws, user=fake_user, membership=fake_membership)


@pytest.mark.asyncio
async def test_limits_endpoint_shape(client: Any) -> None:
    """GET /billing/limits returns WorkspaceLimitsOut shape with all dimensions."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.billing.plans import Dimension

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)

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
                new=AsyncMock(return_value={}),
            ),
        ):
            resp = client.get(
                "/api/v1/billing/limits",
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "dimensions" in data
        assert "period" in data
        assert "workspace_id" in data
        for dim in Dimension:
            assert dim.value in data["dimensions"], f"Missing {dim.value!r}"
            entry = data["dimensions"][dim.value]
            assert "state" in entry
            assert "used" in entry
            assert "limit" in entry
            assert "pct" in entry
    finally:
        app.dependency_overrides.pop(require_workspace, None)


@pytest.mark.asyncio
async def test_limits_endpoint_ok_state(client: Any) -> None:
    """GET /billing/limits → 'ok' when usage is 0."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            patch(
                "civicsignals_api.modules.billing.services.refresh_seats_usage",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "civicsignals_api.modules.billing.services.get_usage",
                new=AsyncMock(return_value={}),
            ),
        ):
            resp = client.get(
                "/api/v1/billing/limits",
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 200
        data = resp.json()
        ai_entry = data["dimensions"][Dimension.AI_RUNS_PER_MONTH.value]
        assert ai_entry["state"] == "ok"
        assert ai_entry["used"] == 0
    finally:
        app.dependency_overrides.pop(require_workspace, None)


@pytest.mark.asyncio
async def test_limits_endpoint_warning_state(client: Any) -> None:
    """GET /billing/limits → 'warning' when usage = 80% of cap."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)
    # SOLO: AI_RUNS cap = 500 → 80% = 400.
    used_80pct = 400

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            patch(
                "civicsignals_api.modules.billing.services.refresh_seats_usage",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "civicsignals_api.modules.billing.services.get_usage",
                new=AsyncMock(return_value={Dimension.AI_RUNS_PER_MONTH.value: used_80pct}),
            ),
        ):
            resp = client.get(
                "/api/v1/billing/limits",
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 200
        ai_entry = resp.json()["dimensions"][Dimension.AI_RUNS_PER_MONTH.value]
        assert ai_entry["state"] == "warning"
        assert ai_entry["pct"] == 80.0
    finally:
        app.dependency_overrides.pop(require_workspace, None)


@pytest.mark.asyncio
async def test_limits_endpoint_exceeded_state(client: Any) -> None:
    """GET /billing/limits → 'exceeded' when usage >= 100% of cap."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)
    # SOLO: SMART_SEARCHES cap = 20 → 100% = 20.
    used_100pct = 20

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            patch(
                "civicsignals_api.modules.billing.services.refresh_seats_usage",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "civicsignals_api.modules.billing.services.get_usage",
                new=AsyncMock(return_value={Dimension.SMART_SEARCHES_PER_MONTH.value: used_100pct}),
            ),
        ):
            resp = client.get(
                "/api/v1/billing/limits",
                headers={"X-Workspace-Id": str(ws_id)},
            )

        assert resp.status_code == 200
        ss_entry = resp.json()["dimensions"][Dimension.SMART_SEARCHES_PER_MONTH.value]
        assert ss_entry["state"] == "exceeded"
        assert ss_entry["pct"] == 100.0
    finally:
        app.dependency_overrides.pop(require_workspace, None)


# ---------------------------------------------------------------------------
# 14-16. Demo smart-search gate (feature gate + quota enforcement)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_gate_402_when_feature_missing(client: Any) -> None:
    """Demo endpoint returns 402 when plan lacks SMART_SEARCH feature."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            # SOLO has SMART_SEARCH — all plans do. Patch plan_allows to return
            # False to simulate a plan that doesn't have the feature.
            patch(
                "civicsignals_api.modules.billing.services.plan_allows",
                return_value=False,
            ),
        ):
            resp = client.get(
                "/api/v1/billing/plan/demo-smart-search",
                headers={"X-Workspace-Id": str(ws_id)},
            )
        assert resp.status_code == 402
        body = resp.json()
        assert body["status"] == 402
        assert body["type"].endswith("plan_feature_required")
    finally:
        app.dependency_overrides.pop(require_workspace, None)


@pytest.mark.asyncio
async def test_demo_gate_429_when_quota_exhausted(client: Any) -> None:
    """Demo endpoint returns 429 when smart-search quota is exhausted."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            patch(
                "civicsignals_api.modules.billing.services.get_usage",
                # SOLO: SMART_SEARCHES cap = 20 → 20 used = 100% = exceeded.
                new=AsyncMock(return_value={Dimension.SMART_SEARCHES_PER_MONTH.value: 20}),
            ),
        ):
            resp = client.get(
                "/api/v1/billing/plan/demo-smart-search",
                headers={"X-Workspace-Id": str(ws_id)},
            )
        assert resp.status_code == 429
        body = resp.json()
        assert body["status"] == 429
        assert body["type"].endswith("limit_exceeded")
    finally:
        app.dependency_overrides.pop(require_workspace, None)


@pytest.mark.asyncio
async def test_demo_gate_200_when_within_limit(client: Any) -> None:
    """Demo endpoint returns 200 when feature enabled + quota not exhausted."""
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    ws_id = uuid.uuid4()
    ctx = _build_limits_http_ctx(ws_id)

    app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        with (
            patch(
                "civicsignals_api.modules.billing.services.get_workspace_plan",
                new=AsyncMock(return_value=SubscriptionPlan.SOLO),
            ),
            patch(
                "civicsignals_api.modules.billing.services.get_usage",
                new=AsyncMock(return_value={}),  # 0 usage → well within limit
            ),
        ):
            resp = client.get(
                "/api/v1/billing/plan/demo-smart-search",
                headers={"X-Workspace-Id": str(ws_id)},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
    finally:
        app.dependency_overrides.pop(require_workspace, None)
