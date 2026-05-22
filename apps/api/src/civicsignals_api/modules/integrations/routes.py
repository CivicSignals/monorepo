"""HTTP endpoints for the integrations module, mounted under `/api/v1/integrations`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/integrations", tags=["integrations"])
