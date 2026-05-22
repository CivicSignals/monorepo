"""``http_static`` — generic HTTP fetcher (doc 18 §1 cat. C, §5 wave 1 #1; D6).

The default fetch path for static, crawlable HTML (most procurement portals,
agency pages, news articles reached via RSS). It is the **non-JS** counterpart to
D2's ``http_browser`` (which renders JS-heavy SPAs and lands in wave 4).

The connector wires an :class:`HttpxFetcher` into the recipe runner. Crucially it
does **not** check robots.txt or apply the politeness window itself — the runner
does, for every fetcher uniformly (doc 18 §2.2), so the static path can never
accidentally bypass the legal posture. On top of that baseline the fetcher adds
what's source-type specific: a request timeout, **retries with exponential
backoff** on transient HTTP 5xx / network errors (tenacity), a bounded redirect
cap, and **conditional GET** (``If-None-Match`` / ``If-Modified-Since``) when a
prior ETag / Last-Modified is known for the URL (doc 18 §2.2 step 3) so unchanged
content returns a cheap ``304``.

``robots_txt`` is a plain HTTP GET of ``/robots.txt`` — the runner consults it
once per host and caches the result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from civicsignals_api.modules.recipes.services import Fetcher, Recipe

from .base import Connector, register


class HttpStaticConfig(BaseModel):
    """Per-recipe ``connector_config.http_static`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = 30.0
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    conditional_get: bool = True


class _TransientFetchError(Exception):
    """Wraps a retryable fetch failure (HTTP 5xx or a network/timeout error).

    Retried by tenacity; if every attempt fails the *last* underlying error is
    re-raised so the caller sees the real cause (not this wrapper).
    """

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class HttpxFetcher:
    """Static-HTTP :class:`Fetcher` for the recipe runner (D6).

    Conforms to the runner's ``Fetcher`` Protocol: synchronous
    ``fetch(url, *, user_agent, max_redirects) -> (status, body, headers)`` and
    ``robots_txt``. Retries transient failures with exponential backoff and emits
    conditional-GET headers from a small in-memory validator cache keyed by URL —
    so a multi-page crawl that re-fetches a listing it has seen before pays a
    ``304`` instead of re-downloading (doc 18 §2.2 step 3). The validator cache is
    per-fetcher-instance; durable cross-run conditional GET is layered on by the
    ingest worker via the stored ``ingestion_raw_document`` ETag (D3/D4) — out of
    scope for the connector itself.

    A 304 is surfaced to the runner as ``(304, "", headers)`` — i.e. "no change";
    the runner's content hash of the empty body dedupes naturally and storage (D3)
    short-circuits.
    """

    def __init__(self, config: HttpStaticConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        # Injected client (tests pass a httpx.Client backed by MockTransport); the
        # default constructs one lazily so importing this module never opens a
        # connection pool.
        self._client = client
        self._owns_client = client is None
        # url -> (etag, last_modified) learned from prior responses, for conditional GET.
        self._validators: dict[str, tuple[str | None, str | None]] = {}

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._config.timeout_seconds)
        return self._client

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        """Fetch ``url`` with retries/backoff + conditional GET. Never parses."""
        headers: dict[str, str] = {"User-Agent": user_agent}
        if self._config.conditional_get:
            etag, last_modified = self._validators.get(url, (None, None))
            if etag is not None:
                headers["If-None-Match"] = etag
            if last_modified is not None:
                headers["If-Modified-Since"] = last_modified

        response = self._request_with_retries(url, headers=headers, max_redirects=max_redirects)
        resp_headers = {k.lower(): v for k, v in response.headers.items()}

        # Remember validators for the next conditional GET of this URL. A 304 keeps
        # the prior validators (the server may not re-send them).
        if self._config.conditional_get and response.status_code != 304:
            self._validators[url] = (
                resp_headers.get("etag"),
                resp_headers.get("last-modified"),
            )

        if response.status_code == 304:
            return 304, "", resp_headers
        return response.status_code, response.text, resp_headers

    def _request_with_retries(
        self, url: str, *, headers: Mapping[str, str], max_redirects: int
    ) -> httpx.Response:
        client = self._get_client()
        retrying = Retrying(
            stop=stop_after_attempt(self._config.max_attempts),
            wait=wait_exponential(multiplier=self._config.backoff_seconds),
            retry=retry_if_exception_type(_TransientFetchError),
            reraise=True,
        )
        try:
            for attempt in retrying:
                with attempt:
                    try:
                        response = client.get(
                            url,
                            headers=dict(headers),
                            follow_redirects=True,
                            extensions={"max_redirects": max_redirects},
                        )
                    except httpx.HTTPError as exc:
                        # Connection/timeout/redirect-loop errors are transient.
                        raise _TransientFetchError(exc) from exc
                    if response.status_code >= 500:
                        # 5xx is transient (doc 18 §4 "HTTP 5xx (transient)"): retry.
                        raise _TransientFetchError(
                            httpx.HTTPStatusError(
                                f"server error {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                        )
                    return response
        except _TransientFetchError as exc:
            # tenacity reraise=True surfaces our wrapper; unwrap to the real cause.
            raise exc.cause from exc
        raise AssertionError("unreachable: retrying loop always returns or raises")

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        """Fetch ``<scheme>://<host>/robots.txt`` (plain GET); ``None`` on any miss.

        A missing/erroring robots.txt is permissive by convention (the runner only
        blocks on an explicit, parseable disallow — doc 18 §2.2).
        """
        parsed = urlparse(url)
        robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        try:
            response = self._get_client().get(
                robots_url,
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return response.text

    def close(self) -> None:
        """Close the owned httpx client (no-op for an injected one)."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> HttpxFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@register
class HttpStaticConnector(Connector):
    """Generic static-HTML connector (doc 18 §1 cat. C, §5 wave 1 #1)."""

    name: ClassVar[str] = "http_static"
    config_model: ClassVar[type[BaseModel] | None] = HttpStaticConfig

    def build_fetcher(self) -> Fetcher:
        config = self.config
        assert isinstance(config, HttpStaticConfig)  # parse_config guarantees this
        return HttpxFetcher(config)

    # discover() is the default pass-through (one pointer per seed URL) — exactly
    # right for static HTML: the recipe seeds the index/listing pages.


def make_static_fetcher(recipe: Recipe) -> HttpxFetcher:
    """Build the :class:`HttpxFetcher` for a recipe (used by the bulk_download path)."""
    connector = HttpStaticConnector(recipe)
    config = connector.config
    assert isinstance(config, HttpStaticConfig)
    return HttpxFetcher(config)


__all__ = [
    "HttpStaticConfig",
    "HttpStaticConnector",
    "HttpxFetcher",
    "make_static_fetcher",
]
