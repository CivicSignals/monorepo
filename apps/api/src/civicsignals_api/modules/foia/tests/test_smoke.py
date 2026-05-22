"""Smoke tests: the foia module imports and exposes a router with the correct routes."""

from __future__ import annotations

from civicsignals_api.modules.foia import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/foia"


def test_router_has_template_routes() -> None:
    paths = {r.path for r in routes.router.routes}  # type: ignore[attr-defined]
    assert "/foia/templates" in paths
    assert "/foia/templates/{jurisdiction}" in paths
    assert "/foia/templates/{jurisdiction}/render" in paths


def test_router_has_request_routes() -> None:
    paths = {r.path for r in routes.router.routes}  # type: ignore[attr-defined]
    assert "/foia/requests" in paths
    assert "/foia/requests/{request_id}" in paths
    assert "/foia/requests/{request_id}/transition" in paths
    assert "/foia/requests/{request_id}/events" in paths
