"""FastAPI application factory.

Run as the ``api`` process:
    uv run uvicorn civicsignals_api.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from civicsignals_api.api.v1 import api_router
from civicsignals_api.config import get_settings
from civicsignals_api.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="CivicSignals API",
        version="0.1.0",
        # OpenAPI published for SDK generation + schemathesis (doc 06 §5, QA-2).
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
