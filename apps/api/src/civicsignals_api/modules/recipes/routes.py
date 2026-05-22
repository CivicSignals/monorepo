"""HTTP endpoints for the recipes module, mounted under `/api/v1/recipes`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/recipes", tags=["recipes"])
