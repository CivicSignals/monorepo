---
id: pagination
title: Pagination
sidebar_label: Pagination
slug: /api/pagination
---

# Pagination

All list endpoints in the CivicSignals API use **cursor-based pagination**. Offset pagination is not supported — the dataset is too large and too actively updated for offset to produce reliable results.

---

## Request parameters

| Parameter | Type | Default | Max | Description |
|---|---|---|---|---|
| `cursor` | string | `null` | — | Opaque cursor from the previous page's `next_cursor`. Omit for the first page. |
| `limit` | integer | `25` | `100` | Number of records per page. |

Example first-page request:

```bash
GET /api/v1/signals?limit=25
```

Example subsequent-page request:

```bash
GET /api/v1/signals?limit=25&cursor=eyJpZCI6IjAxOTBmM2MyLi4uIn0
```

---

## Response envelope

Every list response wraps results in a `data` array and includes a `page` object:

```json
{
  "data": [
    { "id": "0190f3c2-...", "..." : "..." },
    "..."
  ],
  "page": {
    "next_cursor": "eyJpZCI6IjAxOTBmM2QwLi4uIn0",
    "has_more": true,
    "limit": 25
  }
}
```

When `has_more` is `false`, `next_cursor` is `null` and you have reached the end of the result set.

---

## Walking pages

```bash
# Page 1 — no cursor
CURSOR=""

while true; do
  URL="https://api.civicsignals.io/api/v1/signals?limit=100${CURSOR:+&cursor=$CURSOR}"
  RESPONSE=$(curl -s "$URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Workspace-Id: $WORKSPACE")

  echo "$RESPONSE" | jq '.data[].title'

  HAS_MORE=$(echo "$RESPONSE" | jq -r '.page.has_more')
  CURSOR=$(echo "$RESPONSE" | jq -r '.page.next_cursor')

  if [ "$HAS_MORE" = "false" ]; then
    break
  fi
done
```

---

## Python SDK example

```python
from civicsignals import CivicSignals

client = CivicSignals(token="cs_live_...")

# Automatically walks pages
for signal in client.signals.iter(entity_kind="k12_district", state="WA"):
    print(signal.title)
```

---

## TypeScript SDK example

```typescript
import { CivicSignals } from "@civicsignals/sdk";

const client = new CivicSignals({ token: "cs_live_..." });

// Async iterator — walks pages automatically
for await (const signal of client.signals.iter({ entity_kind: "k12_district", state: "WA" })) {
  console.log(signal.title);
}
```

---

## Sorting

List endpoints accept a `sort` parameter:

```
GET /api/v1/signals?sort=score:desc
GET /api/v1/signals?sort=published_at:desc   (default)
```

Available sort fields vary per endpoint and are documented in the [API Reference](pathname:///api-reference.html).

---

## Filtering

Equality filters can be repeated (treated as `IN`):

```
GET /api/v1/signals?signal_type=rfp_posted&signal_type=budget_drafted
```

Range filters use `_gte` / `_lt` suffixes:

```
GET /api/v1/signals?published_at_gte=2026-05-01&published_at_lt=2026-06-01
```

Full-text search:

```
GET /api/v1/signals?q=math+curriculum
```

Unknown filter parameters are rejected with `400 Bad Request` rather than silently ignored.
