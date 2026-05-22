"""Smoke test: the public_feed module imports and exposes a router."""

from civicsignals_api.modules.public_feed import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/public"
