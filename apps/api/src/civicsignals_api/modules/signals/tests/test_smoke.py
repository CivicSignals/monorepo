"""Smoke test: the signals module imports and exposes a router."""
from civicsignals_api.modules.signals import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/signals"
