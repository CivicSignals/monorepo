"""Tests for the FOIA template library (M1).

Coverage:
- Template files load and pass schema validation (all 8 templates).
- list_templates() returns all templates in jurisdiction-sorted order.
- get_template() returns the correct template; raises on missing jurisdiction.
- render_template() correctly substitutes all required + optional placeholders.
- render_template() raises MissingPlaceholderError on missing required vars.
- Optional placeholders (requester_phone, requester_organization, records_officer_name)
  may be omitted; they default to empty string in rendered output.
- fee_waiver_language (template-owned) is rendered against caller context so its
  internal placeholders (e.g. {fee_waiver_basis}) are fully resolved.
- No unresolved {placeholder} tokens remain in the rendered body.
- Each template has deadline_days, statute, and placeholders fields.
- HTTP routes: list / get / render endpoints return correct shapes and errors.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from civicsignals_api.modules.foia import services
from civicsignals_api.modules.foia.routes import router
from civicsignals_api.problems import install_problem_handlers

# ---------------------------------------------------------------------------
# Minimal FastAPI app for route testing (problem handlers installed so RFC 7807
# errors are rendered correctly rather than propagating as 500s).
# ---------------------------------------------------------------------------

_app = FastAPI()
_app.include_router(router, prefix="/api/v1")
install_problem_handlers(_app)
_client = TestClient(_app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Full render context (all known placeholders)
# ---------------------------------------------------------------------------

FULL_CONTEXT = {
    "requester_name": "Jane Doe",
    "requester_address": "123 Main St, Springfield, IL 62701",
    "requester_email": "jane.doe@example.org",
    "requester_phone": "+1-217-555-0100",
    "requester_organization": "Transparency Now",
    "entity_name": "Springfield School District 186",
    "records_officer_name": "FOIA Officer",
    "records_description": "All contracts with technology vendors exceeding $10,000 from 2022-01-01 to 2024-12-31.",
    "date": "2025-05-22",
    "fee_waiver_basis": "non-profit news organization",
}

# Minimal required-only context (optional placeholders omitted).
REQUIRED_CONTEXT = {
    k: v for k, v in FULL_CONTEXT.items() if k not in services.OPTIONAL_PLACEHOLDERS
}

_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


def _unresolved_placeholders(text: str) -> list[str]:
    """Return any remaining {placeholder} tokens found in *text*."""
    return _PLACEHOLDER_RE.findall(text)


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


class TestTemplateRegistry:
    """The registry loads all template files at import time."""

    def test_registry_not_empty(self) -> None:
        templates = services.list_templates()
        assert len(templates) >= 8, "Expected at least 8 templates in the library"

    def test_all_templates_are_draft(self) -> None:
        for tmpl in services.list_templates():
            assert tmpl.status == "draft", (
                f"Template {tmpl.jurisdiction!r} has status {tmpl.status!r}; "
                "all templates must be 'draft' pending counsel review"
            )

    def test_sorted_by_jurisdiction(self) -> None:
        templates = services.list_templates()
        jurisdictions = [t.jurisdiction for t in templates]
        assert jurisdictions == sorted(jurisdictions)

    def test_each_template_has_required_fields(self) -> None:
        for tmpl in services.list_templates():
            assert tmpl.jurisdiction, f"{tmpl.jurisdiction_name}: missing jurisdiction code"
            assert tmpl.statute, f"{tmpl.jurisdiction}: missing statute citation"
            assert isinstance(tmpl.deadline_days, int), (
                f"{tmpl.jurisdiction}: deadline_days must be int, got {type(tmpl.deadline_days)}"
            )
            assert tmpl.deadline_note, f"{tmpl.jurisdiction}: missing deadline_note"
            assert tmpl.body, f"{tmpl.jurisdiction}: missing body"
            assert isinstance(tmpl.placeholders, list), (
                f"{tmpl.jurisdiction}: placeholders must be a list"
            )
            assert len(tmpl.placeholders) > 0, f"{tmpl.jurisdiction}: placeholders list is empty"

    def test_co_cora_jurisdiction_name_no_duplicate(self) -> None:
        """Regression: CO-CORA should not have 'Colorado Colorado' typo."""
        tmpl = services.get_template("CO-CORA")
        assert "Colorado Colorado" not in tmpl.jurisdiction_name


class TestGetTemplate:
    def test_get_federal_foia(self) -> None:
        tmpl = services.get_template("US-FOIA")
        assert tmpl.jurisdiction == "US-FOIA"
        assert tmpl.state is None  # federal has no state

    def test_get_ca_pra(self) -> None:
        tmpl = services.get_template("CA-PRA")
        assert tmpl.state == "CA"
        assert "7920" in tmpl.statute  # Cal. Gov. Code § 7920.000

    def test_get_tx_pia(self) -> None:
        tmpl = services.get_template("TX-PIA")
        assert tmpl.state == "TX"
        assert tmpl.deadline_days == 10

    def test_get_ny_foil(self) -> None:
        tmpl = services.get_template("NY-FOIL")
        assert tmpl.state == "NY"
        assert tmpl.deadline_days == 5

    def test_get_fl_pra(self) -> None:
        tmpl = services.get_template("FL-PRA")
        assert tmpl.state == "FL"
        # Florida has no fixed deadline
        assert tmpl.deadline_days == 0

    def test_get_wa_pra(self) -> None:
        tmpl = services.get_template("WA-PRA")
        assert tmpl.state == "WA"
        assert tmpl.deadline_days == 5

    def test_get_il_foia(self) -> None:
        tmpl = services.get_template("IL-FOIA")
        assert tmpl.state == "IL"
        assert tmpl.deadline_days == 5

    def test_get_co_cora(self) -> None:
        tmpl = services.get_template("CO-CORA")
        assert tmpl.state == "CO"
        assert tmpl.deadline_days == 3

    def test_unknown_jurisdiction_raises(self) -> None:
        with pytest.raises(services.TemplateNotFoundError):
            services.get_template("ZZ-FAKE")


class TestRenderTemplate:
    def test_render_federal_full_context(self) -> None:
        rendered = services.render_template("US-FOIA", FULL_CONTEXT)
        assert "Jane Doe" in rendered
        assert "Springfield School District 186" in rendered
        assert "2025-05-22" in rendered
        # Ensure no unresolved placeholders remain
        assert "{requester_name}" not in rendered
        assert "{entity_name}" not in rendered
        assert _unresolved_placeholders(rendered) == [], (
            f"Unresolved placeholders in US-FOIA render: {_unresolved_placeholders(rendered)}"
        )

    def test_render_ca_pra_full_context(self) -> None:
        rendered = services.render_template("CA-PRA", FULL_CONTEXT)
        assert "Jane Doe" in rendered
        assert _unresolved_placeholders(rendered) == [], (
            f"Unresolved placeholders in CA-PRA render: {_unresolved_placeholders(rendered)}"
        )

    def test_render_fee_waiver_language_fully_substituted(self) -> None:
        """fee_waiver_language is a template-owned field and must be fully rendered.

        Some templates include {fee_waiver_basis} inside fee_waiver_language.
        This verifies that the renderer resolves nested placeholders.
        """
        for tmpl in services.list_templates():
            rendered = services.render_template(tmpl.jurisdiction, FULL_CONTEXT)
            assert "{fee_waiver_basis}" not in rendered, (
                f"Unresolved {{fee_waiver_basis}} in {tmpl.jurisdiction!r} render"
            )
            assert "{fee_waiver_language}" not in rendered, (
                f"{{fee_waiver_language}} was not expanded in {tmpl.jurisdiction!r} render"
            )

    def test_render_optional_placeholders_can_be_omitted(self) -> None:
        """Optional placeholders default to empty string without raising errors."""
        rendered = services.render_template("US-FOIA", REQUIRED_CONTEXT)
        assert isinstance(rendered, str)
        assert len(rendered) > 100

    def test_render_missing_required_placeholder_raises(self) -> None:
        ctx = {k: v for k, v in FULL_CONTEXT.items() if k != "requester_name"}
        with pytest.raises(services.MissingPlaceholderError) as exc_info:
            services.render_template("US-FOIA", ctx)
        assert "requester_name" in exc_info.value.missing

    def test_render_missing_multiple_placeholders(self) -> None:
        ctx: dict[str, str] = {}
        with pytest.raises(services.MissingPlaceholderError) as exc_info:
            services.render_template("US-FOIA", ctx)
        # Multiple missing vars should all be reported
        assert len(exc_info.value.missing) > 1

    def test_render_extra_context_keys_ignored(self) -> None:
        ctx = {**FULL_CONTEXT, "some_extra_key": "ignored_value"}
        rendered = services.render_template("US-FOIA", ctx)
        assert "ignored_value" not in rendered

    def test_render_unknown_jurisdiction_raises(self) -> None:
        with pytest.raises(services.TemplateNotFoundError):
            services.render_template("ZZ-FAKE", FULL_CONTEXT)

    def test_rendered_body_is_string(self) -> None:
        rendered = services.render_template("US-FOIA", FULL_CONTEXT)
        assert isinstance(rendered, str)
        assert len(rendered) > 100  # sanity: non-trivial output


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


class TestListTemplatesRoute:
    def test_returns_200(self) -> None:
        resp = _client.get("/api/v1/foia/templates")
        assert resp.status_code == 200

    def test_response_shape(self) -> None:
        resp = _client.get("/api/v1/foia/templates")
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == len(data["items"])

    def test_at_least_8_templates(self) -> None:
        resp = _client.get("/api/v1/foia/templates")
        data = resp.json()
        assert data["total"] >= 8

    def test_each_item_has_required_fields(self) -> None:
        resp = _client.get("/api/v1/foia/templates")
        for item in resp.json()["items"]:
            assert "jurisdiction" in item
            assert "statute" in item
            assert "deadline_days" in item
            assert "status" in item
            assert item["status"] == "draft"
            assert "placeholders" in item
            assert isinstance(item["placeholders"], list)


class TestGetTemplateRoute:
    def test_returns_federal_foia(self) -> None:
        resp = _client.get("/api/v1/foia/templates/US-FOIA")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jurisdiction"] == "US-FOIA"
        assert data["state"] is None

    def test_not_found_returns_404_problem(self) -> None:
        resp = _client.get("/api/v1/foia/templates/ZZ-FAKE")
        assert resp.status_code == 404
        data = resp.json()
        assert data["status"] == 404
        assert data["title"] == "FOIA template not found"

    def test_ca_pra_deadline(self) -> None:
        resp = _client.get("/api/v1/foia/templates/CA-PRA")
        assert resp.status_code == 200
        assert resp.json()["deadline_days"] == 10


class TestRenderTemplateRoute:
    def test_render_returns_200(self) -> None:
        resp = _client.post(
            "/api/v1/foia/templates/US-FOIA/render",
            json={"context": FULL_CONTEXT},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jurisdiction"] == "US-FOIA"
        assert "Jane Doe" in data["rendered_body"]

    def test_render_missing_placeholder_returns_422(self) -> None:
        ctx = {k: v for k, v in FULL_CONTEXT.items() if k != "requester_name"}
        resp = _client.post(
            "/api/v1/foia/templates/US-FOIA/render",
            json={"context": ctx},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["status"] == 422
        assert "errors" in data

    def test_render_unknown_jurisdiction_returns_404(self) -> None:
        resp = _client.post(
            "/api/v1/foia/templates/ZZ-FAKE/render",
            json={"context": FULL_CONTEXT},
        )
        assert resp.status_code == 404

    def test_render_all_templates_with_full_context_no_unresolved(self) -> None:
        """Smoke test: every template renders with no unresolved {placeholder} tokens."""
        for tmpl in services.list_templates():
            resp = _client.post(
                f"/api/v1/foia/templates/{tmpl.jurisdiction}/render",
                json={"context": FULL_CONTEXT},
            )
            assert resp.status_code == 200, (
                f"Template {tmpl.jurisdiction!r} failed to render: {resp.text}"
            )
            rendered_body = resp.json()["rendered_body"]
            leftover = _unresolved_placeholders(rendered_body)
            assert leftover == [], (
                f"Template {tmpl.jurisdiction!r} has unresolved placeholders: {leftover}"
            )

    def test_render_optional_placeholders_omitted_returns_200(self) -> None:
        """Optional placeholders may be absent from context without triggering 422."""
        resp = _client.post(
            "/api/v1/foia/templates/US-FOIA/render",
            json={"context": REQUIRED_CONTEXT},
        )
        assert resp.status_code == 200
