"""HTTP endpoints for the searches module, mounted under `/api/v1/searches`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/searches", tags=["searches"])
