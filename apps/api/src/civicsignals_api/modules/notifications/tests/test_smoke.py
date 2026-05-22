"""Smoke test: the notifications module imports and exposes a router."""

from civicsignals_api.modules.notifications import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/notifications"
