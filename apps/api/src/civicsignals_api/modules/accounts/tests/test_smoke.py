"""Smoke test: the accounts module imports and exposes its routers."""

from civicsignals_api.modules.accounts import routes


def test_router_mounts_accounts_and_workspaces() -> None:
    # The module router aggregates the /accounts namespace and the top-level
    # /workspaces surface (B5, doc 08 §2). Both must be reachable through it.
    paths = {getattr(route, "path", "") for route in routes.router.routes}
    assert any(path.startswith("/workspaces") for path in paths)
    assert routes.accounts_router.prefix == "/accounts"
    assert routes.workspaces_router.prefix == "/workspaces"
