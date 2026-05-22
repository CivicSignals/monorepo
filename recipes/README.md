# recipes/

Declarative, schema-validated **recipes** — the per-tenant/per-source
instantiations of connectors (doc 16 §17.4, doc 18 §3). Connectors are code we
write (`apps/api` ingestion module); recipes are config that community
contributors can submit via PR (no code, no marketplace UI in MVP).

## Layout

```
recipes/
  <recipe_id>/
    recipe.yml          # validated against @civicsignals/recipe-schema
    fixtures/           # 1–5 sample inputs (HTML/JSON/PDF) — doc 18 §3.3
      <case>.html
      <case>.expected.json
```

## CI contract (TODO A3, D5, QA-4)

On every PR, each recipe is validated against the JSON Schema and run against
its golden fixtures; output is compared to the committed `*.expected.json`.
Adding a fixture is how you fix "the recipe missed this case in production".

The 200-recipe Tier-1 sprint (TODO D12) populates this directory.
