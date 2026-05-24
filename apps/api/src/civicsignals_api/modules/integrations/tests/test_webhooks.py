"""Tests for the L3 outbound-webhook feature.

Covers:
- Webhook subscription CRUD + workspace isolation + admin-gating.
- Event → HMAC-signed delivery to matching subscribers (mocked HTTP).
- Signature verification (valid + tampered + replay).
- Retry + dead-letter on repeated failures.
- Delivery log recorded per attempt; secret never exposed.
- Secret revealed once at create; not in subsequent reads.
- ``retry_failed_webhook_deliveries`` task logic.

Live-DB tests skip without DATABASE_DIRECT_URL / DATABASE_URL.
Unit tests (pure logic / mockable) run always.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from base64 import urlsafe_b64decode
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import Settings
from civicsignals_api.modules.accounts.models import MembershipRole
from civicsignals_api.modules.integrations import services
from civicsignals_api.modules.integrations.models import (
    WEBHOOK_MAX_ATTEMPTS,
    WEBHOOK_RETRY_DELAYS,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookSubscription,
)
from civicsignals_api.modules.integrations.tests.conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
WEBHOOKS = "/api/v1/integrations/webhooks"
PASSWORD = "s3cur3-P4ssword!"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _create_workspace(client: TestClient, token: str, name: str = "WH WS") -> dict[str, Any]:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _add_member_at_role(user_id: str, workspace_id: str, role: MembershipRole) -> None:
    from civicsignals_api.modules.accounts import services as accounts_services

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            await accounts_services.add_member(
                session,
                workspace_id=uuid.UUID(workspace_id),
                user_id=uuid.UUID(user_id),
                role=role,
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())


def _settings(**overrides: Any) -> Settings:
    base = {
        "secret_key": "test-secret-key",
        "integrations_push_max_attempts": 3,
        "integrations_push_retry_base_seconds": 60,
        "integrations_push_retry_max_seconds": 3600,
        "integrations_oauth_state_ttl_seconds": 600,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit tests — pure logic, no DB required
# ---------------------------------------------------------------------------


def test_generate_webhook_secret_has_prefix() -> None:
    secret = services.generate_webhook_secret()
    assert secret.startswith("whsec_")
    assert len(secret) > 10


def test_generate_webhook_secret_is_unique() -> None:
    assert services.generate_webhook_secret() != services.generate_webhook_secret()


def test_webhook_secret_roundtrips_encryption() -> None:
    settings = _settings()
    secret = services.generate_webhook_secret()
    ciphertext = services.store_webhook_secret(secret, settings=settings)
    assert ciphertext != secret
    assert "whsec_" not in ciphertext  # plaintext not embedded
    recovered = services.recover_webhook_secret(ciphertext, settings=settings)
    assert recovered == secret


def test_build_webhook_signature_format() -> None:
    secret = services.generate_webhook_secret()
    body = b'{"foo":"bar"}'
    ts = int(time.time())
    sig = services.build_webhook_signature(timestamp=ts, raw_body=body, secret=secret)
    assert sig.startswith(f"t={ts},v1=")
    assert len(sig.split("v1=")[1]) == 64  # HMAC-SHA256 hex digest


def test_build_webhook_signature_hmac_is_correct() -> None:
    """Manually reproduce the HMAC and verify it matches the helper."""
    secret = "whsec_dGVzdC1zZWNyZXQ"  # "test-secret" base64url-encoded  # gitleaks:allow
    body = b'{"signal_type":"rfp_posted"}'
    ts = 1700000000
    sig = services.build_webhook_signature(timestamp=ts, raw_body=body, secret=secret)

    # Re-derive the key as the service does (strip prefix, pad, decode).
    key_part = secret.removeprefix("whsec_")
    padding = (4 - len(key_part) % 4) % 4
    key_bytes = urlsafe_b64decode(key_part + "=" * padding)
    signed_payload = f"{ts}.".encode("ascii") + body
    expected_hex = hmac.new(key_bytes, signed_payload, hashlib.sha256).hexdigest()
    assert sig == f"t={ts},v1={expected_hex}"


def test_verify_webhook_signature_valid() -> None:
    secret = services.generate_webhook_secret()
    body = b'{"event":"signal.created"}'
    ts = int(time.time())
    sig = services.build_webhook_signature(timestamp=ts, raw_body=body, secret=secret)
    assert services.verify_webhook_signature(header_value=sig, raw_body=body, secret=secret)


def test_verify_webhook_signature_tampered_body() -> None:
    secret = services.generate_webhook_secret()
    body = b'{"event":"signal.created"}'
    ts = int(time.time())
    sig = services.build_webhook_signature(timestamp=ts, raw_body=body, secret=secret)
    # Tamper the body — signature should fail.
    assert not services.verify_webhook_signature(
        header_value=sig, raw_body=b'{"event":"signal.scored"}', secret=secret
    )


def test_verify_webhook_signature_replay_protection() -> None:
    """Signatures more than max_age_seconds old are rejected."""
    secret = services.generate_webhook_secret()
    body = b"hello"
    old_ts = int(time.time()) - 600  # 10 minutes ago
    sig = services.build_webhook_signature(timestamp=old_ts, raw_body=body, secret=secret)
    assert not services.verify_webhook_signature(
        header_value=sig, raw_body=body, secret=secret, max_age_seconds=300
    )


def test_verify_webhook_signature_malformed_header() -> None:
    assert not services.verify_webhook_signature(
        header_value="not-a-signature",
        raw_body=b"body",
        secret=services.generate_webhook_secret(),
    )


# ---------------------------------------------------------------------------
# Delivery helper unit tests (mocked HTTP, no DB)
# ---------------------------------------------------------------------------


class _FakeWebhookSession:
    """Minimal async session stub — flush only."""

    async def flush(self) -> None:
        pass


def _make_subscription(secret: str, settings: Settings) -> WebhookSubscription:
    sub = WebhookSubscription(
        workspace_id=uuid.uuid4(),
        url="https://subscriber.test/hook",
        secret_encrypted=services.store_webhook_secret(secret, settings=settings),
        subscribed_events=["signal.created"],
        active=True,
    )
    return sub


def _make_delivery(sub: WebhookSubscription) -> WebhookDelivery:
    delivery = WebhookDelivery(
        workspace_id=sub.workspace_id,
        subscription_id=uuid.uuid4(),
        event_type="signal.created",
        request_body={"id": "sig-1", "signal_type": "rfp_posted"},
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    return delivery


def test_delivery_success_marks_status() -> None:
    settings = _settings()
    secret = services.generate_webhook_secret()
    sub = _make_subscription(secret, settings)
    delivery = _make_delivery(sub)

    received_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received_headers.update(dict(request.headers))
        return httpx.Response(200, text="ok")

    async def _run() -> WebhookDelivery:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await services._deliver_one(
                _FakeWebhookSession(),  # type: ignore[arg-type]
                subscription=sub,
                delivery=delivery,
                http=http,
                settings=settings,
            )

    result = asyncio.run(_run())
    assert result.status == WebhookDeliveryStatus.SUCCESS
    assert result.response_status == 200
    assert result.attempt_count == 1
    assert result.retry_at is None
    # Verify signature header was sent.
    assert "x-civicsignals-signature" in received_headers
    sig_header = received_headers["x-civicsignals-signature"]
    assert sig_header.startswith("t=")
    assert "v1=" in sig_header
    # Verify the signature is valid (subscriber-side verification).
    raw_body = json.dumps(delivery.request_body, separators=(",", ":")).encode("utf-8")
    assert services.verify_webhook_signature(
        header_value=sig_header, raw_body=raw_body, secret=secret, max_age_seconds=30
    )


def test_delivery_failure_schedules_retry() -> None:
    settings = _settings()
    secret = services.generate_webhook_secret()
    sub = _make_subscription(secret, settings)
    delivery = _make_delivery(sub)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    async def _run() -> WebhookDelivery:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await services._deliver_one(
                _FakeWebhookSession(),  # type: ignore[arg-type]
                subscription=sub,
                delivery=delivery,
                http=http,
                settings=settings,
            )

    result = asyncio.run(_run())
    assert result.status == WebhookDeliveryStatus.FAILED
    assert result.response_status == 500
    assert result.retry_at is not None
    assert result.attempt_count == 1


def test_delivery_exhausted_retries_dead_letters() -> None:
    settings = _settings()
    secret = services.generate_webhook_secret()
    sub = _make_subscription(secret, settings)
    delivery = _make_delivery(sub)
    # Simulate all prior attempts exhausted (attempt_count = max - 1 already).
    delivery.attempt_count = WEBHOOK_MAX_ATTEMPTS - 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still down")

    async def _run() -> WebhookDelivery:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await services._deliver_one(
                _FakeWebhookSession(),  # type: ignore[arg-type]
                subscription=sub,
                delivery=delivery,
                http=http,
                settings=settings,
            )

    result = asyncio.run(_run())
    assert result.status == WebhookDeliveryStatus.DEAD_LETTER
    assert result.retry_at is None
    assert result.attempt_count == WEBHOOK_MAX_ATTEMPTS


def test_delivery_network_error_schedules_retry() -> None:
    settings = _settings()
    secret = services.generate_webhook_secret()
    sub = _make_subscription(secret, settings)
    delivery = _make_delivery(sub)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def _run() -> WebhookDelivery:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await services._deliver_one(
                _FakeWebhookSession(),  # type: ignore[arg-type]
                subscription=sub,
                delivery=delivery,
                http=http,
                settings=settings,
            )

    result = asyncio.run(_run())
    assert result.status == WebhookDeliveryStatus.FAILED
    assert result.response_status is None
    assert result.retry_at is not None


def test_delivery_secret_not_in_stored_headers() -> None:
    """The HMAC signature is never stored in request_headers (security)."""
    settings = _settings()
    secret = services.generate_webhook_secret()
    sub = _make_subscription(secret, settings)
    delivery = _make_delivery(sub)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    async def _run() -> WebhookDelivery:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await services._deliver_one(
                _FakeWebhookSession(),  # type: ignore[arg-type]
                subscription=sub,
                delivery=delivery,
                http=http,
                settings=settings,
            )

    result = asyncio.run(_run())
    stored_headers = result.request_headers
    # The signature header must NOT be stored (it changes each attempt and
    # contains timing data — storing it is misleading and wastes space).
    assert "X-CivicSignals-Signature" not in stored_headers
    assert "x-civicsignals-signature" not in stored_headers
    # But other headers are stored for diagnostics.
    assert "X-CivicSignals-Event" in stored_headers


def test_webhook_retry_delays_match_spec() -> None:
    """The retry schedule follows doc 08 §1.10 (0m, 1m, 5m, 30m, 2h, 12h)."""
    assert WEBHOOK_RETRY_DELAYS[0] == 60  # 1 minute
    assert WEBHOOK_RETRY_DELAYS[1] == 300  # 5 minutes
    assert WEBHOOK_RETRY_DELAYS[2] == 1800  # 30 minutes
    assert WEBHOOK_RETRY_DELAYS[3] == 7200  # 2 hours
    assert WEBHOOK_RETRY_DELAYS[4] == 43200  # 12 hours
    assert len(WEBHOOK_RETRY_DELAYS) + 1 == WEBHOOK_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Live-DB API tests — subscription CRUD + delivery + isolation
# ---------------------------------------------------------------------------


def test_list_webhooks_requires_auth(client: TestClient) -> None:
    resp = client.get(WEBHOOKS)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_list_webhooks_empty_for_owner(client: TestClient) -> None:
    token, _ = _signup(client, "wh-owner-empty@example.com")
    ws = _create_workspace(client, token)
    resp = client.get(WEBHOOKS, headers=_hdr(token, ws["id"]))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"data": []}


def test_create_webhook_requires_admin(client: TestClient) -> None:
    owner_token, _ = _signup(client, "wh-admin-gate-owner@example.com")
    ws = _create_workspace(client, owner_token, "Gate WS WH")
    member_token, member_id = _signup(client, "wh-admin-gate-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)

    resp = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/test", "events": ["signal.created"]},
        headers=_hdr(member_token, ws["id"]),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"].endswith("/forbidden")


def test_create_webhook_returns_secret_once(client: TestClient) -> None:
    token, _ = _signup(client, "wh-create-secret@example.com")
    ws = _create_workspace(client, token, "Secret WS")
    resp = client.post(
        WEBHOOKS,
        json={
            "url": "https://hooks.example.com/secret-test",
            "events": ["signal.created", "signal.scored"],
            "description": "My Zapier hook",
        },
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["url"] == "https://hooks.example.com/secret-test"
    assert set(body["events"]) == {"signal.created", "signal.scored"}
    assert body["active"] is True
    assert body["description"] == "My Zapier hook"
    secret = body["secret"]
    assert secret.startswith("whsec_")
    sub_id = body["id"]

    # Secret is NOT returned in subsequent reads.
    get_resp = client.get(f"{WEBHOOKS}/{sub_id}", headers=_hdr(token, ws["id"]))
    assert get_resp.status_code == 200, get_resp.text
    get_body = get_resp.json()
    assert "secret" not in get_body

    # Secret is NOT returned in the list.
    list_resp = client.get(WEBHOOKS, headers=_hdr(token, ws["id"]))
    assert list_resp.status_code == 200, list_resp.text
    for item in list_resp.json()["data"]:
        assert "secret" not in item


def test_create_webhook_unknown_event_type_rejected(client: TestClient) -> None:
    token, _ = _signup(client, "wh-bad-event@example.com")
    ws = _create_workspace(client, token, "Bad Event WS")
    resp = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/bad", "events": ["unknown.event"]},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 422, resp.text


def test_update_webhook(client: TestClient) -> None:
    token, _ = _signup(client, "wh-update@example.com")
    ws = _create_workspace(client, token, "Update WS")
    create = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/update", "events": ["signal.created"]},
        headers=_hdr(token, ws["id"]),
    )
    assert create.status_code == 201, create.text
    sub_id = create.json()["id"]

    patch = client.patch(
        f"{WEBHOOKS}/{sub_id}",
        json={"active": False, "events": ["signal.scored"]},
        headers=_hdr(token, ws["id"]),
    )
    assert patch.status_code == 200, patch.text
    updated = patch.json()
    assert updated["active"] is False
    assert updated["events"] == ["signal.scored"]


def test_delete_webhook(client: TestClient) -> None:
    token, _ = _signup(client, "wh-delete@example.com")
    ws = _create_workspace(client, token, "Delete WS")
    create = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/delete-me", "events": ["signal.created"]},
        headers=_hdr(token, ws["id"]),
    )
    sub_id = create.json()["id"]

    del_resp = client.delete(f"{WEBHOOKS}/{sub_id}", headers=_hdr(token, ws["id"]))
    assert del_resp.status_code == 204

    # Gone from the list.
    listing = client.get(WEBHOOKS, headers=_hdr(token, ws["id"]))
    assert listing.json() == {"data": []}


def test_webhook_workspace_isolation(client: TestClient) -> None:
    token_a, _ = _signup(client, "wh-iso-a@example.com")
    token_b, _ = _signup(client, "wh-iso-b@example.com")
    ws_a = _create_workspace(client, token_a, "ISO A WH")
    ws_b = _create_workspace(client, token_b, "ISO B WH")

    client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/iso-a", "events": ["signal.created"]},
        headers=_hdr(token_a, ws_a["id"]),
    )
    list_a = client.get(WEBHOOKS, headers=_hdr(token_a, ws_a["id"])).json()
    list_b = client.get(WEBHOOKS, headers=_hdr(token_b, ws_b["id"])).json()
    assert len(list_a["data"]) == 1
    assert list_b["data"] == []

    # Workspace B cannot GET workspace A's subscription.
    sub_id = list_a["data"][0]["id"]
    resp = client.get(f"{WEBHOOKS}/{sub_id}", headers=_hdr(token_b, ws_b["id"]))
    assert resp.status_code == 404

    # Workspace B cannot DELETE workspace A's subscription.
    resp = client.delete(f"{WEBHOOKS}/{sub_id}", headers=_hdr(token_b, ws_b["id"]))
    assert resp.status_code == 404


def test_delivery_log_endpoint_requires_admin(client: TestClient) -> None:
    owner_token, _ = _signup(client, "wh-dl-owner@example.com")
    ws = _create_workspace(client, owner_token, "DL Admin WS")
    viewer_token, viewer_id = _signup(client, "wh-dl-viewer@example.com")
    _add_member_at_role(viewer_id, ws["id"], MembershipRole.VIEWER)

    create = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/dl", "events": ["signal.created"]},
        headers=_hdr(owner_token, ws["id"]),
    )
    sub_id = create.json()["id"]

    resp = client.get(f"{WEBHOOKS}/{sub_id}/deliveries", headers=_hdr(viewer_token, ws["id"]))
    assert resp.status_code == 403


def test_delivery_log_empty_initially(client: TestClient) -> None:
    token, _ = _signup(client, "wh-dl-empty@example.com")
    ws = _create_workspace(client, token, "DL Empty WS")
    create = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/dl-empty", "events": ["signal.created"]},
        headers=_hdr(token, ws["id"]),
    )
    sub_id = create.json()["id"]
    resp = client.get(f"{WEBHOOKS}/{sub_id}/deliveries", headers=_hdr(token, ws["id"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


def test_ping_webhook_records_delivery(client: TestClient) -> None:
    """POST /webhooks/{id}/ping records a delivery (mocked via a 404 subscriber)."""
    token, _ = _signup(client, "wh-ping@example.com")
    ws = _create_workspace(client, token, "Ping WS")

    # Create subscription pointing to a non-existent host; we'll get a network
    # error or a 404, either way a delivery row is created.
    create = client.post(
        WEBHOOKS,
        json={"url": "https://hooks.example.com/ping-me", "events": ["signal.created"]},
        headers=_hdr(token, ws["id"]),
    )
    assert create.status_code == 201, create.text
    sub_id = create.json()["id"]

    ping = client.post(f"{WEBHOOKS}/{sub_id}/ping", headers=_hdr(token, ws["id"]))
    # 202 whether or not the subscriber is reachable.
    assert ping.status_code == 202, ping.text
    ping_body = ping.json()
    assert "delivery_id" in ping_body
    assert "status" in ping_body

    # At least one delivery log row exists after the ping.
    log_resp = client.get(f"{WEBHOOKS}/{sub_id}/deliveries", headers=_hdr(token, ws["id"]))
    assert log_resp.status_code == 200, log_resp.text
    assert len(log_resp.json()["data"]) >= 1
    delivery = log_resp.json()["data"][0]
    assert delivery["event_type"] == "ping"
    # Secret is never in the delivery log.
    assert "secret" not in str(delivery)


def test_get_webhook_not_found_404(client: TestClient) -> None:
    token, _ = _signup(client, "wh-404@example.com")
    ws = _create_workspace(client, token, "404 WS")
    resp = client.get(f"{WEBHOOKS}/{uuid.uuid4()}", headers=_hdr(token, ws["id"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["type"].endswith("/not_found")


def test_retry_task_commits_before_each_delivery_http(
    monkeypatch: Any,
) -> None:
    """A commit must precede every delivery's HTTP POST.

    Under PgBouncer transaction-mode pooling an open transaction pins a pooled
    server connection (doc 06 §4). The subscription read opens a transaction, so
    the sweep must commit (release the connection) *before* calling the delivery
    helper that performs the network POST — not just once at the end of the batch.
    We instrument the (stubbed) delivery to record the commit count at the moment
    it is invoked and assert a commit preceded every attempt, which would fail if
    the read transaction were held idle across the HTTP call.
    """
    from civicsignals_api.modules.integrations import tasks as webhook_tasks

    class _Delivery:
        def __init__(self) -> None:
            self.subscription_id = uuid.uuid4()

    deliveries = [_Delivery(), _Delivery(), _Delivery()]

    class _Session:
        def __init__(self) -> None:
            self.commits = 0

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def commit(self) -> None:
            self.commits += 1

    session = _Session()

    class _Http:
        async def aclose(self) -> None:
            pass

    async def _return(value: Any) -> Any:
        return value

    # Record the session's commit count at the instant each delivery's HTTP call
    # would run, so we can assert the read transaction was already released.
    commits_at_delivery: list[int] = []

    async def _record_delivery(_session: Any, **_kwargs: Any) -> None:
        commits_at_delivery.append(session.commits)

    monkeypatch.setattr("civicsignals_api.db.SessionLocal", lambda: session)
    monkeypatch.setattr(
        services, "due_failed_webhook_deliveries", lambda _s: _return(deliveries)
    )
    monkeypatch.setattr(
        services, "get_webhook_subscription_unscoped", lambda _s, _id: _return(object())
    )
    monkeypatch.setattr(services, "execute_webhook_delivery", _record_delivery)
    monkeypatch.setattr(services, "default_http_client", lambda: _Http())

    attempted = asyncio.run(webhook_tasks._retry_failed_webhook_deliveries_async())

    assert attempted == 3
    assert len(commits_at_delivery) == 3
    # A commit released the connection before the first HTTP call ...
    assert commits_at_delivery[0] >= 1
    # ... and before every subsequent one — strictly increasing (non-decreasing
    # and all distinct), i.e. not a single batch-wide transaction held open across
    # all the network round-trips.
    assert commits_at_delivery == sorted(commits_at_delivery)
    assert len(set(commits_at_delivery)) == len(commits_at_delivery)
