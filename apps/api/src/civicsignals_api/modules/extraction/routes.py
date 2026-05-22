"""HTTP endpoints for the extraction module, mounted under `/api/v1/extraction`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/extraction", tags=["extraction"])
