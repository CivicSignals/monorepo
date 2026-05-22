"""HTTP endpoints for the entities module, mounted under `/api/v1/entities`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/entities", tags=["entities"])
