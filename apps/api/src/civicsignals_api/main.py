"""FastAPI application factory.

Run as the ``api`` process:
    uv run uvicorn civicsignals_api.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette_prometheus import PrometheusMiddleware, metrics

from civicsignals_api.api.v1 import api_router
from civicsignals_api.config import get_settings
from civicsignals_api.logging import configure_logging
from civicsignals_api.middleware import RequestContextMiddleware
from civicsignals_api.otel import init_otel, instrument_app
from civicsignals_api.problems import install_problem_handlers
from civicsignals_api.sentry import init_sentry


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Initialise observability back-ends before the app is constructed so that
    # instrumentations attach to the right tracer provider (A6).
    init_sentry(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
    )
    init_otel(
        endpoint=settings.otel_exporter_otlp_endpoint,
        service_name=settings.otel_service_name,
        headers_raw=settings.otel_exporter_otlp_headers,
        sample_ratio=settings.otel_traces_sample_ratio,
    )

    app = FastAPI(
        title="CivicSignals API",
        version="0.1.0",
        # OpenAPI published for SDK generation + schemathesis (doc 06 §5, QA-2).
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
    )

    # RFC 7807 problem+json error responses across the whole API (doc 08 §1.7).
    install_problem_handlers(app)

    # Middleware order matters: outermost middleware runs first on the way in and
    # last on the way out. We want request_id bound before anything else logs.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(PrometheusMiddleware)

    # Prometheus /metrics endpoint (A6). Scraped by the observability stack;
    # excluded from OpenAPI schema to keep it clean.
    app.add_route("/metrics", metrics, include_in_schema=False)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # OTel ASGI + SQLAlchemy instrumentations (A6). Must be called after the app
    # is fully configured so FastAPI's route list is complete.
    instrument_app(app)

    return app


app = create_app()
