"""Plan definitions & feature flags for the billing module (N2).

This module is the authoritative, code-defined registry of CivicSignals plans.
Plans map to limits (seats, tracked entities, AI runs/month, API requests/month)
and feature flags (which capabilities each plan enables).

**No DB table** — plan limits are code-config, not rows. A workspace's current
plan is resolved from the ``billing_subscription.plan`` column written by N1's
Stripe webhook handlers. When no subscription row exists the workspace defaults
to the ``SELF_HOSTED`` free tier.

Stripe price IDs are read from ``Settings`` (environment variables) at runtime
so CI/self-host environments that lack a real Stripe account never break.

Usage (from other modules via billing.services only)::

    from civicsignals_api.modules.billing import services as billing_services

    plan = await billing_services.get_workspace_plan(session, workspace_id)
    if not billing_services.plan_allows(plan, Feature.SMART_SEARCH):
        raise plan_feature_required(Feature.SMART_SEARCH)
    limit = billing_services.plan_limit(plan, Dimension.SMART_SEARCHES_PER_MONTH)

See also:
    - :mod:`civicsignals_api.modules.billing.services` — service-layer helpers.
    - :mod:`civicsignals_api.modules.billing.dependencies` — FastAPI dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from civicsignals_api.modules.billing.models import SubscriptionPlan

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


class Feature(StrEnum):
    """Gateable product features (N2).

    Each value is a stable string key used in plan definitions and tests.
    Add new features here; update ``PLAN_FEATURES`` for each plan below.

    Naming convention: lower_snake_case, noun or noun_phrase.

    Feature legend (matches PRD §F, doc 03, pricing-data.ts):
      - MANAGED_SCRAPERS   — hosted scraper fleet (Cloud only; self-hosted BYO)
      - CONTACT_ENRICHMENT — verified contact enrichment from .gov/.edu sources
      - CRM_INTEGRATIONS   — Salesforce / HubSpot push sync (F11)
      - EXTENDED_CRMS      — Pipedrive / Attio / Folk connectors (Starter+)
      - SMART_SEARCH       — LLM-backed natural-language signal search (F13)
      - FOIA               — FOIA request tracker (F10)
      - API_ACCESS         — REST API + webhooks (F11.6)
      - ADVANCED_SCORING   — account scoring + competitor displacement (Pro+)
      - SSO                — SAML/OIDC single sign-on (Pro+; v2 feature flag here)
      - CUSTOM_RECIPES     — workspace-private custom scraper recipes (Enterprise)
      - VPC_DEPLOY         — VPC/on-prem deployment option (Enterprise)
      - AUDIT_LOG          — audit log access (F16; all Cloud plans)
      - PRIORITY_QUEUE     — priority signal processing queue (Pro+)
    """

    MANAGED_SCRAPERS = "managed_scrapers"
    CONTACT_ENRICHMENT = "contact_enrichment"
    CRM_INTEGRATIONS = "crm_integrations"
    EXTENDED_CRMS = "extended_crms"
    SMART_SEARCH = "smart_search"
    FOIA = "foia"
    API_ACCESS = "api_access"
    ADVANCED_SCORING = "advanced_scoring"
    SSO = "sso"
    CUSTOM_RECIPES = "custom_recipes"
    VPC_DEPLOY = "vpc_deploy"
    AUDIT_LOG = "audit_log"
    PRIORITY_QUEUE = "priority_queue"


# ---------------------------------------------------------------------------
# Metering dimensions
# ---------------------------------------------------------------------------


class Dimension(StrEnum):
    """Quota/limit dimensions tracked by N3 metering.

    ``UNLIMITED`` is a sentinel value; ``plan_limit`` returns ``None`` for it
    so callers can branch on ``limit is None`` to mean "no cap".

    Naming convention: lower_snake_case, noun or noun_phrase.

    Dimension legend (matches doc N3, PRD §F13, §F5, §F7):
      - SEATS                     — workspace member slots
      - TRACKED_ENTITIES          — active tracked entity count
      - SMART_SEARCHES_PER_MONTH  — LLM smart-search queries per calendar month
      - CONTACT_EXPORTS_PER_MONTH — contact CSV exports per calendar month
      - SAVED_SEARCHES            — max saved searches in the workspace (F7.6)
      - API_REQUESTS_PER_MONTH    — REST API calls per calendar month
    """

    SEATS = "seats"
    TRACKED_ENTITIES = "tracked_entities"
    SMART_SEARCHES_PER_MONTH = "smart_searches_per_month"
    CONTACT_EXPORTS_PER_MONTH = "contact_exports_per_month"
    SAVED_SEARCHES = "saved_searches"
    API_REQUESTS_PER_MONTH = "api_requests_per_month"


# ---------------------------------------------------------------------------
# Plan definition dataclass
# ---------------------------------------------------------------------------

# Sentinel: no numeric cap on a dimension. ``plan_limit`` returns ``None``.
_UNLIMITED = -1


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    """Immutable description of a billing plan (N2).

    ``limits`` maps :class:`Dimension` → integer cap (``_UNLIMITED`` = no cap).
    ``features`` is the set of :class:`Feature` values enabled for this plan.
    """

    plan: SubscriptionPlan
    display_name: str
    limits: dict[Dimension, int] = field(default_factory=dict)
    features: frozenset[Feature] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Plan registry
# ---------------------------------------------------------------------------
# One entry per SubscriptionPlan value. Keep in the same order as the enum.

PLANS: dict[SubscriptionPlan, PlanDefinition] = {
    # -----------------------------------------------------------------------
    # Self-Host — free, AGPL-3.0, BYO infrastructure.
    # No managed fleet, no contact enrichment, no Stripe subscription.
    # The API, FOIA tracker, and open recipe runner are all available.
    # -----------------------------------------------------------------------
    SubscriptionPlan.SELF_HOSTED: PlanDefinition(
        plan=SubscriptionPlan.SELF_HOSTED,
        display_name="Self-Host",
        limits={
            Dimension.SEATS: _UNLIMITED,
            Dimension.TRACKED_ENTITIES: _UNLIMITED,
            Dimension.SMART_SEARCHES_PER_MONTH: _UNLIMITED,
            Dimension.CONTACT_EXPORTS_PER_MONTH: _UNLIMITED,
            Dimension.SAVED_SEARCHES: _UNLIMITED,
            Dimension.API_REQUESTS_PER_MONTH: _UNLIMITED,
        },
        features=frozenset(
            {
                Feature.FOIA,
                Feature.API_ACCESS,
                Feature.SMART_SEARCH,
                Feature.CUSTOM_RECIPES,  # self-hosters can write private recipes
            }
        ),
    ),
    # -----------------------------------------------------------------------
    # Solo — $19/seat/month · 1 seat.
    # Single rep or boutique consultant. Managed fleet included; no enrichment.
    # -----------------------------------------------------------------------
    SubscriptionPlan.SOLO: PlanDefinition(
        plan=SubscriptionPlan.SOLO,
        display_name="Solo",
        limits={
            Dimension.SEATS: 1,
            Dimension.TRACKED_ENTITIES: 500,
            Dimension.SMART_SEARCHES_PER_MONTH: 20,
            Dimension.CONTACT_EXPORTS_PER_MONTH: 50,
            Dimension.SAVED_SEARCHES: 20,
            Dimension.API_REQUESTS_PER_MONTH: 5_000,
        },
        features=frozenset(
            {
                Feature.MANAGED_SCRAPERS,
                Feature.FOIA,
                Feature.API_ACCESS,
                Feature.SMART_SEARCH,
                Feature.AUDIT_LOG,
            }
        ),
    ),
    # -----------------------------------------------------------------------
    # Starter — $49/seat/month · up to 10 seats.
    # Small SLED sales team. Adds contact enrichment + CRM integrations.
    # -----------------------------------------------------------------------
    SubscriptionPlan.STARTER: PlanDefinition(
        plan=SubscriptionPlan.STARTER,
        display_name="Starter",
        limits={
            Dimension.SEATS: 10,
            Dimension.TRACKED_ENTITIES: 5_000,
            Dimension.SMART_SEARCHES_PER_MONTH: 100,
            Dimension.CONTACT_EXPORTS_PER_MONTH: 500,
            Dimension.SAVED_SEARCHES: 50,
            Dimension.API_REQUESTS_PER_MONTH: 50_000,
        },
        features=frozenset(
            {
                Feature.MANAGED_SCRAPERS,
                Feature.CONTACT_ENRICHMENT,
                Feature.CRM_INTEGRATIONS,
                Feature.EXTENDED_CRMS,
                Feature.FOIA,
                Feature.API_ACCESS,
                Feature.SMART_SEARCH,
                Feature.AUDIT_LOG,
            }
        ),
    ),
    # -----------------------------------------------------------------------
    # Pro — $149/seat/month · up to 30 seats.
    # Mid-market team. Adds advanced AI analytics, SSO, priority queue.
    # -----------------------------------------------------------------------
    SubscriptionPlan.PRO: PlanDefinition(
        plan=SubscriptionPlan.PRO,
        display_name="Pro",
        limits={
            Dimension.SEATS: 30,
            Dimension.TRACKED_ENTITIES: _UNLIMITED,
            Dimension.SMART_SEARCHES_PER_MONTH: 1_000,
            Dimension.CONTACT_EXPORTS_PER_MONTH: _UNLIMITED,
            Dimension.SAVED_SEARCHES: 200,
            Dimension.API_REQUESTS_PER_MONTH: 500_000,
        },
        features=frozenset(
            {
                Feature.MANAGED_SCRAPERS,
                Feature.CONTACT_ENRICHMENT,
                Feature.CRM_INTEGRATIONS,
                Feature.EXTENDED_CRMS,
                Feature.FOIA,
                Feature.API_ACCESS,
                Feature.SMART_SEARCH,
                Feature.ADVANCED_SCORING,
                Feature.SSO,
                Feature.AUDIT_LOG,
                Feature.PRIORITY_QUEUE,
            }
        ),
    ),
    # -----------------------------------------------------------------------
    # Enterprise — custom pricing · unlimited seats.
    # VPC / on-prem. Custom recipes, custom SLA, dedicated CSM.
    # -----------------------------------------------------------------------
    SubscriptionPlan.ENTERPRISE: PlanDefinition(
        plan=SubscriptionPlan.ENTERPRISE,
        display_name="Enterprise",
        limits={
            Dimension.SEATS: _UNLIMITED,
            Dimension.TRACKED_ENTITIES: _UNLIMITED,
            Dimension.SMART_SEARCHES_PER_MONTH: _UNLIMITED,
            Dimension.CONTACT_EXPORTS_PER_MONTH: _UNLIMITED,
            Dimension.SAVED_SEARCHES: _UNLIMITED,
            Dimension.API_REQUESTS_PER_MONTH: _UNLIMITED,
        },
        features=frozenset(Feature),  # all features enabled
    ),
}


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_plan(plan: SubscriptionPlan) -> PlanDefinition:
    """Return the :class:`PlanDefinition` for ``plan`` (always succeeds)."""
    return PLANS[plan]


def plan_allows(plan: SubscriptionPlan, feature: Feature) -> bool:
    """Return ``True`` if ``plan`` includes ``feature``."""
    return feature in PLANS[plan].features


def plan_limit(plan: SubscriptionPlan, dimension: Dimension) -> int | None:
    """Return the numeric cap for ``dimension`` on ``plan``.

    Returns ``None`` when the plan has no cap (unlimited).
    Returns ``0`` when the plan explicitly disallows the dimension entirely.
    """
    raw = PLANS[plan].limits.get(dimension)
    if raw is None or raw == _UNLIMITED:
        return None
    return raw


def stripe_price_id_for_plan(plan: SubscriptionPlan) -> str | None:
    """Return the Stripe price ID for ``plan`` from Settings, or ``None``.

    Self-hosted and Enterprise plans have no self-serve Stripe price ID.
    Tests and self-host environments that omit the env vars get ``None``
    without error (N2 does not require a live Stripe account).
    """
    from civicsignals_api.config import get_settings

    settings = get_settings()
    _price_map: dict[SubscriptionPlan, str | None] = {
        SubscriptionPlan.SELF_HOSTED: None,
        SubscriptionPlan.SOLO: settings.stripe_price_id_solo,
        SubscriptionPlan.STARTER: settings.stripe_price_id_starter,
        SubscriptionPlan.PRO: settings.stripe_price_id_pro,
        SubscriptionPlan.ENTERPRISE: None,  # quote-driven; no self-serve price
    }
    return _price_map.get(plan)
