---
id: authentication
title: Authentication
sidebar_label: Authentication
slug: /api/authentication
---

# Authentication

CivicSignals uses three authentication modes. All three issue tokens that are sent as `Authorization: Bearer <token>` (or via a session cookie for web-app sessions).

---

## Authentication modes

| Mode | Token prefix | Issued by | Scope |
|---|---|---|---|
| **Bearer JWT** | `eyJ...` | `POST /auth/login` | Short-lived (15 min); refreshed by the session cookie flow |
| **Workspace API token** | `cs_live_<random32>` (cloud) / `cs_test_<random32>` (test) | Workspace Settings → API Tokens | One workspace; configurable permission scopes |
| **Personal access token (PAT)** | `cs_pat_<random32>` | User Settings → Personal tokens | User's full workspace membership; read-only by default |
| **Session cookie** | `cs_session=<jwt>` (HttpOnly, SameSite=Lax) | `POST /auth/login` | Web-app only; automatically managed by the browser |

---

## Sign up

```bash
POST /api/v1/auth/signup
Content-Type: application/json

{
  "email": "you@example.com",
  "password": "at-least-8-chars",
  "name": "Your Name"
}
```

After signup, verify your email address via the link sent to `email`. The response returns the new user record.

---

## Log in

```bash
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "you@example.com",
  "password": "your-password"
}
```

**Response `200 OK`:**

```json
{
  "user": {
    "id": "0190f3c2-7b8d-7c84-9c1a-2f6e8d4b1a01",
    "email": "you@example.com",
    "name": "Your Name"
  },
  "access_token": "eyJ...",
  "token_type": "bearer",
  "workspaces": [
    { "id": "...", "name": "My Workspace", "role": "admin" }
  ]
}
```

The `access_token` is a short-lived JWT. For web apps the server also sets a `cs_session` HttpOnly cookie. For server-to-server work, prefer [workspace API tokens](#workspace-api-tokens).

**MFA:** If the account has MFA enabled and `mfa_code` is absent, the API returns `401` with `"code": "mfa_required"`. Retry with `"mfa_code": "123456"` in the request body.

---

## Password reset

```bash
# Step 1 — request a reset link (always returns 204, no enumeration)
POST /api/v1/auth/password-reset/request
{ "email": "you@example.com" }

# Step 2 — confirm with the token from the email
POST /api/v1/auth/password-reset/confirm
{ "token": "<from-email>", "new_password": "new-password" }
```

---

## Workspace API tokens

Workspace API tokens are the recommended auth method for server-to-server integrations. They are:

- Tied to **one workspace** — no `X-Workspace-Id` header required.
- Scoped to explicit permissions.
- Long-lived (optionally, with an `expires_at`).
- Auditable (`last_used_at`, `created_by_user_id`).

### Create a workspace API token

```bash
POST /api/v1/workspaces/{workspace_id}/api-tokens
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "name": "Salesforce push",
  "scopes": ["signals:read", "pipeline:write"],
  "expires_at": "2027-01-01T00:00:00Z"
}
```

**Response `201 Created`:**

```json
{
  "id": "...",
  "name": "Salesforce push",
  "token": "cs_live_abc123...",
  "scopes": ["signals:read", "pipeline:write"],
  "expires_at": "2027-01-01T00:00:00Z",
  "created_at": "2026-05-16T10:00:00Z"
}
```

The `token` value is **shown once** at creation. Store it immediately — it cannot be retrieved again.

### Available scopes

| Scope | Access |
|---|---|
| `signals:read` | Read signals, saved searches, feed |
| `signals:write` | Dismiss, pin, provide feedback on signals |
| `contacts:read` | Read contacts directory |
| `contacts:write` | Report invalid contacts |
| `pipeline:read` | Read pipeline stages and items |
| `pipeline:write` | Create/update pipeline items |
| `foia:read` | Read FOIA requests and templates |
| `foia:write` | Create and transition FOIA requests |
| `webhooks:manage` | Create, update, delete webhook subscriptions |
| `admin:read` | Read audit log (admin role required) |

### List and revoke tokens

```bash
# List tokens for a workspace
GET /api/v1/workspaces/{workspace_id}/api-tokens

# Revoke a token
DELETE /api/v1/workspaces/{workspace_id}/api-tokens/{token_id}
```

---

## Personal access tokens (PATs)

PATs are attached to your user account and span all your workspace memberships. They are useful for CLI tooling and scripts.

- Prefix: `cs_pat_<random32>`
- Read-only by default; scopes can be elevated for specific needs.
- Created and revoked in **User Settings → Personal Tokens**.

Use exactly like a workspace API token:

```bash
curl -H "Authorization: Bearer cs_pat_abc123..." \
     -H "X-Workspace-Id: <workspace_id>" \
     https://api.civicsignals.io/api/v1/signals
```

---

## Errors

| Status | Meaning |
|---|---|
| `401 Unauthorized` | No token, expired token, or invalid credentials. |
| `403 Forbidden` | Valid token but insufficient scope or role. |

Both follow the RFC 7807 `application/problem+json` format — see [Errors](/api/errors).
