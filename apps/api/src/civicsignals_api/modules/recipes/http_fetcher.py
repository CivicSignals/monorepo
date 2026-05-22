"""A minimal httpx-backed :class:`Fetcher` for the authoring tooling (TODO D5).

The production HTTP connector (``http_static``) lands in D6 with conditional
GET, content hashing, retries, and S3 storage. This is the lightweight fetcher
the *authoring* tooling needs **today** so the CLI ``preview --url`` and the
staff ``POST /recipes/preview`` (url mode) can do a real dry-run fetch.

It satisfies the runner's :class:`Fetcher` protocol exactly, so the runner's
own ``fetch`` step applies the recipe's robots.txt + politeness posture
(doc 18 §2.2) unchanged. ``fetch`` never parses (doc 18 §2.2). robots.txt is
fetched from the host root; a 404/missing file is the permissive convention
(returns ``None``).
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from .runner import FetchFailedError

DEFAULT_TIMEOUT_SECONDS = 20.0


class HttpxFetcher:
    """Real-network :class:`Fetcher` for recipe authoring previews.

    Stateless aside from a shared :class:`httpx.Client`; create one per preview
    call (or pass an explicit client for testing). Use as a context manager to
    close the underlying connection pool.
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def __enter__(self) -> HttpxFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        """Fetch ``url`` and return ``(status, body, headers)``. Never parses.

        ``max_redirects`` is honored by httpx's redirect cap; we follow
        redirects (the runner already enforces robots/politeness before us).
        """
        try:
            self._client.max_redirects = max_redirects
            response = self._client.get(
                url,
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise FetchFailedError(url, str(exc)) from exc
        return response.status_code, response.text, dict(response.headers)

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        """Fetch ``<scheme>://<host>/robots.txt``; ``None`` on any non-2xx/3xx response.

        Both 4xx (not found / forbidden) and 5xx (server error) return ``None``,
        which the runner treats as permissive (no robots.txt ⇒ allow). This matches
        the widely-used convention: an unreachable robots.txt should not block
        crawling, and a 5xx is typically transient rather than a hard disallow.
        """
        robots_url = urljoin(url, "/robots.txt")
        try:
            response = self._client.get(
                robots_url,
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            )
        except httpx.HTTPError:
            # Treat an unreachable robots.txt as permissive (no file => allow).
            return None
        if response.status_code >= 400:
            return None
        return response.text


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "HttpxFetcher"]
