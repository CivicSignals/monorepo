"""Aggregates every module's router under ``/api/v1`` (doc 06 §3, §5).

Each module owns its own URL space via ``modules/<name>/routes.py``. MVP modules
are mounted here; ``public_feed`` is v2 and stays unmounted until then.
"""

from __future__ import annotations

from fastapi import APIRouter

from civicsignals_api.modules.accounts import routes as accounts_routes
from civicsignals_api.modules.admin import routes as admin_routes
from civicsignals_api.modules.auth import routes as auth_routes
from civicsignals_api.modules.billing import routes as billing_routes
from civicsignals_api.modules.contacts import routes as contacts_routes
from civicsignals_api.modules.entities import routes as entities_routes
from civicsignals_api.modules.extraction import routes as extraction_routes
from civicsignals_api.modules.foia import routes as foia_routes
from civicsignals_api.modules.icp import routes as icp_routes
from civicsignals_api.modules.ingestion import routes as ingestion_routes
from civicsignals_api.modules.integrations import routes as integrations_routes
from civicsignals_api.modules.notifications import routes as notifications_routes
from civicsignals_api.modules.pipeline import routes as pipeline_routes
from civicsignals_api.modules.recipes import routes as recipes_routes
from civicsignals_api.modules.searches import routes as searches_routes
from civicsignals_api.modules.signals import routes as signals_routes
from civicsignals_api.modules.smart_search import routes as smart_search_routes

api_router = APIRouter()

for _module_routes in (
    auth_routes,
    accounts_routes,
    billing_routes,
    icp_routes,
    entities_routes,
    contacts_routes,
    signals_routes,
    recipes_routes,
    ingestion_routes,
    extraction_routes,
    foia_routes,
    searches_routes,
    smart_search_routes,
    integrations_routes,
    pipeline_routes,
    notifications_routes,
    admin_routes,
):
    api_router.include_router(_module_routes.router)
