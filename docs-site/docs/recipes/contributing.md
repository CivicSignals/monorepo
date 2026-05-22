---
id: contributing
title: Contributing a Recipe
sidebar_label: Contributing a recipe
slug: /recipes/contributing
---

# Contributing a Recipe

Recipes are contributed as YAML pull requests with golden fixtures. No code is required — just a valid `recipe.yml` and at least one fixture pair.

This page covers the PR process, the DCO sign-off requirement, and the review checklist.

## Before you start

1. Read the [Overview](./intro.md) to understand what a recipe is and how the lifecycle works.
2. Check whether a recipe for your source already exists under `recipes/` in the monorepo. If it does but is broken or outdated, file an issue rather than duplicating it.
3. Check [Connectors](./connectors.md) to confirm a connector exists for your source type. If not, open an issue — connectors are code-reviewed additions, not YAML.
4. Read [Politeness and legal rules](./politeness.md). PRs that violate the legal/politeness rules are rejected.

## Setup

Fork the repository (or create a branch if you have write access):

```bash
git clone https://github.com/CivicSignals/monorepo.git
cd monorepo
pnpm install
cd apps/api && uv sync
```

## Step-by-step authoring flow

### 1. Scaffold a starter recipe

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli scaffold my-new-source
```

This creates `recipes/my-new-source/recipe.yml` and an empty `fixtures/` directory.

### 2. Edit the recipe YAML

Open `recipes/my-new-source/recipe.yml` and fill in:

- `entity` — the organization this recipe covers (name, state, kind, and `entity_id` if available)
- `connector` — verify it matches the source type (see [Connectors](./connectors.md))
- `connector_config` — connector-specific settings (timeout, pagination, auth)
- `schedule.cron` — a reasonable cadence (not more frequent than the source updates)
- `fields` — CSS/XPath selectors for each field you want to extract, with fallbacks
- `signal_types` — what signals this recipe produces

See [Schema reference](./schema.md) and [Selectors and fallbacks](./selectors.md) for detailed field documentation.

### 3. Capture sample HTML

Save a sample page from the source:

```bash
curl -A "CivicSignalsBot/1.0; +https://civicsignals.io/bot" \
     -o recipes/my-new-source/fixtures/listing-001.html \
     "https://www.example.gov/bids/listing"
```

Or use your browser: visit the page and save the HTML (File → Save As → Webpage, HTML Only).

Add a second fixture if the source has multiple page templates:

```bash
curl -A "CivicSignalsBot/1.0; +https://civicsignals.io/bot" \
     -o recipes/my-new-source/fixtures/listing-002.html \
     "https://www.example.gov/bids/listing?type=rfp"
```

### 4. Verify selectors with `preview`

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe my-new-source \
  --html recipes/my-new-source/fixtures/listing-001.html
```

Iterate on the selectors in `recipe.yml` until all required fields match with `extraction_method: primary`.

### 5. Generate expected JSON

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe my-new-source \
  --html recipes/my-new-source/fixtures/listing-001.html \
  --json > recipes/my-new-source/fixtures/listing-001.expected.json
```

Repeat for each fixture HTML file.

### 6. Validate and test

```bash
# Schema validation:
uv run python -m civicsignals_api.modules.recipes.cli validate recipes/my-new-source/recipe.yml

# Fixture replay (the CI gate):
uv run python -m civicsignals_api.modules.recipes.cli test my-new-source
```

Both must pass before opening a PR.

### 7. Open a PR

Stage and commit your files with a DCO sign-off (see below):

```bash
git add recipes/my-new-source/
git commit -s -m "feat(recipes): add my-new-source recipe (rfp_posted)"
```

Push and open a pull request. Use the PR template:

- **What source does this cover?** (name, URL, entity type)
- **Which connector does it use?** (and why)
- **What signal types does it produce?**
- **How did you verify it?** (browser inspection, `preview` output, `test` output)
- **Politeness checklist** (see below)

## DCO sign-off (required)

CivicSignals uses the **Developer Certificate of Origin** instead of a CLA. Every commit you submit must be signed off. The `-s` flag in `git commit` appends the required `Signed-off-by` trailer:

```bash
git commit -s -m "feat(recipes): add wa-state-webs recipe"
```

This appends:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match your Git author identity. **Pull requests whose commits are not signed off cannot be merged.**

If you forget to sign off, you can fix it before the PR merges:

```bash
# Amend the most recent commit:
git commit --amend -s

# Rebase to sign off multiple commits:
git rebase --signoff HEAD~3   # sign off last 3 commits
git push --force-with-lease
```

See [CONTRIBUTING.md](https://github.com/CivicSignals/monorepo/blob/main/CONTRIBUTING.md) for the full DCO text.

## Fixture requirements

The CI gate (task QA-4) runs on every PR. It will fail if:

- Any recipe under `recipes/` lacks a `fixtures/` directory or has no `*.html` + `*.expected.json` pair.
- Any fixture replay produces output that differs from the committed `*.expected.json`.
- Any recipe fails schema validation.

Minimum fixture requirements for a new recipe PR:

- At least **one** fixture pair (`listing-001.html` + `listing-001.expected.json`).
- The fixture must cover the primary path — the HTML that the recipe's primary selectors should match.
- If your recipe has fallback selectors, add a second fixture that exercises the fallback path (where the primary selector fails and a fallback takes over), with `"degraded": true` in the expected JSON.

## Politeness PR checklist

Before submitting, confirm:

- [ ] `fetch.respect_robots_txt` is `true` (or omitted — the default is `true`)
- [ ] `fetch.politeness_seconds` is ≥ 10
- [ ] `schedule.cron` is not more frequent than the source realistically updates
- [ ] No API keys or secrets are committed in plaintext — use `env:NAME` references
- [ ] The recipe does not access login-gated content
- [ ] The source's `robots.txt` permits our bot user agent on the path being fetched
- [ ] The fixture HTML does not contain PII

## Review process

A maintainer reviews recipe PRs for:

1. Schema validity and field correctness
2. Connector choice (is there a better fit?)
3. Selector quality (do they look stable? are fallbacks provided?)
4. Politeness compliance
5. Fixture completeness (do the fixtures cover the important cases?)
6. Legal/ToS posture of the source

Most recipe PRs are straightforward. If your selectors are clean, your fixtures pass, and your politeness settings are correct, review is typically fast.

## After your recipe is merged

Your recipe will be scheduled and begin producing signals on the next run. You can track its health via the admin dashboard (for self-hosters) or the recipe status API.

If the source changes its HTML structure later, the recipe's degraded rate will rise and a drift alert will be filed as a GitHub issue. You (or another community member) can then update the selectors and open a new PR with bumped `version`.
