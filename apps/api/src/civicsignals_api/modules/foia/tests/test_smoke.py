"""Smoke test: the foia module imports and exposes a router."""
from civicsignals_api.modules.foia import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/foia"
