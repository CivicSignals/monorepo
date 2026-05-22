"""HTTP endpoints for the icp module, mounted under `/api/v1/icp`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/icp", tags=["icp"])
