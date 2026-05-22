"""L5 — Public read-only crawl scenario.

Simulates Googlebot crawling `/entities/*` and `/signals/*` public pages at
50 RPS while L1 runs at 50 % concurrently (doc 11 §7 bullet 5).

NFR targets (doc 09 §2.2, doc 11 §7):
  - 50 RPS against public entity pages (no auth required).
  - No impact on authenticated L1 traffic.
  - List endpoints: p95 ≤ 500 ms.
  - Resource GET:   p95 ≤ 250 ms.
  - Max error rate: 1 %.

The entity module is implemented and public (no auth required — doc C1 req 5).
The signals public-feed module (`public_feed`) is intentionally left unmounted
at MVP (doc 06 architecture note); the tasks skip gracefully on 404.

Run against local stack:
    locust -f load/l5_public_crawl.py --host=http://localhost:8000 \\
           --users=10 --spawn-rate=2 --run-time=5m

Run alongside L1 against staging:
    locust -f load/l1_steady_state.py load/l5_public_crawl.py \\
           --host=https://api.staging.civicsignals.io \\
           --users=1050 --spawn-rate=50 --run-time=30m

# TODO C2: entity pagination / public API may not be seeded locally.
# TODO public_feed: public signal pages are v2; skip gracefully on 404.
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, task

from load.nfr_targets import L5_TARGET

# ---------------------------------------------------------------------------
# Sample entity IDs (override with env var in staging runs)
# ---------------------------------------------------------------------------
_SAMPLE_ENTITY_IDS = (
    os.getenv("CS_SAMPLE_ENTITY_IDS", "").split(",")
    if os.getenv("CS_SAMPLE_ENTITY_IDS")
    else []
)

_ENTITY_KINDS = ["k12_district", "city", "county", "state_agency", "community_college"]
_STATES = ["WA", "CA", "TX", "FL", "NY", "IL", "OH", "PA", "GA", "NC"]

# Googlebot-like User-Agent for realistic crawl simulation.
_CRAWLER_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


class PublicCrawlUser(HttpUser):
    """Virtual user simulating a search-engine bot crawling public entity pages.

    NFR targets (doc 09 §2.2, doc 11 §7):
      - List endpoints: p95 ≤ 500 ms.
      - Resource GET:   p95 ≤ 250 ms.
      - Max error rate: 1 %.
      - Must not degrade authenticated L1 traffic.

    No auth headers — entities are public (doc 06, doc C1).
    """

    # Googlebot crawl rate: ~1 req/s per crawler instance.
    wait_time = between(0.5, 2.0)

    _CRAWLER_HEADERS = {
        "User-Agent": _CRAWLER_UA,
        "Accept": "application/json",
    }

    @task(50)
    def list_entities(self) -> None:
        """GET /api/v1/entities — entity directory, no auth.

        NFR: p95 ≤ 500 ms (doc 09 §2.2 list endpoints).
        """
        params: dict[str, str] = {"limit": "25"}
        if random.random() < 0.6:
            params["kind"] = random.choice(_ENTITY_KINDS)
        if random.random() < 0.5:
            params["state"] = random.choice(_STATES)
        if random.random() < 0.3:
            params["q"] = random.choice(["school", "city of", "county", "district"])

        with self.client.get(
            "/api/v1/entities",
            headers=self._CRAWLER_HEADERS,
            params=params,
            name="GET /api/v1/entities",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(40)
    def get_entity_detail(self) -> None:
        """GET /api/v1/entities/{id} — entity detail page.

        NFR: p95 ≤ 250 ms (doc 09 §2.2 resource GET).
        """
        entity_id = random.choice(_SAMPLE_ENTITY_IDS) if _SAMPLE_ENTITY_IDS else str(uuid.uuid4())
        with self.client.get(
            f"/api/v1/entities/{entity_id}",
            headers=self._CRAWLER_HEADERS,
            name="GET /api/v1/entities/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                # 404 expected for random UUIDs in local/smoke runs.
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(10)
    def public_signals_page(self) -> None:
        """GET /api/v1/signals/{id} (public, unauthenticated) — signal detail page.

        The public_feed module is intentionally unmounted at MVP (v2 feature).
        Skip gracefully on 404.

        # TODO public_feed: mount /api/v2/public/signals when module ships.
        """
        signal_id = str(uuid.uuid4())
        with self.client.get(
            f"/api/v1/signals/{signal_id}",
            headers=self._CRAWLER_HEADERS,
            name="GET /api/v1/signals/{id} (public)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 401, 403, 404):
                # 401/403 expected (no auth) until public feed is built.
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")


SCENARIO_TARGET = L5_TARGET
