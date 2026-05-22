"""HTTP endpoints for the pipeline module, mounted under `/api/v1/pipeline`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
