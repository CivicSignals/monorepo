"""Unit tests for the Salesforce provider + field mapping (K2, no DB needed).

Covers the pieces that are pure logic / mockable: the Salesforce OAuth token
parsing (instance_url capture), object/field discovery over a mocked describe
transport, the create/update push (Opportunity + custom object) with idempotent
upsert by external id, scope-aware error mapping, and the field-mapping → payload
application. Salesforce is never contacted live — the HTTP transport is mocked.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.integrations import services
from civicsignals_api.modules.integrations.models import (
    FieldMapping,
    PushErrorCode,
)
from civicsignals_api.modules.integrations.providers import (
    SALESFORCE_API_VERSION,
    ProviderError,
    PushRequest,
    SalesforceProvider,
)


def _settings(**overrides: Any) -> Settings:
    base = {
        "secret_key": "test-secret-key",
        "salesforce_client_id": "cid",
        "salesforce_client_secret": "csecret",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


INSTANCE = "https://acme.my.salesforce.com"
ACCOUNT: dict[str, object] = {
    "instance_url": INSTANCE,
    "identity_url": "https://login.salesforce.com/id/x/y",
}


# ---------------------------------------------------------------------------
# OAuth config + token parsing (instance_url capture)
# ---------------------------------------------------------------------------


def test_salesforce_oauth_config_uses_login_host() -> None:
    prov = SalesforceProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    config = prov.oauth_config()
    assert config.authorize_url == "https://login.salesforce.com/services/oauth2/authorize"
    assert config.token_url == "https://login.salesforce.com/services/oauth2/token"
    assert "api" in config.scopes and "refresh_token" in config.scopes
    assert config.configured is True


def test_salesforce_token_parse_captures_instance_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "access-AAA",
                "refresh_token": "refresh-BBB",
                "instance_url": INSTANCE,
                "id": "https://login.salesforce.com/id/00D/005",
                "scope": "api refresh_token",
            },
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            tokens = await prov.exchange_code(code="code", redirect_uri="https://cb")
            assert tokens.access_token == "access-AAA"
            assert tokens.provider_account["instance_url"] == INSTANCE
            assert "identity_url" in tokens.provider_account

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Object / field discovery (mocked describe)
# ---------------------------------------------------------------------------


def test_discover_objects_filters_to_createable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/services/data/{SALESFORCE_API_VERSION}/sobjects"
        return httpx.Response(
            200,
            json={
                "sobjects": [
                    {"name": "Opportunity", "label": "Opportunity", "createable": True},
                    {
                        "name": "Custom_Deal__c",
                        "label": "Custom Deal",
                        "createable": True,
                        "custom": True,
                    },
                    {"name": "ReadOnlyThing", "label": "RO", "createable": False},
                ]
            },
        )

    async def _run() -> list[Any]:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            return await prov.discover_objects(access_token="tok", provider_account=ACCOUNT)

    objects = asyncio.run(_run())
    names = {o.name for o in objects}
    assert names == {"Opportunity", "Custom_Deal__c"}
    custom = next(o for o in objects if o.name == "Custom_Deal__c")
    assert custom.custom is True


def test_describe_object_returns_writable_fields_and_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sobjects/Opportunity/describe")
        return httpx.Response(
            200,
            json={
                "fields": [
                    {
                        "name": "Name",
                        "label": "Name",
                        "type": "string",
                        "createable": True,
                        "updateable": True,
                        "nillable": False,
                    },
                    {
                        "name": "Amount",
                        "label": "Amount",
                        "type": "currency",
                        "createable": True,
                        "updateable": True,
                        "nillable": True,
                    },
                    {
                        "name": "Id",
                        "label": "Id",
                        "type": "id",
                        "createable": False,
                        "updateable": False,
                        "nillable": False,
                    },
                ]
            },
        )

    async def _run() -> list[Any]:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            return await prov.describe_object(
                access_token="tok", provider_account=ACCOUNT, object_name="Opportunity"
            )

    fields = asyncio.run(_run())
    names = {f.name for f in fields}
    # Id is read-only → filtered out.
    assert names == {"Name", "Amount"}
    name_field = next(f for f in fields if f.name == "Name")
    assert name_field.required is True  # nillable=false + no default
    amount_field = next(f for f in fields if f.name == "Amount")
    assert amount_field.required is False


def test_discover_missing_instance_url_raises_not_found() -> None:
    async def _run() -> None:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            prov = SalesforceProvider(_settings(), http)
            await prov.discover_objects(access_token="tok", provider_account={})

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Push: create / update Opportunity + custom object (mocked Salesforce)
# ---------------------------------------------------------------------------


def test_push_creates_opportunity() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(
            201,
            json={"id": "0061T00000ABCDE", "success": True},
            headers={"x-request-id": "req-123"},
        )

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="salesforce.opportunity",
                    payload={"Name": "Acme RFP", "__provider_account__": ACCOUNT},
                ),
            )

    result = asyncio.run(_run())
    assert result.external_id == "0061T00000ABCDE"
    assert result.created is True
    assert result.provider_response_id == "req-123"
    assert captured["method"] == "POST"
    assert captured["path"].endswith("/sobjects/Opportunity")
    # The control key never reaches Salesforce.
    assert "__provider_account__" not in captured["body"]
    assert "Acme RFP" in captured["body"]


def test_push_updates_when_external_id_present() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="salesforce.opportunity",
                    payload={"Name": "Updated", "__provider_account__": ACCOUNT},
                    external_id="0061T00000ABCDE",
                ),
            )

    result = asyncio.run(_run())
    assert result.created is False
    assert result.external_id == "0061T00000ABCDE"
    assert captured["method"] == "PATCH"
    assert captured["path"].endswith("/sobjects/Opportunity/0061T00000ABCDE")


def test_push_to_custom_object_keeps_suffix() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(201, json={"id": "a001T00000XYZ", "success": True})

    async def _run() -> Any:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            return await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="salesforce.Civic_Signal__c",
                    payload={"Title__c": "RFP", "__provider_account__": ACCOUNT},
                ),
            )

    result = asyncio.run(_run())
    assert result.external_id == "a001T00000XYZ"
    assert captured["path"].endswith("/sobjects/Civic_Signal__c")


def test_push_validation_error_maps_scope_aware() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json=[
                {
                    "errorCode": "REQUIRED_FIELD_MISSING",
                    "message": "Required fields are missing: [CloseDate]",
                    "fields": ["CloseDate"],
                }
            ],
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="salesforce.opportunity",
                    payload={"Name": "Acme", "__provider_account__": ACCOUNT},
                ),
            )

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.VALIDATION
    assert "CloseDate" in exc_info.value.message


def test_push_auth_error_maps_to_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID", "message": "expired"}])

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = SalesforceProvider(_settings(), http)
            await prov.push(
                access_token="tok",
                request=PushRequest(
                    target="salesforce.opportunity",
                    payload={"__provider_account__": ACCOUNT},
                ),
            )

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.code == PushErrorCode.AUTH


# ---------------------------------------------------------------------------
# Field-mapping application (pure logic)
# ---------------------------------------------------------------------------


def _mapping(
    field_map: dict[str, object], constants: dict[str, object] | None = None
) -> FieldMapping:
    return FieldMapping(
        workspace_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        target_object="Opportunity",
        field_map=field_map,
        constants=constants or {},
    )


def test_resolve_source_path_handles_dotted_and_missing() -> None:
    source: dict[str, object] = {
        "signal": {"title": "Acme RFP", "fields": {"amount_cents": 50000000}}
    }
    assert services.resolve_source_path(source, "signal.title") == "Acme RFP"
    assert services.resolve_source_path(source, "signal.fields.amount_cents") == 50000000
    assert services.resolve_source_path(source, "signal.missing") is None
    assert services.resolve_source_path(source, "nope.deep") is None


def test_apply_field_mapping_maps_and_includes_constants() -> None:
    mapping = _mapping(
        {"Name": "signal.title", "Amount": "signal.fields.amount_cents"},
        {"StageName": "Prospecting"},
    )
    source: dict[str, object] = {
        "signal": {"title": "Acme RFP", "fields": {"amount_cents": 50000000}}
    }
    payload = services.apply_field_mapping(mapping, source)
    assert payload == {
        "StageName": "Prospecting",
        "Name": "Acme RFP",
        "Amount": 50000000,
    }


def test_apply_field_mapping_omits_missing_source_values() -> None:
    mapping = _mapping({"Name": "signal.title", "Amount": "signal.fields.amount_cents"})
    source: dict[str, object] = {"signal": {"title": "Acme RFP"}}  # no amount
    payload = services.apply_field_mapping(mapping, source)
    assert payload == {"Name": "Acme RFP"}
    assert "Amount" not in payload


def test_apply_field_mapping_passthrough_when_no_mapping() -> None:
    source: dict[str, object] = {"Name": "Direct", "Amount": 100}
    assert services.apply_field_mapping(None, source) == source
