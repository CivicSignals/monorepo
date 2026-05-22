"""Smoke test: the entities module imports and exposes a router."""
from civicsignals_api.modules.entities import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/entities"
