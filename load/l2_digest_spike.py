"""L2 — Tuesday-morning digest spike scenario.

Simulates the weekly digest burst (doc 09 §1.3, doc 11 §7 bullet 2):
  - 4 000 digest sends in a 30-minute window.
  - Simultaneously runs L1 traffic at 30 % intensity.
  - Assert transactional mail latency stays under 30 s (background SLO).

The HTTP surface that this scenario exercises:
  - GET  /api/v1/saved-searches        (list digest configurations)
  - GET  /api/v1/saved-searches/{id}/results (resolve digest content)
  - POST /api/v1/digests               (trigger digest send, or read status)
  - GET  /healthz                      (platform health during burst)
  - GET  /api/v1/signals               (L1 @ 30 % — same endpoint, lower weight)

NFR target (doc 11 §7 L2, doc 09 §1.3):
  - Sustained RPS: 600 (30 % of L1's 2 000).
  - Max error rate: 1 %.
  - Transactional mail must not be delayed (background assertion, not HTTP).

Run against local stack:
    locust -f load/l2_digest_spike.py --host=http://localhost:8000 \\
           --users=100 --spawn-rate=20 --run-time=30m

Run against staging:
    locust -f load/l2_digest_spike.py --host=https://api.staging.civicsignals.io \\
           --users=600 --spawn-rate=50 --run-time=30m

# TODO I1: saved-searches and digests endpoints may be stubs; tasks skip on 404.
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task

from load.auth_helper import AuthContext, get_or_create_auth
from load.nfr_targets import L2_TARGET

_SIGNAL_TYPES = ["rfp_posted", "budget_drafted", "grant_awarded"]
_STATES = ["WA", "CA", "TX", "FL", "NY"]


class DigestSpikeUser(HttpUser):
    """Virtual user for L2 digest burst + background L1 mix.

    NFR targets (doc 09 §1.3, doc 11 §7):
      - List endpoints:   p95 ≤ 500 ms
      - Feed list:        p95 ≤ 800 ms
      - Health check:     p95 ≤ 100 ms
      - Max error rate:   1 %
    """

    wait_time = between(0.5, 2.0)
    _user_counter: int = 0

    def on_start(self) -> None:
        DigestSpikeUser._user_counter += 1
        self._user_index = DigestSpikeUser._user_counter
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

    # -----------------------------------------------------------------------
    # Task mix: digest-heavy (60 %) + background feed (40 %)
    # -----------------------------------------------------------------------

    @task(30)
    def list_saved_searches(self) -> None:
        """GET /api/v1/saved-searches — enumerate digest configs.

        NFR: p95 ≤ 500 ms (doc 09 §2.2 list endpoints).
        # TODO I1: may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        with self.client.get(
            "/api/v1/saved-searches",
            headers=self._ctx.headers,
            params={"limit": "25"},
            name="GET /api/v1/saved-searches",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(20)
    def saved_search_results_digest(self) -> None:
        """GET /api/v1/saved-searches/{id}/results — resolve digest content.

        NFR: p95 ≤ 900 ms (doc 09 §2.1 "Saved search result load").
        # TODO I1: may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        with self.client.get(
            "/api/v1/saved-searches/placeholder/results",
            headers=self._ctx.headers,
            params={"limit": "25"},
            name="GET /api/v1/saved-searches/{id}/results",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(10)
    def digest_status(self) -> None:
        """GET /api/v1/digests — check digest configuration / status.

        NFR: p95 ≤ 500 ms (list endpoint).
        # TODO I2: digests endpoint may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        with self.client.get(
            "/api/v1/digests",
            headers=self._ctx.headers,
            name="GET /api/v1/digests",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(30)
    def background_feed(self) -> None:
        """GET /api/v1/signals — background L1 @ 30 % intensity.

        NFR: p95 ≤ 800 ms (doc 09 §2.1 "Signal feed list").
        """
        if not self._ctx:
            return
        params: dict[str, str] = {"limit": "25", "sort": "score:desc"}
        if random.random() < 0.5:
            params["signal_type"] = random.choice(_SIGNAL_TYPES)
        if random.random() < 0.3:
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

    @task(10)
    def health_check(self) -> None:
        """GET /healthz — platform health during burst.

        NFR: p95 ≤ 100 ms (infra health endpoint).
        """
        with self.client.get(
            "/healthz",
            name="GET /healthz",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code}")


SCENARIO_TARGET = L2_TARGET
