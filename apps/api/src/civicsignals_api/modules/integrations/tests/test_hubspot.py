"""Unit tests for the HubSpot provider (K3, no DB needed; mirrors K2).

Covers the pieces that are pure logic / mockable: the HubSpot OAuth token parsing
(hub_id capture), object/property discovery over a mocked CRM v3 transport, the
create/update push (Deal + custom object) with idempotent upsert by external id,
and scope-aware error mapping. HubSpot is never contacted live — the HTTP
transport is mocked (``httpx.MockTransport``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.integrations.models import PushErrorCode
from civicsignals_api.modules.integrations.providers import (
    HUBSPOT_API_BASE,
    HubSpotProvider,
    ProviderError,
    PushRequest,
)


def _settings(**overrides: Any) -> Settings:
    base = {
        "secret_key": "test-secret-key",
        "hubspot_client_id": "cid",
        "hubspot_client_secret": "csecret",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


ACCOUNT: dict[str, object] = {"hub_id": "12345678"}


# ---------------------------------------------------------------------------
# OAuth config + token parsing (hub_id capture)
# ---------------------------------------------------------------------------


def test_hubspot_oauth_config_uses_hubspot_hosts() -> None:
    prov = HubSpotProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    config = prov.oauth_config()
    assert config.authorize_url == "https://app.hubspot.com/oauth/authorize"
    assert config.token_url == "https://api.hubapi.com/oauth/v1/token"
    assert any(s.startswith("crm.objects.deals") for s in config.scopes)
    assert config.configured is True


def test_hubspot_token_parse_captures_hub_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # HubSpot expects form-encoded credentials in the token request body.
        assert b"client_id=cid" in request.content
        assert b"grant_type=authorization_code" in request.content
        return httpx.Response(
            200,
            json={
                "access_token": "access-AAA",
                "refresh_token": "refresh-BBB",
                "expires_in": 1800,
                "hub_id": 12345678,
                "hub_domain": "acme.hubspot.com",
            },
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            tokens = await prov.exchange_code(code="code", redirect_uri="https://cb")
            assert tokens.access_token == "access-AAA"
            assert tokens.refresh_token == "refresh-BBB"
            assert tokens.expires_at is not None  # expires_in → expires_at
            assert tokens.provider_account["hub_id"] == "12345678"
            assert tokens.provider_account["hub_domain"] == "acme.hubspot.com"

    asyncio.run(_run())


def test_hubspot_refresh_reuses_refresh_token_when_omitted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 1800})

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            tokens = await prov.refresh(refresh_token="refresh-OLD")
            assert tokens.access_token == "fresh"
            # Mixin reuses the old refresh token when the response omits one.
            assert tokens.refresh_token == "refresh-OLD"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Object / property discovery (mocked CRM v3 schemas + properties)
# ---------------------------------------------------------------------------


def test_discover_objects_includes_standard_and_custom() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v3/schemas"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "civic_signal",
                        "fullyQualifiedName": "p12345_civic_signal",
                        "labels": {"singular": "Civic Signal", "plural": "Civic Signals"},
                    }
                ]
            },
        )

    async def _run() -> list[Any]:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            return await prov.discover_objects(access_token="tok", provider_account=ACCOUNT)

    objects = asyncio.run(_run())
    names = {o.name for o in objects}
    # Standard objects always offered + the portal's custom object.
    assert "deals" in names
    assert "contacts" in names
    assert "p12345_civic_signal" in names
    deal = next(o for o in objects if o.name == "deals")
    assert deal.custom is False
    assert deal.label == "Deal"
    custom = next(o for o in objects if o.name == "p12345_civic_signal")
    assert custom.custom is True
    assert custom.label == "Civic Signals"


def test_describe_object_returns_writable_properties() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v3/properties/deals"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"name": "dealname", "label": "Deal Name", "type": "string"},
                    {"name": "amount", "label": "Amount", "type": "number", "required": False},
                    # Calculated → filtered out.
                    {"name": "hs_acv", "label": "ACV", "type": "number", "calculated": True},
                    # Read-only via modificationMetadata → filtered out.
                    {
                        "name": "createdate",
                        "label": "Create Date",
                        "type": "datetime",
                        "modificationMetadata": {"readOnlyValue": True},
                    },
                ]
            },
        )

    async def _run() -> list[Any]:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            return await prov.describe_object(
                access_token="tok", provider_account=ACCOUNT, object_name="deals"
            )

    fields = asyncio.run(_run())
    names = {f.name for f in fields}
    assert names == {"dealname", "amount"}
    dealname = next(f for f in fields if f.name == "dealname")
    assert dealname.createable is True
    assert dealname.updateable is True


def test_discover_objects_maps_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"message": "expired", "category": "EXPIRED_AUTHENTICATION"}
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            await prov.discover_objects(access_token="tok", provider_account=ACCOUNT)

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.AUTH


# ---------------------------------------------------------------------------
# Push: create / update Deal + custom object (mocked HubSpot)
# ---------------------------------------------------------------------------


def test_push_creates_deal() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(
            201,
            json={"id": "8675309", "properties": {"dealname": "Acme RFP"}},
            headers={"x-hubspot-correlation-id": "corr-123"},
        )

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="hubspot.deals",
                    payload={"dealname": "Acme RFP", "__provider_account__": ACCOUNT},
                ),
            )

    result = asyncio.run(_run())
    assert result.external_id == "8675309"
    assert result.created is True
    assert result.provider_response_id == "corr-123"
    assert captured["method"] == "POST"
    assert captured["path"] == "/crm/v3/objects/deals"
    body = json.loads(captured["body"])
    # HubSpot wraps property values in a ``properties`` envelope.
    assert body == {"properties": {"dealname": "Acme RFP"}}
    # The control key never reaches HubSpot.
    assert "__provider_account__" not in captured["body"]


def test_push_updates_when_external_id_present() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(200, json={"id": "8675309", "properties": {"dealname": "Updated"}})

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="hubspot.deals",
                    payload={"dealname": "Updated", "__provider_account__": ACCOUNT},
                    external_id="8675309",
                ),
            )

    result = asyncio.run(_run())
    assert result.created is False
    assert result.external_id == "8675309"
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/crm/v3/objects/deals/8675309"


def test_push_deal_alias_resolves_to_deals() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(201, json={"id": "1", "properties": {}})

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(target="hubspot.Deal", payload={"dealname": "X"}),
            )

    asyncio.run(_run())
    assert captured["path"] == "/crm/v3/objects/deals"


def test_push_to_custom_object_keeps_name() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(201, json={"id": "c-1", "properties": {}})

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="hubspot.p12345_civic_signal",
                    payload={"title": "RFP"},
                ),
            )

    result = asyncio.run(_run())
    assert result.external_id == "c-1"
    assert captured["path"] == f"{HUBSPOT_API_BASE}/crm/v3/objects/p12345_civic_signal".replace(
        HUBSPOT_API_BASE, ""
    )


def test_push_validation_error_maps_scope_aware() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "message": "Property values were not valid: dealstage is required",
                "category": "VALIDATION_ERROR",
                "correlationId": "corr-err",
            },
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            await prov.push(
                access_token="tok",
                request=PushRequest(target="hubspot.deals", payload={"dealname": "Acme"}),
            )

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.VALIDATION
    assert "dealstage" in exc_info.value.message
    assert exc_info.value.provider_response_id == "corr-err"


def test_push_auth_error_maps_to_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"message": "expired", "category": "EXPIRED_AUTHENTICATION"}
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            await prov.push(
                access_token="tok",
                request=PushRequest(target="hubspot.deals", payload={}),
            )

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.AUTH


def test_push_create_no_id_raises_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"properties": {"dealname": "X"}})  # no id

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = HubSpotProvider(_settings(), http)
            await prov.push(
                access_token="tok",
                request=PushRequest(target="hubspot.deals", payload={"dealname": "X"}),
            )

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.UNKNOWN
