"""NFR targets shared across all Locust scenarios.

Values are sourced from:
  - doc 09 §2.2 (Public API latency budgets)
  - doc 09 §2.1 (Web UI latency budgets)
  - doc 11 §7 (Load-test scenarios L1–L5)

All latency values are in milliseconds. Error rate thresholds are fractions
(0.01 = 1%).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyTarget:
    """p50 / p95 latency targets in milliseconds."""

    p50_ms: int
    p95_ms: int
    description: str


@dataclass(frozen=True)
class ScenarioTarget:
    """Complete NFR target for one load scenario."""

    scenario_id: str
    description: str
    target_rps: int  # sustained requests-per-second (whole scenario)
    max_error_rate: float  # fraction, e.g. 0.01 = 1 %
    endpoint_targets: dict[str, LatencyTarget]


# ---------------------------------------------------------------------------
# Per-endpoint latency budgets (doc 09 §2.2)
# ---------------------------------------------------------------------------

LIST_ENDPOINTS = LatencyTarget(p50_ms=150, p95_ms=500, description="List endpoints (doc 09 §2.2)")
RESOURCE_GET = LatencyTarget(
    p50_ms=80, p95_ms=250, description="Resource GET by id (doc 09 §2.2)"
)
SYNC_MUTATING = LatencyTarget(
    p50_ms=150, p95_ms=600, description="Synchronous mutating endpoints (doc 09 §2.2)"
)
ASYNC_ACCEPTING = LatencyTarget(
    p50_ms=50, p95_ms=150, description="Async-accepting POST → 202 (doc 09 §2.2)"
)

# Web-UI specific budgets (doc 09 §2.1) — used by L1 feed scenarios
FEED_LIST_UI = LatencyTarget(
    p50_ms=300, p95_ms=800, description="Signal feed list 25 items (doc 09 §2.1)"
)
SIGNAL_DETAIL_UI = LatencyTarget(
    p50_ms=500, p95_ms=1500, description="Signal detail page (doc 09 §2.1)"
)
SAVED_SEARCH_UI = LatencyTarget(
    p50_ms=350, p95_ms=900, description="Saved search result load (doc 09 §2.1)"
)
SMART_SEARCH_UI = LatencyTarget(
    p50_ms=1000, p95_ms=3000, description="Smart search natural-language query (doc 09 §2.1 + §2.4)"
)
CRM_PUSH_UI = LatencyTarget(
    p50_ms=300, p95_ms=1000, description="CRM push synchronous ack (doc 09 §2.1)"
)
LOGIN_UI = LatencyTarget(p50_ms=200, p95_ms=500, description="Login success (doc 09 §2.1)")

# ---------------------------------------------------------------------------
# Scenario-level targets (doc 11 §7)
# ---------------------------------------------------------------------------

# L1 — Steady-state mix (doc 11 §7 bullet 1)
# 60 % feed-list, 20 % signal-detail, 10 % saved-search-result,
# 5 % push-to-CRM, 5 % smart-search
# Ramp to 2 000 RPS sustained.
L1_TARGET = ScenarioTarget(
    scenario_id="L1",
    description="Steady-state mix — 2 000 RPS sustained across feed/detail/search/push",
    target_rps=2000,
    max_error_rate=0.01,
    endpoint_targets={
        "GET /api/v1/signals": FEED_LIST_UI,
        "GET /api/v1/signals/{id}": SIGNAL_DETAIL_UI,
        "GET /api/v1/saved-searches/{id}/results": SAVED_SEARCH_UI,
        "POST /api/v1/signals/{id}/push": CRM_PUSH_UI,
        "GET /api/v1/smart-search": SMART_SEARCH_UI,
    },
)

# L2 — Tuesday-morning digest spike (doc 11 §7 bullet 2)
# 4 000 digests in 30-minute window, L1 at 30 % concurrently.
# Assert transactional mail latency ≤ 30 s (background SLO, not HTTP latency).
L2_TARGET = ScenarioTarget(
    scenario_id="L2",
    description="Tuesday-morning digest — 4 000 sends in 30 min alongside 30 % L1",
    target_rps=600,  # 30 % of 2 000
    max_error_rate=0.01,
    endpoint_targets={
        "GET /api/v1/signals": FEED_LIST_UI,
        "GET /api/v1/saved-searches": LIST_ENDPOINTS,
        "GET /healthz": LatencyTarget(p50_ms=50, p95_ms=100, description="Health check"),
    },
)

# L3 — Ingestion + extraction load (doc 11 §7 bullet 3)
# 250 000 raw docs over 24 h; end-to-end SLO: source → feed-visible ≤ 60 min p95.
# HTTP surface is the recipe/job trigger endpoints.
L3_TARGET = ScenarioTarget(
    scenario_id="L3",
    description="Ingestion + extraction load — 250 000 docs / 24 h, e2e SLO ≤ 60 min",
    target_rps=3,  # ~250k / 86400 s ≈ 3 trigger RPS on the HTTP surface
    max_error_rate=0.02,
    endpoint_targets={
        "GET /api/v1/recipes": LIST_ENDPOINTS,
        "GET /api/v1/jobs/{id}": RESOURCE_GET,
    },
)

# L4 — Smart-search spike (doc 11 §7 bullet 4)
# 200 concurrent smart-search queries; p95 ≤ 3 s; graceful degradation on budget.
L4_TARGET = ScenarioTarget(
    scenario_id="L4",
    description="Smart-search spike — 200 concurrent queries, p95 ≤ 3 000 ms",
    target_rps=200,
    max_error_rate=0.05,  # degraded fallback = not an error
    endpoint_targets={
        "GET /api/v1/smart-search": SMART_SEARCH_UI,
    },
)

# L5 — Public read-only crawl (doc 11 §7 bullet 5)
# Simulates Googlebot at 50 RPS against /entities/* and /signals/* public pages.
L5_TARGET = ScenarioTarget(
    scenario_id="L5",
    description="Public read-only crawl — 50 RPS Googlebot-like, no auth impact on L1",
    target_rps=50,
    max_error_rate=0.01,
    endpoint_targets={
        "GET /api/v1/entities": LIST_ENDPOINTS,
        "GET /api/v1/entities/{id}": RESOURCE_GET,
    },
)

ALL_TARGETS = {t.scenario_id: t for t in [L1_TARGET, L2_TARGET, L3_TARGET, L4_TARGET, L5_TARGET]}
