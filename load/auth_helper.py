"""Auth helper for CivicSignals Locust load tests.

Obtains a bearer token (signup/login via B1) and X-Workspace-Id (create
workspace via B5) so authenticated scenarios work. Credentials / config
are taken from environment variables.

Environment variables
---------------------
CS_TEST_EMAIL      Base email for virtual users. ``{n}@`` prefixed per user.
                   Default: ``locust{n}@loadtest.invalid``
CS_TEST_PASSWORD   Shared password for all VUs. Default: ``Locust$ecret99!``
CS_BASE_URL        Overrides host passed on the CLI (useful for programmatic use).
CS_SIGNUP_PREFIX   String prefix for generated emails. Default: ``locust``
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Config knobs
# ---------------------------------------------------------------------------

_PASSWORD = os.getenv("CS_TEST_PASSWORD", "Locust$ecret99!")
_EMAIL_PREFIX = os.getenv("CS_SIGNUP_PREFIX", "locust")
_TOKEN_CACHE: dict[str, "AuthContext"] = {}
_CACHE_LOCK = threading.Lock()


@dataclass
class AuthContext:
    """Bearer token + workspace id for one virtual user."""

    access_token: str
    workspace_id: str
    user_id: str
    email: str
    headers: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "X-Workspace-Id": self.workspace_id,
            "Content-Type": "application/json",
        }


def _user_email(user_index: int) -> str:
    """Deterministic, unique email per virtual user index."""
    base = os.getenv("CS_TEST_EMAIL", "")
    if base:
        # Allow a single static email for single-user smoke runs.
        return base
    return f"{_EMAIL_PREFIX}{user_index}@loadtest.invalid"


def get_or_create_auth(
    client: Any,  # locust.clients.HttpSession or requests.Session
    user_index: int,
    *,
    workspace_name: str | None = None,
) -> AuthContext:
    """Return cached AuthContext for *user_index*, creating one if needed.

    The first call signs up the virtual user and creates a workspace; subsequent
    calls return the cached token so the costly signup/login path is exercised
    only once per VU.  Thread-safe via ``_CACHE_LOCK``.
    """
    cache_key = f"{user_index}"
    with _CACHE_LOCK:
        if cache_key in _TOKEN_CACHE:
            return _TOKEN_CACHE[cache_key]

    email = _user_email(user_index)
    ctx = _signup_or_login(client, email, workspace_name=workspace_name or f"loadtest-ws-{user_index}")
    with _CACHE_LOCK:
        _TOKEN_CACHE[cache_key] = ctx
    return ctx


def _signup_or_login(
    client: Any,
    email: str,
    *,
    workspace_name: str,
) -> AuthContext:
    """Attempt signup; fall back to login if the account already exists."""
    signup_payload = {
        "email": email,
        "password": _PASSWORD,
        "name": f"Load Test User <{email}>",
    }
    resp = client.post(
        "/api/v1/auth/signup",
        json=signup_payload,
        name="[auth] signup",
    )

    access_token: str
    user_id: str

    if resp.status_code in (200, 201):
        data = resp.json()
        access_token = data["tokens"]["access_token"]
        user_id = data["user"]["id"]
    elif resp.status_code == 409:
        # Account already exists — log in instead.
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": _PASSWORD},
            name="[auth] login",
        )
        login_resp.raise_for_status()
        data = login_resp.json()
        access_token = data["tokens"]["access_token"]
        user_id = data["user"]["id"]
    else:
        resp.raise_for_status()  # unexpected — surface the error
        raise RuntimeError(f"Unexpected signup status {resp.status_code}")

    # Create or reuse a workspace.
    ws_id = _ensure_workspace(client, access_token, workspace_name)
    return AuthContext(
        access_token=access_token,
        workspace_id=ws_id,
        user_id=user_id,
        email=email,
    )


def _ensure_workspace(client: Any, access_token: str, name: str) -> str:
    """Create a workspace; if name is taken, return the existing workspace id."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    resp = client.post(
        "/api/v1/workspaces",
        json={"name": name},
        headers=headers,
        name="[auth] create workspace",
    )
    if resp.status_code in (200, 201):
        return str(resp.json()["id"])
    if resp.status_code == 409:
        # Workspace already exists — list workspaces and find it.
        list_resp = client.get(
            "/api/v1/workspaces",
            headers=headers,
            name="[auth] list workspaces",
        )
        list_resp.raise_for_status()
        workspaces = list_resp.json().get("data", [])
        for ws in workspaces:
            if ws.get("name") == name:
                return str(ws["id"])
        # Fall back to the first workspace if name match fails.
        if workspaces:
            return str(workspaces[0]["id"])
        raise RuntimeError("Could not resolve workspace id after 409")
    resp.raise_for_status()
    raise RuntimeError(f"Unexpected workspace status {resp.status_code}")


def random_idempotency_key() -> str:
    """UUIDv4-based idempotency key for push endpoints."""
    return str(uuid.uuid4())
