"""HTTP endpoints for the foia module, mounted under `/api/v1/foia`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/foia", tags=["foia"])
