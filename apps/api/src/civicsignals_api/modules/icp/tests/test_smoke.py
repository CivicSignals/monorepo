"""Smoke test: the icp module imports and exposes a router."""

from civicsignals_api.modules.icp import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/icp"
