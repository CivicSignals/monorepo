"""Thin httpx client over the CivicSignals REST API (doc 06 §5).

Conventions: versioned ``/api/v1``, bearer auth, cursor pagination, RFC 7807
``application/problem+json`` errors, ``X-Workspace-Id`` scoping (doc 06 §6).
"""

from __future__ import annotations

from typing import Any

import httpx


class CivicSignalsError(Exception):
    def __init__(self, status_code: int, problem: Any) -> None:
        super().__init__(f"CivicSignals API error {status_code}")
        self.status_code = status_code
        self.problem = problem


class CivicSignalsClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if workspace_id:
            headers["X-Workspace-Id"] = workspace_id
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=30.0)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.is_error:
            try:
                problem = response.json()
            except ValueError:
                problem = {"title": response.reason_phrase}
            raise CivicSignalsError(response.status_code, problem)
        return response.json()

    def close(self) -> None:
        self._client.close()
