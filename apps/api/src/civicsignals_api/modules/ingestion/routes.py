"""HTTP endpoints for the ingestion module, mounted under `/api/v1/ingestion`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
