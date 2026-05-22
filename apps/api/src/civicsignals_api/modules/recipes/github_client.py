"""GitHub issue-opener for recipe drift auto-pause (doc 18 §3.2, TODO E7).

When a recipe auto-pauses on drift, we file a GitHub issue with a sanitized
sample of the failing input and the rolling metrics so a human can inspect and
fix or unpause it (doc 18 §3.2). This module is the injectable seam for that:

- :class:`GitHubClient` — the protocol the drift service depends on (one method,
  ``open_issue``). Tests inject a recording fake; production uses the default.
- :class:`HttpxGitHubClient` — the real client, talking to the GitHub REST API
  with the token from settings (no extra SDK dependency — we already ship httpx).
- :class:`NoopGitHubClient` — used when ``GITHUB_TOKEN`` is unset (dev / self-host
  without a token). Drift still pauses the recipe; it just doesn't file an issue.

The selection (real vs no-op) and the override hook live in ``services.py`` next
to the rest of the drift service, mirroring billing's Stripe-client seam.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class GitHubClient(Protocol):
    """The one method the drift service needs: open an issue, return its URL.

    Returns the html URL of the created issue, or ``None`` when the client is a
    no-op (unconfigured) — the caller treats ``None`` as "no issue filed" and does
    not record an idempotency key, so a later tick can retry once configured.
    """

    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None: ...


class NoopGitHubClient:
    """No-op client used when ``GITHUB_TOKEN`` is unset (doc 18 §3.2, E7).

    Auto-pause still happens (the recipe stops running, past signals stay visible);
    we simply skip filing an issue. Returning ``None`` signals "no issue" so the
    drift service does not persist an idempotency key.
    """

    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None:
        return None


class HttpxGitHubClient:
    """Real GitHub REST client backed by httpx (no GitHub SDK dependency).

    POSTs to ``/repos/{repo}/issues`` with a bearer token. ``repo`` is
    ``owner/name``. Raises on a non-2xx response so a transient failure surfaces to
    the caller (which leaves the recipe paused but without a recorded issue — the
    next drift tick retries the file).
    """

    def __init__(self, token: str, repo: str, *, api_base_url: str) -> None:
        self._token = token
        self._repo = repo
        self._api_base_url = api_base_url.rstrip("/")

    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None:
        url = f"{self._api_base_url}/repos/{self._repo}/issues"
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "body": body, "labels": labels},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        html_url = data.get("html_url")
        return str(html_url) if html_url is not None else None
