"""HTTP endpoints for the contacts module, mounted under `/api/v1/contacts`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/contacts", tags=["contacts"])
