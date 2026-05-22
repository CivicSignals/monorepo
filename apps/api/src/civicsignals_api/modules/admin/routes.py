"""HTTP endpoints for the admin module, mounted under `/api/v1/admin`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])
