---
id: intro
title: Getting Started
sidebar_label: Getting Started
slug: /api/intro
---

# Getting Started with the CivicSignals API

CivicSignals exposes a fully documented REST API at `https://api.civicsignals.io/api/v1`. The frontend and the public API consume the **same** endpoints — anything the web app does, an integration can do with a token.

An interactive **[API Reference](pathname:///api-reference.html)** is available (rendered from the live OpenAPI spec).

---

## Quick start

### 1. Sign up and get a token

Create an account and log in to receive a session JWT, or generate an API token from your workspace settings.

```bash
# Log in — sets the cs_session cookie and returns a bearer token pair
curl -s -X POST https://api.civicsignals.io/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "your-password"}' | jq .
```

Response:

```json
{
  "user": { "id": "...", "email": "you@example.com", "name": "Your Name" },
  "access_token": "eyJ...",
  "token_type": "bearer",
  "workspaces": [
    { "id": "01912abc-...", "name": "My Workspace", "role": "admin" }
  ]
}
```

Use the `access_token` as a Bearer token in subsequent requests.

### 2. Make your first call

List signals filtered to Washington K-12 districts, sorted by score:

```bash
export TOKEN="eyJ..."
export WORKSPACE="01912abc-..."  # your workspace ID from login response

curl -s "https://api.civicsignals.io/api/v1/signals?entity_kind=k12_district&state=WA&sort=score:desc&limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Workspace-Id: $WORKSPACE" | jq .data[].title
```

### 3. Use an API token for server-side calls

For long-lived integrations, generate a workspace API token (go to **Settings → API Tokens** in the web app, or call `POST /api/v1/workspaces/{id}/api-tokens`). API tokens are tied to a single workspace, so you do not need to send `X-Workspace-Id` when using them.

```bash
curl -s "https://api.civicsignals.io/api/v1/signals?limit=25" \
  -H "Authorization: Bearer cs_live_abc123..."
```

---

## Base URL

| Environment | Base URL |
|---|---|
| Cloud | `https://api.civicsignals.io/api/v1` |
| Self-host | `https://<your-host>/api/v1` |

---

## Conventions at a glance

| Convention | Detail |
|---|---|
| Auth | `Authorization: Bearer <token>` or `cs_session` cookie |
| Workspace scoping | `X-Workspace-Id: <uuid>` header (see [Workspace Scoping](/api/workspace-scoping)) |
| Pagination | Cursor-based `?cursor=…&limit=25` (see [Pagination](/api/pagination)) |
| Error format | RFC 7807 `application/problem+json` (see [Errors](/api/errors)) |
| Timestamps | ISO 8601 UTC with `Z` suffix: `2026-05-16T10:42:00Z` |
| IDs | UUID v7 strings |
| Field names | `snake_case` |
| Money | Integer cents USD, e.g. `amount_cents: 4900000` = $49,000.00 |

---

## Interactive explorer

- **Full API Reference** (Redoc): [/api-reference.html](pathname:///api-reference.html)
- **Swagger UI** (when running locally): `http://localhost:8000/docs`
- **OpenAPI JSON**: `/api/v1/openapi.json` (live) or [`/openapi.json`](/openapi.json) (captured snapshot)

---

## SDKs

Official SDKs wrap the OpenAPI spec and are hand-tightened for ergonomics:

| SDK | Package | Source |
|---|---|---|
| TypeScript | `@civicsignals/sdk` | `packages/sdk-ts/` |
| Python | `civicsignals` | `packages/sdk-py/` |

Install the TypeScript SDK:

```bash
npm install @civicsignals/sdk
```

Install the Python SDK:

```bash
pip install civicsignals
```

To regenerate the TS SDK from a live API server (when the API is running locally):

```bash
pnpm --filter @civicsignals/sdk-ts generate
```

Community SDKs in any language are welcome — the OpenAPI spec at `/api/v1/openapi.json` is the contract.

---

## Guides

- [Authentication](/api/authentication) — bearer JWTs, workspace API tokens, personal access tokens
- [Workspace Scoping](/api/workspace-scoping) — the `X-Workspace-Id` header and when to use it
- [Pagination](/api/pagination) — cursor-based pagination for list endpoints
- [Errors](/api/errors) — RFC 7807 error format and status code reference
- [Rate Limits](/api/rate-limits) — per-plan rate limits and the `429` response
