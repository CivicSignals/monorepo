"""Test fixtures for the billing module (N1).

Unit tests (services) use an in-memory async SQLite engine so they run in CI
without a real Postgres instance.

Because billing_customer and billing_subscription have FK references to
accounts_workspace, we create a minimal stub for that table (and its own FK
dependency accounts_organization) using standard SQL types so SQLite can
compile the schema without CITEXT / pgvector.

Stripe network calls are mocked via ``override_stripe_client`` /
``reset_stripe_client`` from billing.services.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from civicsignals_api.db import get_session
from civicsignals_api.main import app
from civicsignals_api.modules.billing import services as billing_services

# Register billing models so their Table objects exist on Base.metadata.
from civicsignals_api.modules.billing.models import (  # noqa: F401
    BillingCustomer,
    BillingSubscription,
    BillingWebhookEvent,
)

# ---------------------------------------------------------------------------
# SQLite-compatible metadata for billing tests
# ---------------------------------------------------------------------------
# We build a separate MetaData that mirrors only the tables we need, using
# standard SQL types (TEXT instead of CITEXT, VARCHAR(36) instead of UUID).

_TEST_META = MetaData()

# Minimal stub for accounts_organization (needed by accounts_workspace FK).
_accounts_org = Table(
    "accounts_organization",
    _TEST_META,
    Column("id", String(36), primary_key=True),
)

# Minimal stub for accounts_workspace (referenced by billing_customer + sub).
_accounts_ws = Table(
    "accounts_workspace",
    _TEST_META,
    Column("id", String(36), primary_key=True),
)

# Billing tables — copied from Base.metadata, cast to the test MetaData.
# We do this lazily in _get_test_meta() so Base.metadata is fully populated
# by the time the test session starts.


def _get_billing_meta() -> MetaData:
    """Return a SQLite-compatible MetaData containing accounts stubs + billing tables."""
    from civicsignals_api.db import Base

    billing_table_names = {
        "billing_customer",
        "billing_subscription",
        "billing_webhook_event",
    }
    meta = MetaData()

    # Add the stub tables first (so FKs resolve).
    for stub in (_accounts_org, _accounts_ws):
        stub.to_metadata(meta)

    # Clone billing tables into the meta; tometadata copies columns +
    # constraints and re-resolves string FKs to the tables already in meta.
    for name, table in Base.metadata.tables.items():
        if name in billing_table_names:
            table.to_metadata(meta)

    return meta


_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _make_engine() -> Any:
    return create_async_engine(
        _SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


async def _create_schema(engine: Any) -> None:
    meta = _get_billing_meta()
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Any:
    return _make_engine()


@pytest.fixture
def session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    asyncio.run(_create_schema(engine))
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


# ---------------------------------------------------------------------------
# HTTP TestClient fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client(engine: Any) -> Iterator[TestClient]:
    asyncio.run(_create_schema(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Mock Stripe client helpers
# ---------------------------------------------------------------------------


def make_mock_stripe_client(
    stripe_customer_id: str = "cus_test_123",
) -> MagicMock:
    """Build a MagicMock that satisfies the StripeClient protocol."""
    mock = MagicMock()
    mock.customers.create.return_value = {"id": stripe_customer_id}
    mock.customers.retrieve.return_value = {"id": stripe_customer_id}
    return mock


@pytest.fixture(autouse=True)
def reset_stripe(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Auto-reset the cached Stripe client after every test."""
    yield
    billing_services.reset_stripe_client()


# ---------------------------------------------------------------------------
# Stripe webhook payload helpers
# ---------------------------------------------------------------------------


def make_stripe_event(
    event_type: str,
    data_object: dict[str, Any],
    event_id: str = "evt_test_001",
) -> dict[str, Any]:
    """Build a minimal Stripe event payload dict."""
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
        "object": "event",
        "api_version": "2024-04-10",
        "created": int(time.time()),
        "livemode": False,
    }


def make_subscription_updated_event(
    stripe_subscription_id: str = "sub_test_123",
    status: str = "active",
    price_id: str = "price_starter",
    current_period_end: int | None = None,
    event_id: str = "evt_sub_updated_001",
) -> dict[str, Any]:
    """Build a ``customer.subscription.updated`` event payload."""
    ts = current_period_end or (int(time.time()) + 30 * 86400)
    return make_stripe_event(
        event_type="customer.subscription.updated",
        event_id=event_id,
        data_object={
            "id": stripe_subscription_id,
            "object": "subscription",
            "status": status,
            "current_period_end": ts,
            "items": {
                "data": [
                    {"price": {"id": price_id}},
                ]
            },
        },
    )


def make_invoice_paid_event(
    stripe_subscription_id: str = "sub_test_123",
    period_end: int | None = None,
    event_id: str = "evt_inv_paid_001",
) -> dict[str, Any]:
    """Build an ``invoice.paid`` event payload."""
    ts = period_end or (int(time.time()) + 30 * 86400)
    return make_stripe_event(
        event_type="invoice.paid",
        event_id=event_id,
        data_object={
            "id": "in_test_001",
            "object": "invoice",
            "subscription": stripe_subscription_id,
            "period_end": ts,
        },
    )


def make_invoice_payment_failed_event(
    stripe_subscription_id: str = "sub_test_123",
    event_id: str = "evt_inv_failed_001",
) -> dict[str, Any]:
    """Build an ``invoice.payment_failed`` event payload."""
    return make_stripe_event(
        event_type="invoice.payment_failed",
        event_id=event_id,
        data_object={
            "id": "in_test_002",
            "object": "invoice",
            "subscription": stripe_subscription_id,
        },
    )


def signed_webhook_request(
    event: dict[str, Any],
    secret: str = "whsec_test_secret",
) -> tuple[bytes, str]:
    """Return (payload_bytes, stripe-signature header) for a test event.

    We construct the ``stripe-signature`` header manually (``t=<ts>,v1=<hmac>``)
    so tests don't need a real Stripe endpoint secret or the Stripe test mode.
    The signature scheme is documented at:
    https://stripe.com/docs/webhooks/signatures
    """
    import hashlib
    import hmac

    payload_bytes = json.dumps(event).encode()
    ts = str(int(time.time()))
    signed_payload = f"{ts}.{payload_bytes.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    sig_header = f"t={ts},v1={digest}"
    return payload_bytes, sig_header
