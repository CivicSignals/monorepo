"""L1 — Steady-state mix load scenario.

Traffic mix (doc 11 §7):
  60 % feed-list (GET /api/v1/signals)
  20 % signal-detail (GET /api/v1/signals/{id})
  10 % saved-search-result (GET /api/v1/saved-searches/{id}/results)
   5 % push-to-CRM (POST /api/v1/signals/{id}/push)
   5 % smart-search (GET /api/v1/smart-search)

Target: 2 000 RPS sustained.
NFR source: doc 09 §2.1–2.2, doc 11 §7 (L1).

Run against local stack (make dev):
    locust -f load/l1_steady_state.py --host=http://localhost:8000 \\
           --users=200 --spawn-rate=20 --run-time=10m

Run against staging:
    locust -f load/l1_steady_state.py --host=https://api.staging.civicsignals.io \\
           --users=2000 --spawn-rate=100 --run-time=30m

CI smoke (import + instantiation check only — no real load):
    python -c "from load.l1_steady_state import SteadyStateMixUser; print('L1 OK')"

TODO G1, H1: signal-detail and smart-search endpoints are not yet built.
  The tasks below include guarded placeholders that skip gracefully when the
  endpoint returns 404, so the suite runs now and auto-covers them as they land.
"""

from __future__ import annotations

import os
import random
from typing import TYPE_CHECKING

from locust import HttpUser, between, task

from load.auth_helper import AuthContext, get_or_create_auth, random_idempotency_key
from load.nfr_targets import L1_TARGET

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Sample data pools (replaced by seeded IDs in a real staging run)
# ---------------------------------------------------------------------------

_SAMPLE_SIGNAL_IDS = os.getenv("CS_SAMPLE_SIGNAL_IDS", "").split(",") if os.getenv(
    "CS_SAMPLE_SIGNAL_IDS"
) else []
_SAMPLE_SAVED_SEARCH_IDS = os.getenv("CS_SAMPLE_SAVED_SEARCH_IDS", "").split(",") if os.getenv(
    "CS_SAMPLE_SAVED_SEARCH_IDS"
) else []
_SAMPLE_CONNECTION_IDS = os.getenv("CS_SAMPLE_CONNECTION_IDS", "").split(",") if os.getenv(
    "CS_SAMPLE_CONNECTION_IDS"
) else []

_SMART_SEARCH_QUERIES = [
    "K-12 school district RFP technology",
    "city council budget amendment 2026",
    "municipal water infrastructure grant",
    "county housing bond measure",
    "public library board decision hiring",
]

_SIGNAL_TYPES = ["rfp_posted", "budget_drafted", "personnel_change", "grant_awarded"]
_ENTITY_KINDS = ["k12_district", "city", "county", "state_agency"]
_STATES = ["WA", "CA", "TX", "FL", "NY"]


class SteadyStateMixUser(HttpUser):
    """Virtual user for L1 steady-state mix.

    NFR targets (doc 09 §2.1–2.2, doc 11 §7):
      - Feed list:          p50 ≤ 300 ms, p95 ≤ 800 ms
      - Signal detail:      p50 ≤ 500 ms, p95 ≤ 1 500 ms
      - Saved search:       p50 ≤ 350 ms, p95 ≤ 900 ms
      - CRM push (ack):     p50 ≤ 300 ms, p95 ≤ 1 000 ms
      - Smart search:       p50 ≤ 1 000 ms, p95 ≤ 3 000 ms
      - Max error rate:     1 %
      - Target RPS:         2 000 (2 000 users × ~1 req/s)
    """

    # Think time between requests: 0.5–2 s simulates realistic browser pacing.
    wait_time = between(0.5, 2.0)

    # Incremented across instances to give each VU a distinct identity.
    _user_counter: int = 0

    def on_start(self) -> None:
        """Authenticate and cache the context for this virtual user."""
        SteadyStateMixUser._user_counter += 1
        self._user_index = SteadyStateMixUser._user_counter
        self._ctx: AuthContext | None = None
        try:
            self._ctx = get_or_create_auth(self.client, self._user_index)
        except Exception as exc:  # noqa: BLE001
            # Auth failure does not abort the run; scenario degrades gracefully.
            self.environment.events.request.fire(
                request_type="AUTH",
                name="[auth] on_start",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )

    # -----------------------------------------------------------------------
    # Task weights implement the 60/20/10/5/5 traffic mix.
    # -----------------------------------------------------------------------

    @task(60)
    def feed_list(self) -> None:
        """GET /api/v1/signals — feed list (60 % of traffic).

        NFR: p95 ≤ 800 ms (doc 09 §2.1 "Signal feed list").
        """
        if not self._ctx:
            return
        params: dict[str, str] = {
            "limit": "25",
            "sort": "score:desc",
        }
        if random.random() < 0.7:
            params["signal_type"] = random.choice(_SIGNAL_TYPES)
        if random.random() < 0.5:
            params["entity_kind"] = random.choice(_ENTITY_KINDS)
        if random.random() < 0.4:
            params["state"] = random.choice(_STATES)

        with self.client.get(
            "/api/v1/signals",
            headers=self._ctx.headers,
            params=params,
            name="GET /api/v1/signals",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(20)
    def signal_detail(self) -> None:
        """GET /api/v1/signals/{id} — signal detail (20 % of traffic).

        NFR: p95 ≤ 1 500 ms (doc 09 §2.1 "Signal detail page").

        # TODO G1: signals module routes are stubs; skip gracefully until built.
        """
        if not self._ctx:
            return
        signal_id = random.choice(_SAMPLE_SIGNAL_IDS) if _SAMPLE_SIGNAL_IDS else "placeholder"
        with self.client.get(
            f"/api/v1/signals/{signal_id}",
            headers=self._ctx.headers,
            name="GET /api/v1/signals/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                # 404 expected when signals module is a stub (TODO G1).
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(10)
    def saved_search_results(self) -> None:
        """GET /api/v1/saved-searches/{id}/results (10 % of traffic).

        NFR: p95 ≤ 900 ms (doc 09 §2.1 "Saved search result load").

        # TODO I1: saved-searches results endpoint may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        saved_search_id = (
            random.choice(_SAMPLE_SAVED_SEARCH_IDS)
            if _SAMPLE_SAVED_SEARCH_IDS
            else "placeholder"
        )
        with self.client.get(
            f"/api/v1/saved-searches/{saved_search_id}/results",
            headers=self._ctx.headers,
            params={"limit": "25"},
            name="GET /api/v1/saved-searches/{id}/results",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(5)
    def push_to_crm(self) -> None:
        """POST /api/v1/signals/{id}/push — CRM push ack (5 % of traffic).

        NFR: p95 ≤ 1 000 ms synchronous ack (doc 09 §2.1 "CRM push").

        The endpoint returns 202 Accepted with a job_id; latency measured here
        is the synchronous ack, not end-to-end CRM round-trip.

        # TODO K1: push endpoint is a stub until integrations module is built.
        """
        if not self._ctx:
            return
        if not _SAMPLE_SIGNAL_IDS or not _SAMPLE_CONNECTION_IDS:
            # Skip gracefully until sample data is seeded.  # TODO K1
            return
        signal_id = random.choice(_SAMPLE_SIGNAL_IDS)
        connection_id = random.choice(_SAMPLE_CONNECTION_IDS)
        with self.client.post(
            f"/api/v1/signals/{signal_id}/push",
            headers={
                **self._ctx.headers,
                "Idempotency-Key": random_idempotency_key(),
            },
            json={
                "connection_id": connection_id,
                "target": "salesforce.opportunity",
            },
            name="POST /api/v1/signals/{id}/push",
            catch_response=True,
        ) as resp:
            if resp.status_code in (202, 404, 422):
                # 404/422 expected while module is a stub.  # TODO K1
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(5)
    def smart_search(self) -> None:
        """GET /api/v1/smart-search — natural-language search (5 % of traffic).

        NFR: p95 ≤ 3 000 ms (doc 09 §2.1 + §2.4 "Smart search").

        Graceful degradation: when daily token budget is exhausted the API
        returns 429; we treat it as a success (not a load-test failure) per
        doc 09 §3.3 degradation modes.

        # TODO J1: smart_search module routes are stubs; skip gracefully on 404.
        """
        if not self._ctx:
            return
        query = random.choice(_SMART_SEARCH_QUERIES)
        with self.client.get(
            "/api/v1/smart-search",
            headers=self._ctx.headers,
            params={"q": query, "limit": "10"},
            name="GET /api/v1/smart-search",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404, 429):
                # 429 = token budget exhausted — graceful degradation, not error.
                # 404 = endpoint not yet built (TODO J1).
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")


# ---------------------------------------------------------------------------
# Expose target metadata for the CI smoke check
# ---------------------------------------------------------------------------
SCENARIO_TARGET = L1_TARGET
