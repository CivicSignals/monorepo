"""HTTP endpoints for the signals module, mounted under `/api/v1/signals`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/signals", tags=["signals"])
