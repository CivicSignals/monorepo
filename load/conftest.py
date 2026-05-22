"""Pytest conftest for the load test smoke suite (QA-3).

The smoke tests in this suite deliberately do NOT import locust directly to
avoid the gevent/ssl monkey-patch conflict (gevent/issues/1016). Locust
import checks are done via subprocess instead (see test_smoke.py).

This conftest is intentionally minimal.
"""
