"""Smoke test: the billing module imports and exposes a router."""

from civicsignals_api.modules.billing import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/billing"
