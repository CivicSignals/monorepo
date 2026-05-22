---
id: workspace-scoping
title: Workspace Scoping
sidebar_label: Workspace Scoping
slug: /api/workspace-scoping
---

# Workspace Scoping

Most CivicSignals resources are **workspace-scoped** — signals scored for your ICP, pipeline items, saved searches, FOIA requests, and integrations all belong to one workspace. The API needs to know which workspace to use for every request.

---

## How the workspace is resolved

The workspace is resolved from the request in this order:

1. **Workspace API token** — the token itself is bound to a single workspace; no header needed.
2. **`X-Workspace-Id` header** — use with Bearer JWTs or personal access tokens (PATs).
3. **Fallback** — if `X-Workspace-Id` is absent on a session/PAT-authenticated request, the API uses the user's `last_active_workspace_id`.

```http
GET /api/v1/signals HTTP/1.1
Authorization: Bearer eyJ...
X-Workspace-Id: 0190f3c2-7b8d-7c84-9c1a-2f6e8d4b1a01
```

---

## Which endpoints require it

| Endpoint group | Workspace-scoped? | Notes |
|---|---|---|
| `GET /entities`, `GET /entities/{id}` | No | Entity directory is global |
| `GET /entities/{id}/contacts` | No | Contacts are global per entity |
| `GET /contacts`, `GET /contacts/{id}` | No | Global read |
| `POST /contacts/{id}/report-invalid` | **Yes** | Records which workspace reported it |
| `GET /signals` | No | Signal corpus is global; workspace affects score |
| `GET /signals/{id}` | No | Global signal record |
| `POST /signals/{id}/feedback` | **Yes** | Per-workspace signal feedback |
| `POST /signals/{id}/dismiss` | **Yes** | Per-workspace dismissal |
| `POST /signals/{id}/push` | **Yes** | Uses workspace integration connection |
| `GET /icp` | **Yes** | ICP is workspace-specific |
| `GET /pipeline/*` | **Yes** | Pipeline is workspace-specific |
| `GET /foia/templates` | No | Global reference data |
| `GET /foia/requests/*` | **Yes** | FOIA requests are workspace-specific |
| `GET /billing/*` | **Yes** | Per-workspace subscription |
| `GET /admin/audit-events` | **Yes** | Per-workspace audit log |
| `GET /workspaces` | No | Lists the calling user's workspaces |
| `GET /workspaces/{id}/members` | **Yes** | Per-workspace membership |

---

## curl examples

**With `X-Workspace-Id` (PAT or JWT):**

```bash
curl -s https://api.civicsignals.io/api/v1/icp \
  -H "Authorization: Bearer $PAT_TOKEN" \
  -H "X-Workspace-Id: $WORKSPACE_ID"
```

**With a workspace API token (no header needed):**

```bash
curl -s https://api.civicsignals.io/api/v1/icp \
  -H "Authorization: Bearer $API_TOKEN"
```

---

## Error responses

If a workspace-scoped endpoint is called without valid workspace context:

```http
HTTP/1.1 400 Bad Request
Content-Type: application/problem+json

{
  "type": "https://docs.civicsignals.io/errors/missing-workspace",
  "title": "Workspace required",
  "status": 400,
  "detail": "X-Workspace-Id header is required for this endpoint."
}
```

If the token has access to the workspace but the resource was not found (or belongs to a different workspace):

```http
HTTP/1.1 404 Not Found
Content-Type: application/problem+json

{
  "type": "about:blank",
  "title": "Not found",
  "status": 404,
  "detail": "Resource not found (or not accessible from this workspace)."
}
```

---

## Multi-workspace workflows

If your integration needs to operate across multiple workspaces (e.g., an agency managing several clients), use a PAT or JWT and supply the appropriate `X-Workspace-Id` header per request. Alternatively, generate one workspace API token per workspace.
