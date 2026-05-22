"""Smoke test: the auth module imports and exposes a router."""

from civicsignals_api.modules.auth import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/auth"
