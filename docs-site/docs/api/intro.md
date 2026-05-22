---
id: intro
title: API Overview
sidebar_label: Overview
slug: /api/intro
---

# API Reference

CivicSignals exposes a versioned REST API at `https://api.civicsignals.io/api/v1`. All endpoints are documented via OpenAPI and available at `/api/v1/openapi.json` when the server is running.

<!-- TODO Q3: Replace this stub with generated API documentation + hand-written guides. See task Q3 in TODO.md.
  Planned content:
  - Authentication (bearer tokens, API token management)
  - Workspace scoping (X-Workspace-Id header)
  - Cursor pagination (?cursor=…&limit=25)
  - Error format (RFC 7807 application/problem+json)
  - Endpoint reference (auto-generated from OpenAPI spec)
  - Code examples (Python SDK, TypeScript SDK, raw curl)
-->

## Conventions

All API responses follow these conventions (full documentation coming in **Q3 — API docs**):

| Convention | Detail |
|---|---|
| Base URL | `https://api.civicsignals.io/api/v1` |
| Auth | `Authorization: Bearer <token>` |
| Workspace scoping | `X-Workspace-Id: <workspace_id>` header |
| Pagination | Cursor-based — `?cursor=…&limit=25` |
| Error format | RFC 7807 `application/problem+json` |

## SDKs

- **TypeScript SDK** — `@civicsignals/sdk-ts` (see `packages/sdk-ts/`)
- **Python SDK** — `civicsignals-sdk` (see `packages/sdk-py/`)

## Interactive explorer

When running locally, the interactive API explorer is available at:

```
http://localhost:8000/docs
```

Full reference documentation will be generated from the live OpenAPI spec as part of **Q3**.
