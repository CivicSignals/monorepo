"""Smoke test: the accounts module imports and exposes a router."""
from civicsignals_api.modules.accounts import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/accounts"
