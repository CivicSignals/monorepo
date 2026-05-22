---
id: rate-limits
title: Rate Limits & Plan Limits
sidebar_label: Rate Limits
slug: /api/rate-limits
---

# Rate Limits & Plan Limits

---

## API rate limits

Rate limits are enforced **per workspace API token** using a leaky-bucket algorithm.

| Plan | Sustained rate | Burst |
|---|---|---|
| **Solo** | 60 req/min | 120 |
| **Starter** | 300 req/min | 600 |
| **Pro** | 1,200 req/min | 2,400 |
| **Enterprise** | by contract | by contract |
| **Self-host** | unlimited (operator-configured) | unlimited |

---

## Rate limit headers

Every API response includes the current limit state:

```
X-RateLimit-Limit: 1200
X-RateLimit-Remaining: 1199
X-RateLimit-Reset: 1747391520
```

| Header | Description |
|---|---|
| `X-RateLimit-Limit` | Maximum requests allowed in the current window. |
| `X-RateLimit-Remaining` | Requests remaining in the current window. |
| `X-RateLimit-Reset` | Unix timestamp (seconds) when the window resets. |

---

## 429 Too Many Requests

When rate-limited, the response includes a `Retry-After` header:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
Retry-After: 30

{
  "type": "https://docs.civicsignals.io/errors/rate-limited",
  "title": "Rate limit exceeded",
  "status": 429,
  "detail": "You have exceeded the 300 req/min limit for this token. Retry after 30 seconds.",
  "request_id": "req_01J..."
}
```

`Retry-After` is in seconds. Implement exponential backoff if you encounter sustained `429` responses.

---

## Plan limits

In addition to request-rate limits, each plan enforces workspace-level usage limits. Check current usage:

```bash
GET /api/v1/billing/usage
Authorization: Bearer $TOKEN
```

```json
{
  "workspace_id": "...",
  "period": { "start": "2026-05-01", "end": "2026-05-31" },
  "limits": {
    "seats": 10,
    "tracked_entities": 500,
    "saved_searches": 50,
    "ai_runs_per_month": 5000,
    "api_requests_per_month": 100000
  },
  "current": {
    "seats": 6,
    "tracked_entities": 247,
    "saved_searches": 12,
    "ai_runs_per_month": 1820,
    "api_requests_per_month": 12044
  },
  "approaching_limit": ["tracked_entities"]
}
```

The `approaching_limit` array lists dimensions where usage is nearing the plan cap. The `GET /billing/limits` endpoint returns per-dimension state (`ok`, `warning`, or `exceeded`).

When a plan limit is exceeded (e.g. max tracked entities), the relevant write endpoint returns `429` with a `plan_limit_exceeded` error code.

---

## Self-host

On self-hosted installations, `GET /billing/usage` still works for visibility but plan limits are reported as `null` (unlimited; operator-managed). Rate limiting is operator-configured.

---

## Best practices

- Read `X-RateLimit-Remaining` and back off before hitting zero.
- Respect `Retry-After` on `429` responses.
- Use the [SDK](/api/intro#sdks) — both SDKs implement automatic retry with exponential backoff.
- For high-volume use cases (e.g. syncing the full signal corpus), prefer **webhooks** over polling the list endpoints.
