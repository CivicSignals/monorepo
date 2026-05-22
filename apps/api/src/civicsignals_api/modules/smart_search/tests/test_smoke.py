"""Smoke test: the smart_search module imports and exposes a router."""
from civicsignals_api.modules.smart_search import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/smart-search"
