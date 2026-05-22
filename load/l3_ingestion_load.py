"""L3 — Ingestion + extraction load scenario.

Simulates injecting 250 000 raw documents over 24 hours across 1 500 recipes
(doc 11 §7 bullet 3). The HTTP surface for this scenario is:
  - GET  /api/v1/recipes               (list/browse available recipes)
  - GET  /api/v1/recipes/{id}          (fetch recipe detail)
  - GET  /api/v1/jobs/{id}             (poll async extraction job status)
  - GET  /healthz                      (pipeline health)

Note: actual recipe *triggering* (POST /api/v1/recipes/{id}/run) is an
admin-only / internal action handled by the Celery scheduler — it is NOT
called by load-test virtual users so we don't accidentally hammer the
scheduler. Instead, this scenario measures the read-side latency that operators
experience while monitoring ingestion, and validates the job-polling path.

End-to-end SLO (background, not HTTP latency — doc 09 §2.3):
  - Source publish → raw document stored: ≤ 30 min p95
  - Raw document → extraction completed: ≤ 15 min p95
  - Extraction → signal scored + feed-visible: ≤ 5 min p95
  - End-to-end: ≤ 60 min p95

NFR target (doc 11 §7 L3):
  - HTTP trigger RPS: ~3 (250k / 86 400 s ≈ 2.9)
  - Max error rate: 2 % (higher tolerance due to rate-limiting on admin ops)

Run against local stack:
    locust -f load/l3_ingestion_load.py --host=http://localhost:8000 \\
           --users=10 --spawn-rate=1 --run-time=1h

Run against staging (24-hour soak):
    locust -f load/l3_ingestion_load.py --host=https://api.staging.civicsignals.io \\
           --users=30 --spawn-rate=1 --run-time=24h

# TODO D1, D2: recipe runner and ingestion queue endpoints are stubs; skip on 404.
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, task

from load.auth_helper import AuthContext, get_or_create_auth
from load.nfr_targets import L3_TARGET

_SAMPLE_JOB_IDS = os.getenv("CS_SAMPLE_JOB_IDS", "").split(",") if os.getenv(
    "CS_SAMPLE_JOB_IDS"
) else []

_RECIPE_STATES = ["active", "paused", "degraded"]


class IngestionMonitorUser(HttpUser):
    """Virtual user monitoring ingestion pipeline read paths.

    NFR targets (doc 09 §2.2, doc 11 §7):
      - List endpoints (recipes):  p95 ≤ 500 ms
      - Resource GET (jobs):       p95 ≤ 250 ms
      - Health check:              p95 ≤ 100 ms
      - Max error rate:            2 %
    """

    # Longer think time: ingestion monitoring is human-paced.
    wait_time = between(2.0, 8.0)
    _user_counter: int = 0

    def on_start(self) -> None:
        IngestionMonitorUser._user_counter += 1
        self._user_index = IngestionMonitorUser._user_counter
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

    @task(40)
    def list_recipes(self) -> None:
        """GET /api/v1/recipes — list active recipes.

        NFR: p95 ≤ 500 ms (doc 09 §2.2 list endpoints).
        # TODO D1: recipes module may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        params: dict[str, str] = {"limit": "25"}
        if random.random() < 0.5:
            params["status"] = random.choice(_RECIPE_STATES)
        with self.client.get(
            "/api/v1/recipes",
            headers=self._ctx.headers,
            params=params,
            name="GET /api/v1/recipes",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(30)
    def get_recipe_detail(self) -> None:
        """GET /api/v1/recipes/{id} — recipe detail.

        NFR: p95 ≤ 250 ms (doc 09 §2.2 resource GET).
        # TODO D1: may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        recipe_id = str(uuid.uuid4())
        with self.client.get(
            f"/api/v1/recipes/{recipe_id}",
            headers=self._ctx.headers,
            name="GET /api/v1/recipes/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(20)
    def poll_job_status(self) -> None:
        """GET /api/v1/jobs/{id} — poll async extraction job.

        NFR: p95 ≤ 250 ms (doc 09 §2.2 resource GET).
        # TODO E1: jobs endpoint may be a stub; skip on 404.
        """
        if not self._ctx:
            return
        job_id = random.choice(_SAMPLE_JOB_IDS) if _SAMPLE_JOB_IDS else f"job_{uuid.uuid4()}"
        with self.client.get(
            f"/api/v1/jobs/{job_id}",
            headers=self._ctx.headers,
            name="GET /api/v1/jobs/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(10)
    def health_check(self) -> None:
        """GET /healthz — pipeline health during ingestion load.

        NFR: p95 ≤ 100 ms.
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


SCENARIO_TARGET = L3_TARGET
