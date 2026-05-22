# Self-host telemetry

CivicSignals includes an **opt-in** anonymous usage ping that helps us understand
how the self-hosted product is used in the wild. This data funds ongoing
improvements to recipe quality, reliability, and documentation.

**Telemetry is OFF by default.** Nothing is ever sent unless you explicitly
enable it.

---

## Enabling telemetry

Add one environment variable to your `.env` file or compose override:

```env
CIVICSIGNALS_TELEMETRY_ENABLED=true
```

That is the only change required. The beat scheduler will send a ping once a
week (Monday at 03:00 UTC by default).

---

## What is sent

One HTTPS POST to `https://telemetry.civicsignals.io/v1/ping` per week. The
JSON body contains **only** the following fields:

| Field | Example value | Description |
|---|---|---|
| `instance_id` | `"a1b2c3d4-…"` | A random UUID generated on first run and stored locally. **Not derived from any user or workspace data.** |
| `version` | `"0.1.0"` | The running application version. |
| `deploy_type` | `"self-host"` | Always `"self-host"` for this distribution. |
| `workspace_count_bucket` | `"6-25"` | Coarse band — never an exact count. |
| `signal_count_bucket` | `"100k"` | Coarse band — never an exact count. |

**Nothing else.** There is no mechanism to include anything else — the payload
builder enforces a strict allowlist (`_ALLOWED_PAYLOAD_KEYS` in
`apps/api/src/civicsignals_api/telemetry.py`) and the test suite asserts that
no PII field names are ever present.

### What is never sent

- Email addresses, names, or any contact records.
- Signal content, ICP text, or scraped document data.
- Any workspace ID, user ID, or other linkable identifier.
- Query strings, search terms, or recipe configuration.
- IP addresses (the receiving endpoint sees only your egress NAT IP, which we
  do not log or store beyond the TCP connection).

---

## Instance ID

The instance ID is a random UUID4 generated on the first telemetry ping and
persisted to a local file (default: `/tmp/civicsignals-instance-id`). It is
**not** derived from any tenant, user, or database record. Its sole purpose is
to let us distinguish "one instance running for a long time" from "many
short-lived instances" when looking at aggregate weekly counts.

You can change the storage path to a persistent volume so the ID survives
container restarts:

```env
CIVICSIGNALS_TELEMETRY_ID_PATH=/var/lib/civicsignals/instance-id
```

You can also reset your instance ID at any time by deleting the file.

---

## Changing the endpoint

If you run an internal telemetry collector (for your own visibility into
multi-node self-host deployments), point telemetry there instead:

```env
CIVICSIGNALS_TELEMETRY_ENDPOINT=https://your-collector.example.com/v1/ping
```

---

## Disabling telemetry

Simply leave `CIVICSIGNALS_TELEMETRY_ENABLED` unset (the default) or set it
to `false`. No network connection is ever opened, and the Celery beat task
returns immediately without building any payload.

```env
CIVICSIGNALS_TELEMETRY_ENABLED=false
```

---

## Source code

The complete implementation is in
[`apps/api/src/civicsignals_api/telemetry.py`](../../apps/api/src/civicsignals_api/telemetry.py).
The Celery beat entry lives in
[`apps/api/src/civicsignals_api/celery_app.py`](../../apps/api/src/civicsignals_api/celery_app.py)
under the key `"telemetry.ping"`.

Tests are in
[`apps/api/tests/test_telemetry.py`](../../apps/api/tests/test_telemetry.py).

---

## Relationship to PostHog / product analytics

PostHog (LC-14) is the *cloud* product analytics layer — it tracks in-app
events for signed-in users on `app.civicsignals.io`. It is a separate system
with its own consent and privacy controls and is **not present in the
self-hosted distribution**.

The anonymous telemetry ping described here is **exclusively for self-hosted
instances** and contains only the coarse aggregate fields listed above.
