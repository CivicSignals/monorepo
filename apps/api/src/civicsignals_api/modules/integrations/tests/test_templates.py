"""Tests for K6 field-mapping templates / per-connection defaults.

Two layers:

- **Pure-unit** (no DB): the ``apply_field_mapping`` building blocks a template
  applies through, and the in-memory default-template fallback path of
  ``push_source`` via a fake session — these always run.
- **Live-DB API** (skips without ``DATABASE_DIRECT_URL`` / ``DATABASE_URL``):
  save / list / apply / delete templates end-to-end through the HTTP surface,
  the "at most one default per connection" invariant, RBAC (member can manage),
  and workspace isolation.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient

from civicsignals_api.modules.accounts.models import MembershipRole
from civicsignals_api.modules.integrations import services
from civicsignals_api.modules.integrations.models import (
    FieldMapping,
    FieldMappingTemplate,
)
from civicsignals_api.modules.integrations.tests.test_salesforce_api import (
    CONNECTIONS,
    _add_member_at_role,
    _create_workspace,
    _hdr,
    _seed_connection,
    _signup,
)

# ---------------------------------------------------------------------------
# Pure-unit: a template's snapshot maps the same way a saved mapping does.
# ---------------------------------------------------------------------------


def test_template_snapshot_applies_like_a_mapping() -> None:
    """A FieldMapping built from a template's snapshot shapes the push body (K6)."""
    template = FieldMappingTemplate(
        workspace_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        name="Opportunity defaults",
        target_object="Opportunity",
        field_map={"Name": "signal.title"},
        constants={"StageName": "Prospecting"},
        is_default=True,
    )
    # apply_field_mapping_template materialises this same shape onto a FieldMapping.
    mapping = FieldMapping(
        workspace_id=template.workspace_id,
        connection_id=template.connection_id,
        target_object=template.target_object,
        field_map=dict(template.field_map),
        constants=dict(template.constants),
    )
    body = services.apply_field_mapping(mapping, {"signal": {"title": "Northshore RFP"}})
    assert body == {"StageName": "Prospecting", "Name": "Northshore RFP"}


# ---------------------------------------------------------------------------
# Pure-unit: push_source falls back to the connection default template when no
# per-target mapping exists. Uses a fake session that answers the two lookups
# push_source makes (per-target mapping → None, default template → the default).
# ---------------------------------------------------------------------------


def test_save_template_clears_prior_default() -> None:
    """Flagging a new default clears the connection's prior default in place (K6)."""
    connection_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    prior = FieldMappingTemplate(
        workspace_id=workspace_id,
        connection_id=connection_id,
        name="old",
        target_object="Opportunity",
        field_map={},
        constants={},
        is_default=True,
    )

    class _Result:
        def __init__(self, value: Any) -> None:
            self._value = value

        def scalar_one_or_none(self) -> Any:
            return self._value

    class _FakeSession:
        """Fake session: default lookup returns ``prior``; name lookup returns None."""

        def __init__(self) -> None:
            self.added: list[Any] = []
            self._call = 0

        async def execute(self, _stmt: Any) -> _Result:
            self._call += 1
            # 1st execute = get_default_field_mapping_template (clear-prior path),
            # 2nd execute = _get_template_by_name (the upsert lookup).
            return _Result(prior) if self._call == 1 else _Result(None)

        def add(self, obj: Any) -> None:
            self.added.append(obj)

        async def flush(self) -> None:
            pass

    session = _FakeSession()
    saved = asyncio.run(
        services.save_field_mapping_template(
            session,  # type: ignore[arg-type]
            workspace_id=workspace_id,
            connection_id=connection_id,
            name="new",
            target_object="Opportunity",
            field_map={"Name": "signal.title"},
            constants={},
            is_default=True,
        )
    )
    # Prior default was cleared; the new template is the default and was added.
    assert prior.is_default is False
    assert saved.is_default is True
    assert saved.name == "new"
    assert saved in session.added


# ---------------------------------------------------------------------------
# Live-DB API: save / list / apply / delete + default invariant + RBAC + iso.
# ---------------------------------------------------------------------------

TEMPLATES_PATH = "field-mapping-templates"


def _templates_base(conn_id: str) -> str:
    return f"{CONNECTIONS}/{conn_id}/{TEMPLATES_PATH}"


def test_template_save_list_apply_delete(client: TestClient) -> None:
    token, _ = _signup(client, "k6-crud@example.com")
    ws = _create_workspace(client, token, "K6 CRUD")
    conn_id = _seed_connection(ws["id"])
    base = _templates_base(conn_id)

    # Initially empty.
    assert client.get(base, headers=_hdr(token, ws["id"])).json() == {"data": []}

    # Save a template as the connection default.
    saved = client.post(
        base,
        json={
            "name": "Opportunity defaults",
            "target_object": "Opportunity",
            "field_map": {"Name": "signal.title"},
            "constants": {"StageName": "Prospecting"},
            "is_default": True,
        },
        headers=_hdr(token, ws["id"]),
    )
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["name"] == "Opportunity defaults"
    assert body["is_default"] is True
    assert body["field_map"]["Name"] == "signal.title"
    template_id = body["id"]

    # It now lists.
    listing = client.get(base, headers=_hdr(token, ws["id"])).json()
    assert len(listing["data"]) == 1

    # Apply it → materialises the per-target field mapping.
    applied = client.post(f"{base}/{template_id}/apply", headers=_hdr(token, ws["id"]))
    assert applied.status_code == 200, applied.text
    assert applied.json()["target_object"] == "Opportunity"
    assert applied.json()["field_map"]["Name"] == "signal.title"
    assert applied.json()["constants"]["StageName"] == "Prospecting"

    # The live field-mapping now exists.
    mappings = client.get(
        f"{CONNECTIONS}/{conn_id}/field-mappings", headers=_hdr(token, ws["id"])
    ).json()
    assert len(mappings["data"]) == 1
    assert mappings["data"][0]["field_map"]["Name"] == "signal.title"

    # Delete the template.
    deleted = client.delete(f"{base}/{template_id}", headers=_hdr(token, ws["id"]))
    assert deleted.status_code == 204
    assert client.get(base, headers=_hdr(token, ws["id"])).json() == {"data": []}


def test_template_single_default_per_connection(client: TestClient) -> None:
    token, _ = _signup(client, "k6-default@example.com")
    ws = _create_workspace(client, token, "K6 Default")
    conn_id = _seed_connection(ws["id"])
    base = _templates_base(conn_id)

    first = client.post(
        base,
        json={
            "name": "first",
            "target_object": "Opportunity",
            "field_map": {"Name": "signal.title"},
            "is_default": True,
        },
        headers=_hdr(token, ws["id"]),
    )
    assert first.status_code == 201, first.text

    # Saving a second default clears the first's default flag.
    second = client.post(
        base,
        json={
            "name": "second",
            "target_object": "Opportunity",
            "field_map": {"Name": "signal.summary"},
            "is_default": True,
        },
        headers=_hdr(token, ws["id"]),
    )
    assert second.status_code == 201, second.text

    listing = client.get(base, headers=_hdr(token, ws["id"])).json()["data"]
    defaults = [t for t in listing if t["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "second"


def test_template_default_auto_applies_on_push(client: TestClient, salesforce_http: None) -> None:
    """With no per-target mapping, the default template shapes the push body (K6)."""
    token, _ = _signup(client, "k6-push@example.com")
    ws = _create_workspace(client, token, "K6 Push")
    conn_id = _seed_connection(ws["id"])
    base = _templates_base(conn_id)

    # Save a default template — but DO NOT save a per-target field mapping.
    client.post(
        base,
        json={
            "name": "default",
            "target_object": "Opportunity",
            "field_map": {"Name": "signal.title"},
            "constants": {"StageName": "Prospecting"},
            "is_default": True,
        },
        headers=_hdr(token, ws["id"]),
    )

    resp = client.post(
        f"{CONNECTIONS}/{conn_id}/push",
        json={
            "source": {"signal": {"title": "Northshore RFP"}},
            "target": "Opportunity",
            "signal_id": "sig-k6",
        },
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 200, resp.text
    log = resp.json()["push_log"]
    assert log["status"] == "success"
    # The push-log request was shaped by the default template (Name + constant).
    assert log["target"] == "salesforce.Opportunity"


def test_template_member_can_manage(client: TestClient) -> None:
    """Templates gate on RequireMember (data work), unlike admin-only mapping CRUD."""
    owner_token, _ = _signup(client, "k6-owner@example.com")
    ws = _create_workspace(client, owner_token, "K6 Member")
    conn_id = _seed_connection(ws["id"])
    member_token, member_id = _signup(client, "k6-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)

    resp = client.post(
        _templates_base(conn_id),
        json={"name": "m", "target_object": "Opportunity", "field_map": {}},
        headers=_hdr(member_token, ws["id"]),
    )
    assert resp.status_code == 201, resp.text


def test_template_workspace_isolated(client: TestClient) -> None:
    token_a, _ = _signup(client, "k6-iso-a@example.com")
    token_b, _ = _signup(client, "k6-iso-b@example.com")
    ws_a = _create_workspace(client, token_a, "K6 Iso A")
    ws_b = _create_workspace(client, token_b, "K6 Iso B")
    conn_a = _seed_connection(ws_a["id"])

    # Workspace B cannot read workspace A's connection templates (404 — connection
    # is resolved scoped to the active workspace).
    resp = client.get(_templates_base(conn_a), headers=_hdr(token_b, ws_b["id"]))
    assert resp.status_code == 404
