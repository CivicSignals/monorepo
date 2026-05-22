# Prompts

Versioned LLM prompts (doc 06 §7, doc 18 §3.5, doc 19 §5.3). A caller references
a prompt by name + version through the LLM gateway; a bad prompt is rolled back by
changing the version pointer, with no code deploy. Loaded and validated by the
`civicsignals_api.prompt_registry.PromptRegistry` (TODO E3).

## Layout

```
prompts/
  <name>/            # name may nest, e.g. signal_type/rfp_posted
    v1.md
    v2.md
```

`<name>` is the directory path relative to this folder; the version is the file
name `v<N>.md` (N a positive integer; the integer drives the "latest" resolver,
so `v10` beats `v2`). Files that are not `v<N>.md` (this README, etc.) are
ignored by the loader.

## File format — Markdown with YAML frontmatter

Each `v<N>.md` is a YAML frontmatter block delimited by `---` lines, followed by
the template body:

```markdown
---
description: One-line summary of what this prompt does.
task: classify            # logical gateway task -> model routing (optional)
model_hint: anthropic:claude-3-5-haiku-latest   # advisory provider:model (optional)
variables: [cleaned_text] # declared template variables (optional but recommended)
system: >                 # optional system prompt (also templated)
  You are a classifier ...
---
Body template with {variable} placeholders.
```

Frontmatter keys (all optional except an implicit body): `description`, `task`,
`model_hint`, `system`, `variables`. Unknown keys fail validation at load time so
typos are caught early.

## Templating

The body and `system` use simple `{variable}` placeholders. Substitution is
strict: every placeholder must be supplied at render time (missing → error) and
only plain `{name}` fields are allowed (no `{}`, `{0}`, `{a.b}`, or format specs).
Literal braces must be doubled: `{{` and `}}`.

If `variables:` is declared, the template may not reference any variable not in
that list — keeping the declared contract and the body in sync.

## Model routing

`task` and `model_hint` are advisory. The gateway uses them only when the caller
left `task` at its default and gave no explicit `provider`/`model`; an explicit
caller choice (e.g. doc 19 §5.1 Haiku→Sonnet escalation) always wins. Per doc 19,
classify/query-rewrite prompts hint Haiku; extraction prompts hint Sonnet.

## Starter prompts

| Name | Task | Model hint | Used by |
|---|---|---|---|
| `relevance_classifier/v1` | classify | Haiku | E8 (stage 2 relevance gate) |
| `entity_extraction/v1` | extraction | Sonnet | E11 (stage 3 LLM-assisted) |
| `smart_search_rewrite/v1` | classify | Haiku | I2 (NL → structured query) |
```
