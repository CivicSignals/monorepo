"""``rest_api_pager`` — REST/JSON API client (doc 18 §1 cat. A, §5 wave 1 #4; D6).

The source exposes a documented JSON API with stable schemas (Grants.gov,
USAspending, SAM.gov, OpenStates, GDELT). This connector adds the three things a
generic HTTP fetch can't: **pagination** (cursor / offset / page), **auth**
(api-key header/param or bearer token, the secret resolved from the environment —
never committed plaintext), and client-side **rate limiting** plus retry/backoff
on transient 5xx / 429 (doc 18 §4 cat. A).

``discover()`` walks pages forward — bounded by ``max_pages`` so a runaway backfill
can't loop forever (doc 18 §2.1 REST) — and emits **one pointer per page** whose
URL is the concrete request URL for that page. The injected
:class:`RestApiPagerFetcher` then re-serves each page's JSON body to the runner,
which extracts records from it through the normal chain. Walking in ``discover``
(not ``fetch``) keeps ``fetch`` a pure "retrieve one URL" step and lets the runner
apply politeness between page requests.

The fetcher implements the runner's synchronous ``Fetcher`` Protocol; ``robots_txt``
returns ``None`` (documented APIs are not robots-gated — they grant access via the
key), so the runner does not block API calls.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from civicsignals_api.modules.recipes.services import Clock, Fetcher, SourcePointer

from .base import Connector, ConnectorError, register


class AuthConfig(BaseModel):
    """Auth applied to every request (doc 18 §2.2 step 2)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "api_key", "bearer"] = "none"
    header: str | None = None
    param: str | None = None
    # The literal secret, or ``env:NAME`` to read it from the environment.
    value: str | None = None


class PaginationConfig(BaseModel):
    """How to advance through pages (doc 18 §2.1 REST)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["cursor", "offset", "page"]
    items_path: str = "items"
    cursor_path: str | None = None
    cursor_param: str = "cursor"
    offset_param: str = "offset"
    page_param: str = "page"
    limit_param: str = "limit"
    page_size: int = 50
    start_page: int = 1
    max_pages: int = 100


class RestApiPagerConfig(BaseModel):
    """Per-recipe ``connector_config.rest_api_pager`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    pagination: PaginationConfig
    auth: AuthConfig = AuthConfig()
    rate_limit_per_minute: int | None = None


def _resolve_secret(value: str | None) -> str | None:
    """Resolve a config secret: ``env:NAME`` reads the env var, else literal.

    Keeps real keys out of committed YAML (doc 18 §3.5 / §4 cat. A "auth/key
    expiry"); a missing env var raises so a misconfigured recipe fails loudly
    rather than sending an empty key.
    """
    if value is None:
        return None
    if value.startswith("env:"):
        name = value[len("env:") :]
        resolved = os.environ.get(name)
        if resolved is None:
            raise ConnectorError(f"auth secret env var {name!r} is not set")
        return resolved
    return value


def _dotted_get(obj: object, path: str) -> object:
    """Read a dotted ``a.b.c`` path out of a nested mapping; ``None`` if absent."""
    cur: object = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class _TransientApiError(Exception):
    """Retryable API failure: HTTP 5xx / 429, or a network/timeout error."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class RestApiPagerFetcher:
    """REST/JSON :class:`Fetcher`: auth + rate-limit + retry, page-at-a-time.

    Conforms to the runner's ``Fetcher`` Protocol. ``fetch(url, …)`` issues one
    authenticated GET (the URL already carries the page's pagination params, built
    by the connector's ``discover``) and returns the raw JSON body as text.
    Client-side rate limiting spaces requests to ``rate_limit_per_minute``; the
    runner's politeness window applies on top. Transient errors (5xx / 429 /
    network) are retried with exponential backoff (doc 18 §4 cat. A).
    """

    def __init__(
        self,
        config: RestApiPagerConfig,
        *,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        self._clock = clock
        self._last_request_at: float | None = None
        # Resolve the secret once (raises early on a missing env var).
        self._secret = _resolve_secret(config.auth.value)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self._client

    def _now(self) -> float:
        return self._clock.monotonic() if self._clock is not None else time.monotonic()

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self._clock is not None:
            self._clock.sleep(seconds)
        else:  # pragma: no cover - real sleep
            time.sleep(seconds)

    def _apply_rate_limit(self) -> None:
        rpm = self._config.rate_limit_per_minute
        if rpm is None:
            return
        min_interval = 60.0 / rpm
        if self._last_request_at is not None:
            elapsed = self._now() - self._last_request_at
            self._sleep(min_interval - elapsed)

    def _auth_headers(self) -> dict[str, str]:
        auth = self._config.auth
        headers: dict[str, str] = {"Accept": "application/json"}
        if auth.mode == "bearer" and self._secret:
            headers["Authorization"] = f"Bearer {self._secret}"
        elif auth.mode == "api_key" and auth.header and self._secret:
            headers[auth.header] = self._secret
        return headers

    def auth_query(self) -> dict[str, str]:
        """Auth carried as a query param (``api_key`` mode with ``param`` set)."""
        auth = self._config.auth
        if auth.mode == "api_key" and auth.param and self._secret:
            return {auth.param: self._secret}
        return {}

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        """GET one page URL with auth + rate limiting; return its JSON body as text."""
        self._apply_rate_limit()
        headers = self._auth_headers()
        headers["User-Agent"] = user_agent

        response = self._request_with_retries(url, headers=headers, max_redirects=max_redirects)
        self._last_request_at = self._now()
        resp_headers = {k.lower(): v for k, v in response.headers.items()}
        return response.status_code, response.text, resp_headers

    def _request_with_retries(
        self, url: str, *, headers: dict[str, str], max_redirects: int
    ) -> httpx.Response:
        client = self._get_client()
        client.max_redirects = max_redirects
        retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1.0),
            retry=retry_if_exception_type(_TransientApiError),
            reraise=True,
        )
        try:
            for attempt in retrying:
                with attempt:
                    try:
                        response = client.get(
                            url,
                            headers=headers,
                            follow_redirects=True,
                        )
                    except httpx.HTTPError as exc:
                        raise _TransientApiError(exc) from exc
                    # 429 (rate limit) + 5xx (transient) are retryable (doc 18 §4).
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _TransientApiError(
                            httpx.HTTPStatusError(
                                f"retryable status {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                        )
                    return response
        except _TransientApiError as exc:
            raise exc.cause from exc
        raise AssertionError("unreachable: retrying loop always returns or raises")

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        # Documented APIs are accessed via a key, not robots-gated; returning None
        # is the permissive convention so the runner does not block API calls.
        return None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> RestApiPagerFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _build_page_url(
    base_url: str, pagination: PaginationConfig, *, page_index: int, cursor: str | None
) -> str:
    """Build the concrete request URL for a page, applying pagination params."""
    params: dict[str, str] = {pagination.limit_param: str(pagination.page_size)}
    if pagination.mode == "page":
        params[pagination.page_param] = str(pagination.start_page + page_index)
    elif pagination.mode == "offset":
        params[pagination.offset_param] = str(page_index * pagination.page_size)
    elif pagination.mode == "cursor" and cursor is not None:
        params[pagination.cursor_param] = cursor
    request = httpx.Request("GET", base_url, params=params)
    return str(request.url)


@register
class RestApiPagerConnector(Connector):
    """REST/JSON paginated connector (doc 18 §1 cat. A, §5 wave 1 #4)."""

    name: ClassVar[str] = "rest_api_pager"
    config_model: ClassVar[type[BaseModel] | None] = RestApiPagerConfig

    def build_fetcher(self) -> Fetcher:
        config = self.config
        assert isinstance(config, RestApiPagerConfig)
        return RestApiPagerFetcher(config, clock=self.clock)

    def discover(self, seed_urls: Sequence[str]) -> list[SourcePointer]:
        """Walk pages forward and emit one pointer per page (doc 18 §2.1 REST).

        For cursor mode we must read each page's body to learn the next cursor, so
        ``discover`` issues the requests itself (via the same fetcher), bounded by
        ``max_pages``. For offset/page mode the URLs are computable up front; we
        still stop early at the first empty page so we don't request beyond the
        data. ``seed_urls`` are ignored — the API base + pagination fully determine
        the request set.
        """
        config = self.config
        assert isinstance(config, RestApiPagerConfig)
        pagination = config.pagination

        fetcher = RestApiPagerFetcher(config, clock=self.clock)
        auth_query = fetcher.auth_query()
        pointers: list[SourcePointer] = []
        cursor: str | None = None
        try:
            for page_index in range(pagination.max_pages):
                url = _build_page_url(
                    config.base_url, pagination, page_index=page_index, cursor=cursor
                )
                url = _with_query(url, auth_query)
                status, body, _headers = fetcher.fetch(
                    url,
                    user_agent=self.recipe.fetch.user_agent,
                    max_redirects=self.recipe.fetch.max_redirects,
                )
                if status == 401 or status == 403:
                    raise ConnectorError(
                        f"rest_api_pager auth failed for {config.base_url!r} "
                        f"(HTTP {status}); check the API key (doc 18 §4 cat. A)"
                    )
                payload = _parse_json(body)
                items = _dotted_get(payload, pagination.items_path)
                items_list = items if isinstance(items, list) else []
                if not items_list:
                    break  # no more data: stop walking
                pointers.append(
                    SourcePointer(
                        recipe_id=self.recipe.recipe_id,
                        connector=self.recipe.connector,
                        url=url,
                        hint_metadata={"page_index": page_index, "item_count": len(items_list)},
                    )
                )
                if pagination.mode == "cursor":
                    next_cursor = (
                        _dotted_get(payload, pagination.cursor_path)
                        if pagination.cursor_path
                        else None
                    )
                    if not next_cursor:
                        break  # no next cursor: last page
                    cursor = str(next_cursor)
        finally:
            fetcher.close()
        return pointers


def _with_query(url: str, extra: dict[str, str]) -> str:
    """Merge extra query params into ``url`` (used to append api-key-in-query)."""
    if not extra:
        return url
    request = httpx.Request("GET", url)
    merged = dict(request.url.params)
    merged.update(extra)
    return str(httpx.Request("GET", str(request.url.copy_with(query=None)), params=merged).url)


def _parse_json(body: str) -> Any:
    """Parse a JSON page body; raise a clear :class:`ConnectorError` on garbage."""
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"rest_api_pager response was not valid JSON: {exc}") from exc


__all__ = [
    "AuthConfig",
    "PaginationConfig",
    "RestApiPagerConfig",
    "RestApiPagerConnector",
    "RestApiPagerFetcher",
]
