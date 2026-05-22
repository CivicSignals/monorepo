"""Smoke test: the integrations module imports and exposes a router."""

from civicsignals_api.modules.integrations import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/integrations"
