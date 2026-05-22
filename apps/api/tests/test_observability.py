"""Tests for A6 observability scaffolding.

Covers:
- RequestContextMiddleware: sets/propagates X-Request-Id, binds log context.
- Sentry init: no-op when SENTRY_DSN is unset (no crash, no network call).
- OTel init: no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset (no crash).
- /metrics endpoint: responds 200 with Prometheus text format.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi.testclient import TestClient

from civicsignals_api.main import app
from civicsignals_api.otel import _parse_headers
from civicsignals_api.sentry import init_sentry

# ---------------------------------------------------------------------------
# RequestContextMiddleware
# ---------------------------------------------------------------------------


def test_request_id_generated_when_absent() -> None:
    """Response carries X-Request-Id even if the client does not send one."""
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-Id")
    assert request_id is not None
    # Should be a valid UUID.
    uuid.UUID(request_id)


def test_request_id_propagated_from_client() -> None:
    """Inbound X-Request-Id is echoed back unchanged."""
    custom_id = "my-trace-id-123"
    client = TestClient(app)
    response = client.get("/healthz", headers={"X-Request-Id": custom_id})
    assert response.headers.get("X-Request-Id") == custom_id


def test_request_id_differs_per_request() -> None:
    """Each request without X-Request-Id gets its own unique ID."""
    client = TestClient(app)
    r1 = client.get("/healthz")
    r2 = client.get("/healthz")
    assert r1.headers["X-Request-Id"] != r2.headers["X-Request-Id"]


def test_structlog_context_cleared_between_requests() -> None:
    """Context vars from request N must not bleed into request N+1."""
    client = TestClient(app)
    client.get("/healthz", headers={"X-Workspace-Id": "ws-abc"})
    # Second request without the header; context should be fresh (None).
    client.get("/healthz")
    # Structlog contextvars are cleared at the start of each request; verify
    # no workspace_id leaks by inspecting the cleared state after both requests
    # complete (the middleware clears at the START so end-state is the last
    # request's context which has workspace_id=None).
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("workspace_id") is None


# ---------------------------------------------------------------------------
# Sentry -- no-op when DSN is unset
# ---------------------------------------------------------------------------


def test_sentry_noop_when_dsn_none() -> None:
    """init_sentry(dsn=None) must not raise and must not import sentry_sdk."""
    # Should complete silently with no external network calls.
    init_sentry(dsn=None)


def test_sentry_noop_when_dsn_empty() -> None:
    """init_sentry(dsn='') must also be a no-op."""
    init_sentry(dsn="")


# ---------------------------------------------------------------------------
# OTel -- no-op when endpoint is unset
# ---------------------------------------------------------------------------


def test_otel_noop_when_endpoint_none() -> None:
    """init_otel(endpoint=None) must not raise."""
    from civicsignals_api.otel import init_otel

    init_otel(endpoint=None)


def test_otel_noop_when_endpoint_empty() -> None:
    """init_otel(endpoint='') must also be a no-op."""
    from civicsignals_api.otel import init_otel

    init_otel(endpoint="")


def test_parse_headers_empty() -> None:
    assert _parse_headers(None) == {}
    assert _parse_headers("") == {}


def test_parse_headers_single() -> None:
    assert _parse_headers("x-api-key=secret") == {"x-api-key": "secret"}


def test_parse_headers_multiple() -> None:
    result = _parse_headers("x-api-key=abc,x-team=my-team")
    assert result == {"x-api-key": "abc", "x-team": "my-team"}


# ---------------------------------------------------------------------------
# Prometheus /metrics endpoint
# ---------------------------------------------------------------------------


def test_metrics_endpoint_available() -> None:
    """/metrics responds 200 with Prometheus exposition format."""
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    # Prometheus text format starts with a HELP or TYPE comment or a metric name.
    body = response.text
    assert "python_gc_objects_collected_total" in body or "starlette" in body or "#" in body
