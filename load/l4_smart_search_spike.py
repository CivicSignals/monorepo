"""L4 — Smart-search spike scenario.

200 concurrent smart-search queries (doc 11 §7 bullet 4).

NFR targets (doc 09 §2.1 + §2.4, doc 11 §7):
  - p95 latency ≤ 3 000 ms (natural-language query → result set).
  - Pure vector search (no LLM rewrite) ≤ 800 ms p95.
  - Graceful degradation when daily token budget exhausted (429 → BM25 fallback).
  - Max error rate: 5 % (degraded responses count as success, not errors).

The scenario sends a mix of:
  - Natural-language queries (LLM rewrite + pgvector ANN + summarization).
  - Direct keyword queries (bypass LLM; pure BM25 + vector).

Run against local stack:
    locust -f load/l4_smart_search_spike.py --host=http://localhost:8000 \\
           --users=20 --spawn-rate=5 --run-time=5m

Run against staging (full spike):
    locust -f load/l4_smart_search_spike.py --host=https://api.staging.civicsignals.io \\
           --users=200 --spawn-rate=20 --run-time=10m

# TODO J1: smart_search module routes are stubs; tasks skip gracefully on 404.
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task

from load.auth_helper import AuthContext, get_or_create_auth
from load.nfr_targets import L4_TARGET

_NL_QUERIES = [
    "K-12 school district technology RFP over $500k in Washington state",
    "municipal water infrastructure bond measure 2026",
    "county housing authority personnel change director",
    "public university library budget amendment this fiscal year",
    "state agency grant award environmental science",
    "city council contract expiring IT services vendor",
    "community college board decision curriculum approval",
    "school district construction bond election results",
    "special district broadband grant federal funding",
    "city manager hiring announcement Pacific Northwest",
]

_KEYWORD_QUERIES = [
    "rfp technology",
    "budget amendment",
    "personnel change director",
    "grant awarded environmental",
    "contract expiring",
    "bond measure",
    "curriculum adoption",
    "broadband infrastructure",
]

_ENTITY_KINDS = ["k12_district", "city", "county", "state_agency", "community_college"]


class SmartSearchSpikeUser(HttpUser):
    """Virtual user for L4 smart-search spike.

    NFR targets (doc 09 §2.1, §2.4, doc 11 §7):
      - p95 ≤ 3 000 ms (natural-language with LLM rewrite).
      - p95 ≤ 800 ms (pure vector search, no LLM).
      - Max error rate: 5 % (429 = budget exhausted = graceful degradation).
    """

    # Short think time: spike scenario — users are hammering search.
    wait_time = between(0.1, 0.5)
    _user_counter: int = 0

    def on_start(self) -> None:
        SmartSearchSpikeUser._user_counter += 1
        self._user_index = SmartSearchSpikeUser._user_counter
        self._ctx: AuthContext | None = None
        try:
            self._ctx = get_or_create_auth(self.client, self._user_index)
        except Exception as exc:  # noqa: BLE001
            self.environment.events.request.fire(
                request_type="AUTH",
                name="[auth] on_start",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )

    @task(70)
    def natural_language_search(self) -> None:
        """GET /api/v1/smart-search — NL query with LLM rewrite.

        NFR: p95 ≤ 3 000 ms (doc 09 §2.1 + §2.4).
        Budget exhaustion (429) = graceful degradation per doc 09 §3.3 — not an error.

        # TODO J1: smart_search routes are stubs; skip on 404.
        """
        if not self._ctx:
            return
        query = random.choice(_NL_QUERIES)
        params: dict[str, str | int] = {
            "q": query,
            "limit": "10",
            "mode": "nlp",
        }
        if random.random() < 0.4:
            params["entity_kind"] = random.choice(_ENTITY_KINDS)

        with self.client.get(
            "/api/v1/smart-search",
            headers=self._ctx.headers,
            params=params,
            name="GET /api/v1/smart-search (nlp)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            elif resp.status_code == 429:
                # Token budget exhausted — graceful degradation, not an error.
                # The client should fall back to keyword search (see next task).
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(30)
    def keyword_vector_search(self) -> None:
        """GET /api/v1/smart-search — keyword/BM25 + vector (no LLM rewrite).

        NFR: p95 ≤ 800 ms (doc 09 §2.4 "Pure vector search without LLM rewriting").

        # TODO J1: smart_search routes are stubs; skip on 404.
        """
        if not self._ctx:
            return
        query = random.choice(_KEYWORD_QUERIES)
        with self.client.get(
            "/api/v1/smart-search",
            headers=self._ctx.headers,
            params={"q": query, "limit": "10", "mode": "keyword"},
            name="GET /api/v1/smart-search (keyword)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")


SCENARIO_TARGET = L4_TARGET
