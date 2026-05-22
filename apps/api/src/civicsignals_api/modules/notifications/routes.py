"""HTTP endpoints for the notifications module, mounted under `/api/v1/notifications`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])
