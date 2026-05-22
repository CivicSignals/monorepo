"""Smoke test: the ingestion module imports and exposes a router."""

from civicsignals_api.modules.ingestion import routes


def test_router_is_mounted() -> None:
    assert routes.router.prefix == "/ingestion"
