"""Smoke test: the pipeline module imports and exposes a router."""

from civicsignals_api.modules.pipeline import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/pipeline"
