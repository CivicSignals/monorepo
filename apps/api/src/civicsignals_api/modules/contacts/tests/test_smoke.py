"""Smoke test: the contacts module imports and exposes a router."""
from civicsignals_api.modules.contacts import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/contacts"
