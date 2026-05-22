"""HTTP endpoints for the auth module, mounted under `/api/v1/auth`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
