"""Smoke test: the recipes module imports and exposes a router."""

from civicsignals_api.modules.recipes import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/recipes"
