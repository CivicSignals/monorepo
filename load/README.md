# CivicSignals Load Tests (QA-3)

Five Locust scenarios (L1–L5) that mirror the NFRs from
[doc 09 (NFR)](../09-nfr.md) and the load test plan in
[doc 11 §7 (QA plan)](../11-qa-test-plan.md).

Results are logged in `ops/loadtest-history.md` after each run.

---

## Scenarios

| ID | File | Description | Target RPS | p95 budget | Max error rate |
|----|------|-------------|-----------|------------|----------------|
| **L1** | `l1_steady_state.py` | Steady-state mix: 60 % feed, 20 % signal-detail, 10 % saved-search, 5 % CRM-push, 5 % smart-search | 2 000 | varies per endpoint (800 ms feed, 1.5 s detail, 3 s search) | 1 % |
| **L2** | `l2_digest_spike.py` | Tuesday-morning digest burst: 4 000 sends / 30 min + L1 @ 30 % | 600 | 500 ms list, 800 ms feed | 1 % |
| **L3** | `l3_ingestion_load.py` | Ingestion monitoring: 250 k docs / 24 h; read-side recipe + job-poll paths | ~3 | 500 ms list, 250 ms get | 2 % |
| **L4** | `l4_smart_search_spike.py` | Smart-search spike: 200 concurrent NL + keyword queries | 200 | 3 000 ms NL, 800 ms keyword | 5 % (429 = graceful degradation) |
| **L5** | `l5_public_crawl.py` | Public crawl: Googlebot-like entity page crawl at 50 RPS, no auth | 50 | 500 ms list, 250 ms get | 1 % |

NFR numeric values are defined in `nfr_targets.py` and sourced from docs 09 and 11.

---

## Prerequisites

```bash
# From the repo root
cd apps/api && uv sync   # installs locust in the dev group
uv run locust --version  # should print locust 2.x
```

---

## Run against local stack (`make dev`)

```bash
# Start the full dev stack first (Postgres + Redis + API)
make dev   # in a separate terminal

# L1 — steady-state mix (smoke: 10 users, 30 s)
PYTHONPATH=. apps/api/.venv/bin/locust \
  -f load/l1_steady_state.py \
  --host=http://localhost:8000 \
  --users=10 --spawn-rate=2 --run-time=30s \
  --headless --only-summary

# L5 — public crawl (no auth required)
PYTHONPATH=. apps/api/.venv/bin/locust \
  -f load/l5_public_crawl.py \
  --host=http://localhost:8000 \
  --users=5 --spawn-rate=1 --run-time=30s \
  --headless --only-summary

# All scenarios combined (web UI on :8089)
PYTHONPATH=. apps/api/.venv/bin/locust \
  -f load/locustfile.py \
  --host=http://localhost:8000
```

### Environment variables for local runs

| Variable | Default | Description |
|----------|---------|-------------|
| `CS_TEST_PASSWORD` | `Locust$ecret99!` | Password for all virtual users |
| `CS_SIGNUP_PREFIX` | `locust` | Email prefix: `locust{n}@loadtest.invalid` |
| `CS_TEST_EMAIL` | _(generated)_ | Fixed email for single-user smoke runs |
| `CS_SAMPLE_SIGNAL_IDS` | _(empty)_ | Comma-separated signal UUIDs for detail/push tasks |
| `CS_SAMPLE_SAVED_SEARCH_IDS` | _(empty)_ | Comma-separated saved-search UUIDs |
| `CS_SAMPLE_CONNECTION_IDS` | _(empty)_ | Comma-separated integration-connection UUIDs for push tasks |
| `CS_SAMPLE_ENTITY_IDS` | _(empty)_ | Comma-separated entity UUIDs for L5 crawl |
| `CS_SAMPLE_JOB_IDS` | _(empty)_ | Comma-separated job UUIDs for L3 polling |

Seed the IDs from the staging DB snapshot before full load runs:

```bash
export CS_SAMPLE_SIGNAL_IDS=$(psql $DATABASE_URL -tAc \
  "SELECT string_agg(id::text, ',') FROM (SELECT id FROM signals_signal LIMIT 100) t")
```

---

## Run against staging

```bash
# Full L1 (2 000 users, 30-minute ramp + 30-minute sustain)
PYTHONPATH=. apps/api/.venv/bin/locust \
  -f load/l1_steady_state.py \
  --host=https://api.staging.civicsignals.io \
  --users=2000 --spawn-rate=100 --run-time=60m \
  --headless --only-summary \
  --check-fail-ratio=0.01 \
  --check-avg-response-time=800

# L4 spike (200 concurrent smart-search)
PYTHONPATH=. apps/api/.venv/bin/locust \
  -f load/l4_smart_search_spike.py \
  --host=https://api.staging.civicsignals.io \
  --users=200 --spawn-rate=20 --run-time=10m \
  --headless --only-summary \
  --check-fail-ratio=0.05

# L5 + L1 combined crawl
PYTHONPATH=. apps/api/.venv/bin/locust \
  -f load/l1_steady_state.py load/l5_public_crawl.py \
  --host=https://api.staging.civicsignals.io \
  --users=1050 --spawn-rate=50 --run-time=30m \
  --headless --only-summary
```

### NFR assertion flags

Use `--check-fail-ratio` and `--check-avg-response-time` to enforce NFRs in CI:

```bash
locust ... \
  --check-fail-ratio=0.01 \        # fail if error rate > 1 %
  --check-avg-response-time=800    # fail if avg response > 800 ms
```

The exit code is non-zero on NFR violation — integrate with CI as a quality gate.

---

## Smoke check (no real load)

```bash
# Import check only — confirms scenarios are valid Python
PYTHONPATH=. apps/api/.venv/bin/python -c \
  "from load.locustfile import ALL_TARGETS; print('OK:', list(ALL_TARGETS))"

# Structural smoke tests (pytest, no load generated)
PYTHONPATH=. apps/api/.venv/bin/pytest load/ -q -c load/pytest.ini
```

---

## Stubs and TODOs

Endpoints not yet implemented are guarded with `# TODO <task-id>` comments and
skip gracefully (accept 404 as success) so the suite is runnable today. As
each module ships, remove the guard and the endpoint auto-joins the load test:

| Task | Endpoint guarded |
|------|-----------------|
| `TODO G1` | `GET /api/v1/signals/{id}` |
| `TODO I1` | `GET /api/v1/saved-searches/{id}/results` |
| `TODO J1` | `GET /api/v1/smart-search` |
| `TODO K1` | `POST /api/v1/signals/{id}/push` |
| `TODO D1` | `GET /api/v1/recipes`, `GET /api/v1/recipes/{id}` |
| `TODO E1` | `GET /api/v1/jobs/{id}` |
| `TODO public_feed` | Public signals endpoint (v2, intentionally unmounted at MVP) |

---

## Recording results

After each full run, append a summary to `ops/loadtest-history.md`:

```markdown
## 2026-05-22 — L1 pre-release

- Host: staging
- Users: 2 000 | Spawn: 100/s | Duration: 60 min
- RPS achieved: 1 980
- p50: 210 ms | p95: 740 ms | error rate: 0.3 %
- Result: PASS
```
