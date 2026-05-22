"""Smoke test: the admin module imports and exposes a router."""

from civicsignals_api.modules.admin import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/admin"
