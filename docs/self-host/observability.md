# Observability — self-host guide

CivicSignals ships a full observability stack as an **optional** Docker Compose
profile. No external accounts are required; everything runs locally.

## What is included

| Component | Role |
|---|---|
| **OpenTelemetry Collector** | Receives OTLP traces from the api/workers; fans out to Tempo |
| **Grafana Tempo** | Distributed tracing back-end (stores and serves traces) |
| **Prometheus** | Scrapes `/metrics` from the api; stores time-series metrics |
| **Loki + Promtail** | Log aggregation; Promtail tails Docker stdout and ships JSON to Loki |
| **Grafana** | Unified UI for metrics (Prometheus), logs (Loki), and traces (Tempo) |

## Quick start

```bash
# 1. Start the core stack (if not already running).
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d

# 2. Add the observability profile.
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  --profile observability up -d

# 3. Open Grafana.  Default: anonymous admin access (no login required).
open http://localhost:3100
```

Grafana is exposed on port `3100` by default. Override with `GRAFANA_PORT` in
`infra/.env`.

## Connecting the api to the OTel Collector

Set the following in `infra/.env`:

```dotenv
# Tracing -- route api spans to the local OTel Collector.
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=civicsignals-api
OTEL_TRACES_SAMPLE_RATIO=1.0
```

Restart the `api` container for the change to take effect:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  up -d --no-deps api
```

## Error tracking with Sentry (optional)

CivicSignals supports Sentry for error tracking. You can use:

- **Sentry SaaS**: create a project at <https://sentry.io> and copy the DSN.
- **Sentry self-hosted**: follow <https://develop.sentry.dev/self-hosted/>.
- **GlitchTip**: a lightweight OSS Sentry-compatible alternative
  (<https://glitchtip.com/>).

Set the DSN in `infra/.env`:

```dotenv
SENTRY_DSN=https://<key>@<host>/<project>
# Optional tuning:
SENTRY_TRACES_SAMPLE_RATE=0.05
SENTRY_PROFILES_SAMPLE_RATE=0.0
```

Leave `SENTRY_DSN` unset (or empty) to disable Sentry entirely -- no network
calls are made in that case.

## Environment variables reference

All observability settings are optional. The app runs without any of them.

| Variable | Default | Description |
|---|---|---|
| `SENTRY_DSN` | _(unset)_ | Full Sentry DSN. Unset = Sentry disabled. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.05` | Fraction of transactions traced by Sentry (0.0-1.0). |
| `SENTRY_PROFILES_SAMPLE_RATE` | `0.0` | Fraction of sampled transactions profiled (0.0-1.0). |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(unset)_ | OTLP/gRPC endpoint, e.g. `http://otel-collector:4317`. Unset = OTel disabled. |
| `OTEL_EXPORTER_OTLP_HEADERS` | _(unset)_ | Comma-separated `key=value` headers (e.g. for Honeycomb/Grafana Cloud API key). |
| `OTEL_SERVICE_NAME` | `civicsignals-api` | OTel `service.name` attribute shown in trace UIs. |
| `OTEL_TRACES_SAMPLE_RATIO` | `1.0` | Head-based sampling ratio for OTel traces (1.0 = all). |
| `GRAFANA_PORT` | `3100` | Host port for Grafana (default avoids conflict with Next.js :3000). |

## Grafana data sources

When Grafana starts for the first time it has no pre-configured data sources.
Add them manually (or use Grafana provisioning):

1. **Prometheus** -- URL: `http://prometheus:9090`
2. **Loki** -- URL: `http://loki:3100`
3. **Tempo** -- URL: `http://tempo:3200`; enable "Use Tempo search" and set
   TraceQL; link Loki logs via `traceId` label.

## Sending traces to a cloud provider (Honeycomb, Grafana Cloud, etc.)

Instead of running the local collector, point `OTEL_EXPORTER_OTLP_ENDPOINT`
directly at your cloud provider's OTLP endpoint and provide any required
authentication header via `OTEL_EXPORTER_OTLP_HEADERS`:

```dotenv
OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io:443
OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=<your-api-key>
```

## Stopping the observability stack

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  --profile observability down
```

Data volumes (`prometheusdata`, `lokidata`, `tempodata`, `grafanadata`) are
preserved across restarts. Destroy them with `make clean` (caution: this also
destroys Postgres and Redis data).
