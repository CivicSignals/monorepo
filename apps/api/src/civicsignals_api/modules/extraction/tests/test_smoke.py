"""Smoke test: the extraction module imports and exposes a router."""
from civicsignals_api.modules.extraction import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/extraction"
