"""API contract tests using schemathesis (QA-2).

Tests property-based contract conformance of the CivicSignals OpenAPI spec.
The schema is loaded from the ASGI app in-process — no running server needed.

Coverage today
--------------
Only endpoints reachable without authentication are tested:
  - GET /healthz          (always public)
  - GET /api/v1/openapi.json  (always public)

This is intentional — the auth module (B1) is not yet merged, so there are
no bearer-authenticated endpoints to test. As endpoints land, add them in the
section marked "TODO QA-2: expand coverage".

How coverage expands
--------------------
1. After B1 (auth) merges: add a ``@schema.auth(...)`` hook or a
   ``schemathesis.hooks`` fixture that injects a test JWT so authed
   endpoints (signals, entities, etc.) are exercised.
2. After each module's routes land: schemathesis auto-discovers them
   from the OpenAPI schema — no code changes needed here unless you need
   custom overrides/filters.
3. For write endpoints (POST/PATCH/DELETE): add Hypothesis ``@given``
   strategies or ``before_call`` hooks to inject required headers like
   ``Idempotency-Key`` (§1.8 of doc 08).
4. To run nightly against staging: add a second job in api-contract.yml
   using ``schemathesis.openapi.from_url`` with the staging base URL
   and a long-lived test token (stored in GitHub Secrets).

Running locally
---------------
  cd apps/api && uv run pytest tests/test_contract.py -v
  # or just the schemathesis tests across the whole suite:
  cd apps/api && uv run pytest -k contract -v
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings
from schemathesis.openapi import from_asgi

from civicsignals_api.main import app

# ---------------------------------------------------------------------------
# Schema fixture (module-level so Hypothesis reuses the loaded schema)
# ---------------------------------------------------------------------------

# Load the OpenAPI schema directly from the ASGI app. This exercises exactly
# the same schema that FastAPI generates for the live server — no network, no
# separate process. The path must match openapi_url in main.py.
_schema = from_asgi("/api/v1/openapi.json", app)

# ---------------------------------------------------------------------------
# Public / unauthenticated surface schema
#
# ``include(path=...)`` returns a *new* schema filtered to the listed paths.
# Operations restricted to the public surface (no auth required).
# Expand this set as more public endpoints land (e.g. /api/v1/entities is
# publicly readable per doc 08 §3.4).
# ---------------------------------------------------------------------------

_PUBLIC_PATHS: list[str] = [
    "/healthz",
    "/api/v1/openapi.json",
]

_public_schema = _schema.include(path=_PUBLIC_PATHS)

# ---------------------------------------------------------------------------
# Contract test — public / unauthenticated surface
#
# schemathesis.parametrize() generates one pytest parameter per OpenAPI
# operation that passes the filter. Each generated ``case`` is an instance
# of schemathesis.Case with property-based inputs drawn by Hypothesis.
# call_and_validate() sends the request (via httpx ASGI transport, in-process)
# and asserts:
#   - The response status code is listed in the OpenAPI responses dict
#     (catches undocumented 4xx/5xx or undocumented 2xx).
#   - No 5xx is returned (not_a_server_error check, always enabled).
#   - The response body conforms to the declared JSON Schema.
# ---------------------------------------------------------------------------


@_public_schema.parametrize()
@settings(
    max_examples=10,  # fast in CI; raise to 50+ for nightly runs
    suppress_health_check=[
        # The ASGI test client does not use a real network so the
        # "too_slow" health check is irrelevant.
        HealthCheck.too_slow,
        # Filter set is small; Hypothesis may see repeated inputs.
        HealthCheck.filter_too_much,
    ],
    deadline=None,  # no wall-clock deadline — in-process is fast but variable
)
def test_openapi_contract_public(case):  # type: ignore[no-untyped-def]
    """All public operations conform to the OpenAPI contract.

    Checks applied by call_and_validate (schemathesis defaults):
    - not_a_server_error: response must not be 5xx.
    - response_schema_conformance: body must validate against the declared
      schema (when the spec declares a response schema).
    - status_code_conformance: status code must appear in the declared
      responses for this operation.
    """
    response = case.call_and_validate()
    # Belt-and-suspenders: explicitly assert no 5xx even though
    # call_and_validate already applies not_a_server_error.
    assert response.status_code < 500, (
        f"Server error {response.status_code} on {case.method} {case.path}: "
        f"{response.text[:200]}"
    )


# ---------------------------------------------------------------------------
# TODO QA-2: expand coverage as authed endpoints land
#
# Example pattern once B1 (auth) merges and issues JWTs:
#
#   import schemathesis
#
#   @schemathesis.hooks.register
#   def before_call(context, case):
#       """Inject a test bearer token so authed endpoints are exercised."""
#       case.headers = case.headers or {}
#       case.headers["Authorization"] = f"Bearer {_get_test_token()}"
#       case.headers["X-Workspace-Id"] = TEST_WORKSPACE_ID
#
#   @_schema.parametrize()
#   @settings(max_examples=10)
#   def test_openapi_contract_authed(case):
#       case.call_and_validate()
#
# The hook is scoped to the decorated test, so public + authed tests run
# independently without interfering with each other.
# ---------------------------------------------------------------------------
