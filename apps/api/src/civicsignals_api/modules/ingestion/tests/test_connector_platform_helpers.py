"""Shared wave-2 building blocks (``connectors._platform``; doc 18 §5; D7).

The open-data connectors project each JSON record into a stable HTML ``<dl>`` the
recipe runner extracts over, and re-serve it from a :class:`StaticBodyFetcher`.
These unit tests pin that contract (flattening, escaping, determinism, the
by-reference body map) so a change to it can't silently break every open-data
recipe's golden fixtures.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from civicsignals_api.modules.ingestion.connectors._platform import (
    StaticBodyFetcher,
    dotted_get,
    parse_json_body,
    records_to_bodies,
    render_record_html,
    with_query,
)
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError


def _text(soup: BeautifulSoup, selector: str) -> str:
    """Selected element's text; asserts the element exists (keeps mypy strict happy)."""
    element = soup.select_one(selector)
    assert isinstance(element, Tag), f"selector {selector!r} matched nothing"
    return element.get_text()


def test_render_flattens_nested_and_lists() -> None:
    html = render_record_html(
        {"title": "T", "budget": {"amount": 50000, "fy": 2026}, "tags": ["a", "b"]}
    )
    soup = BeautifulSoup(html, "html.parser")
    assert _text(soup, "dd[data-key='title']") == "T"
    assert _text(soup, "dd[data-key='budget.amount']") == "50000"
    assert _text(soup, "dd[data-key='budget.fy']") == "2026"
    assert _text(soup, "dd[data-key='tags']") == "a; b"


def test_render_is_deterministic_sorted_keys() -> None:
    a = render_record_html({"b": 2, "a": 1})
    b = render_record_html({"a": 1, "b": 2})
    assert a == b
    # 'a' renders before 'b'.
    assert a.index('data-key="a"') < a.index('data-key="b"')


def test_render_escapes_html() -> None:
    html = render_record_html({"x": "<script>alert(1)</script>"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_none_becomes_empty_string() -> None:
    soup = BeautifulSoup(render_record_html({"x": None}), "html.parser")
    assert _text(soup, "dd[data-key='x']") == ""


def test_static_body_fetcher_serves_by_reference() -> None:
    bodies: dict[str, str] = {}
    fetcher = StaticBodyFetcher(bodies)  # built before bodies populated
    bodies["http://x/1"] = "<dl>ok</dl>"
    status, body, headers = fetcher.fetch("http://x/1", user_agent="UA", max_redirects=5)
    assert status == 200
    assert body == "<dl>ok</dl>"
    assert "text/html" in headers["content-type"]
    # Unknown URL -> 404 (the runner only ever fetches discovered URLs).
    assert fetcher.fetch("http://x/missing", user_agent="UA", max_redirects=5)[0] == 404
    assert fetcher.robots_txt("http://x/1", user_agent="UA") is None


def test_records_to_bodies_fills_map_and_returns_urls() -> None:
    bodies: dict[str, str] = {}
    urls = records_to_bodies(
        [{"title": "A"}, {"title": "B"}],
        url_for=lambda i, _r: f"http://x/{i}",
        bodies=bodies,
    )
    assert urls == ["http://x/0", "http://x/1"]
    assert set(bodies) == {"http://x/0", "http://x/1"}


def test_dotted_get() -> None:
    assert dotted_get({"a": {"b": {"c": 1}}}, "a.b.c") == 1
    assert dotted_get({"a": {}}, "a.b") is None
    assert dotted_get([], "a") is None


def test_parse_json_body_raises_on_garbage() -> None:
    import pytest

    assert parse_json_body("", connector="socrata") == {}
    with pytest.raises(ConnectorError, match="not valid JSON"):
        parse_json_body("not json {{", connector="socrata")


def test_with_query_merges_params() -> None:
    out = with_query("https://x/y?a=1", {"b": "2"})
    assert "a=1" in out
    assert "b=2" in out
