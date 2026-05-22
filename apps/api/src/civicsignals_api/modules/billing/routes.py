"""HTTP endpoints for the billing module, mounted under `/api/v1/billing`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/billing", tags=["billing"])
