"""HTTP endpoints for the public_feed module, mounted under `/api/v1/public`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/public", tags=["public-feed"])
