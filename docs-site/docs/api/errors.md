---
id: errors
title: Errors
sidebar_label: Errors
slug: /api/errors
---

# Errors

All CivicSignals API errors follow [RFC 7807 Problem Details](https://datatracker.ietf.org/doc/html/rfc7807) with `Content-Type: application/problem+json`.

---

## Error shape

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/problem+json

{
  "type": "https://docs.civicsignals.io/errors/validation",
  "title": "Validation failed",
  "status": 422,
  "detail": "filter combination is not supported",
  "instance": "/api/v1/saved-searches",
  "errors": [
    {
      "field": "filters.entity_kind",
      "code": "invalid_combination",
      "message": "entity_kind 'k12_district' cannot be combined with state='International'"
    }
  ],
  "request_id": "req_01J0R3F6Y7E1ABCDEF"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | URI | Machine-readable error type. Link to the error reference if published; `about:blank` otherwise. |
| `title` | string | Short human-readable summary. Stable for a given `type`. |
| `status` | integer | HTTP status code. |
| `detail` | string | Human-readable explanation specific to this occurrence. |
| `instance` | string | The request path that produced the error. |
| `errors` | array | Per-field validation errors (present on `422` responses). |
| `errors[].field` | string | Dot-path to the offending field. |
| `errors[].code` | string | Machine-readable error code (e.g. `required`, `invalid_combination`). |
| `errors[].message` | string | Human-readable field-level message. |
| `request_id` | string | Opaque ID logged server-side. Include in support requests. |

---

## HTTP status codes

| Code | Meaning |
|---|---|
| `200 OK` | Request succeeded. |
| `201 Created` | Resource created. Body is the new resource; `Location` header points to it. |
| `202 Accepted` | Async job started. Body has `job_id` and `status_url`. |
| `204 No Content` | Delete or void action succeeded. |
| `400 Bad Request` | Malformed request, unknown filter parameter, or missing required header. |
| `401 Unauthorized` | No token, expired token, or invalid credentials. |
| `403 Forbidden` | Valid token but insufficient scope or role. |
| `404 Not Found` | Resource does not exist or is not visible to this workspace. |
| `409 Conflict` | Duplicate resource (e.g. duplicate webhook URL). |
| `410 Gone` | Resource was permanently deleted. |
| `422 Unprocessable Entity` | Validation error — see `errors` array. |
| `429 Too Many Requests` | Rate limit exceeded — see [Rate Limits](/api/rate-limits). |
| `500 Internal Server Error` | Server error. Include `request_id` when contacting support. |
| `503 Service Unavailable` | Partial outage (e.g. CRM provider temporarily unreachable). |

---

## Common error codes

| `errors[].code` | When it appears |
|---|---|
| `required` | A required field is missing. |
| `invalid_value` | Field value is not in the allowed set. |
| `invalid_combination` | Two field values are mutually incompatible. |
| `out_of_range` | Numeric value exceeds allowed bounds. |
| `mfa_required` | MFA code is required to complete login. |
| `email_not_verified` | Email address must be verified before proceeding. |
| `not_found` | Resource was not found (returned in the `detail`, not `errors` array). |
| `conflict` | Concurrent write conflict — safe to retry. |

---

## Idempotency

Mutating endpoints (POST/PATCH/PUT/DELETE) accept an `Idempotency-Key` header. The same key within 24 hours returns the cached response without re-executing the operation.

```bash
curl -X POST https://api.civicsignals.io/api/v1/signals/01912abc-.../push \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"connection_id": "...", "target": "salesforce.opportunity"}'
```

The `Idempotency-Key` header is **required** for `POST /signals/{id}/push`.

---

## Async jobs

Operations that may take longer than 1 second return `202 Accepted` with a job descriptor:

```json
{
  "job_id": "job_01J...",
  "status_url": "/api/v1/jobs/job_01J..."
}
```

Poll `GET /jobs/{id}` until the job reaches a terminal state:

| Status | Terminal? |
|---|---|
| `queued` | No |
| `running` | No |
| `succeeded` | Yes |
| `failed` | Yes |
| `cancelled` | Yes |

Failed jobs include a structured `error` block in the job result. You can also subscribe to a webhook on the event type to avoid polling.
