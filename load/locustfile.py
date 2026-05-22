"""CivicSignals Locust load test — combined entry point.

Imports all five scenarios (L1–L5) so a single ``locust -f load/locustfile.py``
invocation can target any or all user classes.  Each scenario is also runnable
in isolation from its own file.

Scenario summary (doc 11 §7):

  L1 — SteadyStateMixUser:    2 000 RPS sustained (feed/detail/search/push)
  L2 — DigestSpikeUser:       600 RPS; 4 000 digests / 30 min burst
  L3 — IngestionMonitorUser:  ~3 RPS; 250 k docs / 24 h ingestion monitoring
  L4 — SmartSearchSpikeUser:  200 concurrent NL search queries, p95 ≤ 3 s
  L5 — PublicCrawlUser:       50 RPS; Googlebot-like public entity pages

NFR assertions (doc 09 §2.1–2.2, doc 11 §7):
  Targets are encoded in ``load/nfr_targets.py`` and attached to each scenario
  class as ``SCENARIO_TARGET``.  Use ``--check-fail-ratio`` and
  ``--check-avg-response-time`` on the Locust CLI to enforce them in CI:

    locust -f load/locustfile.py --host=http://localhost:8000 \\
           --headless --users=10 --spawn-rate=2 --run-time=30s \\
           --check-fail-ratio=0.01 --check-avg-response-time=800 \\
           --only-summary

Smoke run (CI-safe, no real load — 1 user, 5 seconds):
    locust -f load/locustfile.py --host=http://localhost:8000 \\
           --headless --users=1 --spawn-rate=1 --run-time=5s \\
           --only-summary

Or just verify imports:
    python -m load.locustfile
"""

from __future__ import annotations

# Re-export all user classes so Locust discovers them.
from load.l1_steady_state import SteadyStateMixUser as SteadyStateMixUser
from load.l2_digest_spike import DigestSpikeUser as DigestSpikeUser
from load.l3_ingestion_load import IngestionMonitorUser as IngestionMonitorUser
from load.l4_smart_search_spike import SmartSearchSpikeUser as SmartSearchSpikeUser
from load.l5_public_crawl import PublicCrawlUser as PublicCrawlUser
from load.nfr_targets import ALL_TARGETS as ALL_TARGETS

__all__ = [
    "SteadyStateMixUser",
    "DigestSpikeUser",
    "IngestionMonitorUser",
    "SmartSearchSpikeUser",
    "PublicCrawlUser",
    "ALL_TARGETS",
]

if __name__ == "__main__":
    # Quick self-test: print scenario targets without running a load test.
    for scenario_id, target in sorted(ALL_TARGETS.items()):
        print(f"{scenario_id}: {target.description}")
        print(f"  target_rps={target.target_rps}, max_error_rate={target.max_error_rate:.0%}")
        for name, lat in target.endpoint_targets.items():
            print(f"  [{name}] p50≤{lat.p50_ms}ms  p95≤{lat.p95_ms}ms")
        print()
