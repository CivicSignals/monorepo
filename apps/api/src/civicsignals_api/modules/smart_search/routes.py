"""HTTP endpoints for the smart_search module, mounted under `/api/v1/smart-search`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/smart-search", tags=["smart-search"])
