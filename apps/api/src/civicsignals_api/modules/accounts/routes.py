"""HTTP endpoints for the accounts module, mounted under `/api/v1/accounts`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/accounts", tags=["accounts"])
