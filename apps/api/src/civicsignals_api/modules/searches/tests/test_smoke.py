"""Smoke test: the searches module imports and exposes a router."""

from civicsignals_api.modules.searches import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/searches"
