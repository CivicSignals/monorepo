"""OpenTelemetry tracing integration (doc 06 §9, A6).

OTel is **config-gated**: tracing is a no-op when ``OTEL_EXPORTER_OTLP_ENDPOINT``
is not set.  When the endpoint is set, the SDK is configured with:

- OTLP/gRPC exporter (``opentelemetry-exporter-otlp-proto-grpc``).
- FastAPI + SQLAlchemy + Celery auto-instrumentations.
- Head-based ratio sampler (default 100%; tune via ``OTEL_TRACES_SAMPLE_RATIO``).

Self-host back-ends (see ``infra/observability/``): the OTel Collector + Tempo or
Jaeger are the recommended local options.  Cloud: Honeycomb, Grafana Cloud, etc.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _parse_headers(raw: str | None) -> dict[str, str]:
    """Parse ``KEY=VALUE,KEY2=VALUE2`` header string into a dict."""
    if not raw:
        return {}
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = v.strip()
    return result


def init_otel(
    endpoint: str | None,
    service_name: str = "civicsignals-api",
    headers_raw: str | None = None,
    sample_ratio: float = 1.0,
) -> None:
    """Configure OTel SDK if ``endpoint`` is provided; otherwise a no-op.

    This function must be called **before** the FastAPI app is instrumented
    (i.e. before :func:`instrument_app`).

    Args:
        endpoint: OTLP/gRPC endpoint, e.g. ``"http://otel-collector:4317"``.
                  Pass ``None`` or empty string to disable.
        service_name: OTel ``service.name`` resource attribute.
        headers_raw: Optional comma-separated ``key=value`` pairs forwarded as
                     gRPC metadata (useful for cloud providers requiring an API
                     key header).
        sample_ratio: Head-based sampling ratio (0.0-1.0, default 1.0 = all).
    """
    if not endpoint:
        logger.debug("otel_exporter_otlp_endpoint not set — OTel tracing disabled")
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    resource = Resource(attributes={SERVICE_NAME: service_name})
    sampler = TraceIdRatioBased(sample_ratio)
    provider = TracerProvider(resource=resource, sampler=sampler)

    headers = _parse_headers(headers_raw)
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    logger.info(
        "otel_initialized",
        extra={"endpoint": endpoint, "service_name": service_name},
    )


def instrument_app(app: FastAPI) -> None:
    """Attach OTel auto-instrumentations to a FastAPI app instance.

    Safe to call even when OTel is disabled (instrumentation packages check
    whether a provider is configured before doing anything).

    Call this *after* :func:`init_otel` and *after* the app is created.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    # SQLAlchemy instrumentation is engine-level; instrument the whole module so
    # it picks up the async engine created lazily in db.py.
    SQLAlchemyInstrumentor().instrument()


def instrument_celery() -> None:
    """Attach OTel auto-instrumentation to Celery.

    Call this from the Celery worker entrypoint (or from celery_app.py after the
    OTel SDK is initialised).  Safe to call when OTel is disabled.
    """
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
