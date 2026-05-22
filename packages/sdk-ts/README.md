# @civicsignals/sdk-ts

TypeScript client for the CivicSignals REST API, consumed by `apps/web` and
external integrators. Endpoint types are generated from the live OpenAPI doc:

```bash
pnpm --filter @civicsignals/sdk-ts generate   # api must be running on :8000
```

REST conventions (doc 06 §5): `/api/v1`, bearer auth, cursor pagination,
RFC 7807 `application/problem+json` errors.
