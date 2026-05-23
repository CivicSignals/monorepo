"""FastAPI application factory.

Run as the ``api`` process:
    uv run uvicorn civicsignals_api.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette_prometheus import PrometheusMiddleware, metrics

from civicsignals_api.api.v1 import api_router
from civicsignals_api.config import get_settings
from civicsignals_api.logging import configure_logging
from civicsignals_api.middleware import RequestContextMiddleware
from civicsignals_api.modules.admin.listeners import register_listeners
from civicsignals_api.modules.signals.listeners import (
    register_listeners as register_signals_listeners,
)
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

    # CORS (browser → API). The web app fetches the API cross-origin from the
    # browser (web :3000 → api :8000), so without these response headers the
    # browser blocks every authed call (incl. login). Added LAST so it is the
    # OUTERMOST middleware: it must answer the preflight ``OPTIONS`` and stamp the
    # ``Access-Control-Allow-*`` headers on the way out even for error responses
    # produced by the inner stack. ``allow_credentials`` is required for the
    # bearer + cookie flows; ``allow_methods``/``allow_headers="*"`` lets the
    # ``Authorization`` and ``X-Workspace-Id`` (workspace-scoping) headers through
    # preflight. ``X-Request-Id`` (echoed by RequestContextMiddleware) is exposed
    # so browser code can read it for support/debugging.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id"],
        )

    # Prometheus /metrics endpoint (A6). Scraped by the observability stack;
    # excluded from OpenAPI schema to keep it clean.
    app.add_route("/metrics", metrics, include_in_schema=False)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # B9: register in-process event-bus listeners that persist domain events as
    # audit rows.  Must be called after the router is mounted so the DB engine is
    # ready, but before the app starts serving requests.
    register_listeners()
    # F3: score each new signal for every workspace whose ICP pre-filter matches,
    # on ``signal.created`` (doc 14 §6) — the live matcher → scorer half of the
    # global-signal → per-workspace-feed path.
    register_signals_listeners()

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # OTel ASGI + SQLAlchemy instrumentations (A6). Must be called after the app
    # is fully configured so FastAPI's route list is complete.
    instrument_app(app)

    return app


app = create_app()
