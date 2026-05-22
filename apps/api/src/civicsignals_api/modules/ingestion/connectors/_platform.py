"""Shared building blocks for the wave-2 platform connectors (doc 18 §1 cat. E,
§5 wave 2; TODO D7).

Wave 2 is the **multi-tenant platforms** — one connector covers thousands of
entities via per-tenant recipes (doc 18 §5). They split into two families that
share machinery captured here so the six connectors stay thin:

* **Open-data / GIS REST APIs** (``socrata``, ``ckan``, ``arcgis_rest``): JSON
  over HTTP. They reuse the wave-1 :class:`RestApiPagerFetcher` to talk to the
  tenant's API in ``discover`` (auth + rate-limit + retry/backoff for free), then
  emit **one pointer per record**. Because the shared recipe runner extracts
  fields with CSS/XPath selectors over an HTML body (doc 18 §3.4), each record is
  projected into a small, deterministic HTML ``<dl>`` fragment by
  :func:`render_record_html` — a stable JSON→HTML view a recipe selects with
  ``dd[data-key='<field>']`` regardless of tenant. The connector's fetcher
  re-serves that fragment from the pointer's ``hint_metadata`` so the runner never
  re-hits the network in ``fetch`` (the listing call already happened in
  ``discover``).

* **Government meeting/agenda platforms** (``boarddocs``, ``granicus_peak``,
  ``civicplus``): static HTML per tenant. They reuse the wave-1
  :class:`HttpxFetcher`; ``discover`` fetches the tenant's index/listing and emits
  one pointer per meeting/agenda **detail page**, which the runner then fetches +
  extracts as ordinary HTML. No JS rendering — these all serve a static
  fallback (doc 18 §5: prefer static; the headless browser is wave 4). Any tenant
  that genuinely requires JS is a per-recipe D9/``http_browser`` decision, noted
  in the recipe's ``legal_notes`` rather than handled here.

Keeping ``discover`` walking the API/listing (not ``fetch``) preserves the
runner's contract: ``fetch`` is a pure "retrieve one URL" step and the runner
applies politeness between pointers (doc 18 §2.1, §2.2).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, Sequence
from html import escape
from typing import Any

import httpx

from civicsignals_api.modules.recipes.services import Fetcher

from .base import ConnectorError

# ----------------------------------------------------------------------------
# JSON record -> HTML projection (open-data / GIS connectors)
# ----------------------------------------------------------------------------
# The recipe runner extracts with CSS/XPath selectors over an HTML body (doc 18
# §3.4). Open-data APIs hand us JSON records, so we project each record into a
# stable HTML fragment: a <dl> whose <dd> carries a `data-key` attribute. A tenant
# recipe then selects a field with `dd[data-key='budget_amount']` independent of
# the JSON's nesting — the projection flattens nested objects to dotted keys. The
# fragment is deterministic (sorted keys), so a golden fixture committed as the
# rendered HTML replays byte-stably through the shared QA-4 harness.

_RECORD_ROOT_CLASS = "platform-record"


def _flatten(obj: object, prefix: str = "") -> dict[str, str]:
    """Flatten a JSON record into ``dotted.key -> string value`` pairs.

    Nested objects recurse with a dotted prefix; lists are joined by ``"; "`` for
    scalars or rendered as their JSON for objects. ``None`` becomes the empty
    string so a missing value is an empty (not absent) field, which the runner
    treats as a selector miss → degraded/dead-letter rather than a crash.
    """
    flat: dict[str, str] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten(value, child))
    elif isinstance(obj, list):
        scalars = [item for item in obj if not isinstance(item, dict | list)]
        if scalars and len(scalars) == len(obj):
            flat[prefix] = "; ".join(_scalar(item) for item in scalars)
        else:
            flat[prefix] = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    else:
        flat[prefix] = _scalar(obj)
    return flat


def _scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_record_html(record: Mapping[str, Any]) -> str:
    """Project one JSON record into a deterministic HTML ``<dl>`` fragment.

    Each (flattened, dotted) key becomes ``<dt>key</dt><dd data-key="key">value
    </dd>``. Keys are sorted so the rendering is stable across runs (golden-fixture
    replay compares byte-for-byte). A tenant recipe selects fields with
    ``dd[data-key='<key>']`` — the connector-agnostic seam between the open-data
    JSON shape and the runner's selector extraction.
    """
    flat = _flatten(dict(record))
    rows = []
    for key in sorted(flat):
        rows.append(
            f"<dt>{escape(key)}</dt>"
            f'<dd data-key="{escape(key, quote=True)}">{escape(flat[key])}</dd>'
        )
    body = "".join(rows)
    return f'<dl class="{_RECORD_ROOT_CLASS}">{body}</dl>'


# ----------------------------------------------------------------------------
# Pointer fetcher: re-serve a body already computed in discover
# ----------------------------------------------------------------------------


class StaticBodyFetcher:
    """A :class:`Fetcher` that returns a body precomputed per pointer URL.

    The open-data connectors already issued the listing/query call in ``discover``
    (so the API round-trip and its pagination/auth/rate-limit happen once); each
    record's HTML projection is stashed keyed by the pointer URL. The runner's
    ``fetch`` step then resolves that URL to its body with **no network call** —
    the per-pointer politeness window the runner applies is therefore effectively
    free, and a re-fetch is impossible to get wrong. ``robots_txt`` returns
    ``None`` (API access is granted by key, not robots-gated — doc 18 §2.2).

    The ``bodies`` mapping is held **by reference**, not copied: the ingestion
    dispatch (``crawl_recipe_with_connector``) calls ``build_fetcher`` *before*
    ``discover``, so a connector builds the fetcher over its still-empty body dict
    and ``discover`` then populates that same dict — the fetcher sees the rendered
    rows by the time the runner fetches them.
    """

    def __init__(self, bodies: MutableMapping[str, str]) -> None:
        self._bodies = bodies

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        body = self._bodies.get(url)
        if body is None:  # pragma: no cover - the runner only fetches discovered URLs
            return 404, "", {}
        return 200, body, {"content-type": "text/html; charset=utf-8"}

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> StaticBodyFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ----------------------------------------------------------------------------
# JSON helpers shared by the open-data connectors
# ----------------------------------------------------------------------------


def parse_json_body(body: str, *, connector: str) -> Any:
    """Parse a JSON API body; raise a clear :class:`ConnectorError` on garbage."""
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"{connector} response was not valid JSON: {exc}") from exc


def dotted_get(obj: object, path: str) -> object:
    """Read a dotted ``a.b.c`` path out of a nested mapping; ``None`` if absent."""
    cur: object = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def with_query(url: str, params: Mapping[str, str]) -> str:
    """Merge ``params`` into ``url``'s query string (preserving existing params)."""
    request = httpx.Request("GET", url)
    merged = dict(request.url.params)
    merged.update(params)
    base = str(request.url.copy_with(query=None))
    return str(httpx.Request("GET", base, params=merged).url)


def trim_base(base_url: str) -> str:
    """Drop a trailing slash so we can join path segments unambiguously."""
    return base_url.rstrip("/")


def records_to_bodies(
    records: Sequence[Mapping[str, Any]],
    *,
    url_for: object,
    bodies: MutableMapping[str, str],
) -> list[str]:
    """Render records to HTML into ``bodies`` keyed by a per-record pointer URL.

    ``url_for(index, record) -> str`` builds the stable pointer URL for each
    record; the rendered HTML is written into the caller's live ``bodies`` map (the
    same object the connector's :class:`StaticBodyFetcher` holds by reference).
    Returns the ordered list of pointer URLs.
    """
    urls: list[str] = []
    assert callable(url_for)
    for index, record in enumerate(records):
        url = str(url_for(index, record))
        urls.append(url)
        bodies[url] = render_record_html(record)
    return urls


__all__ = [
    "Fetcher",
    "StaticBodyFetcher",
    "dotted_get",
    "parse_json_body",
    "records_to_bodies",
    "render_record_html",
    "trim_base",
    "with_query",
]
