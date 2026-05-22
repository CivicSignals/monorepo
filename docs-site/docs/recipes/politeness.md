---
id: politeness
title: Politeness and Legal Rules
sidebar_label: Politeness & legal
slug: /recipes/politeness
---

# Politeness and Legal Rules

CivicSignals ingests public data in a way that is legal, transparent, and respectful of the sources we depend on. Every recipe inherits these rules; some can be configured upward (more polite), but none can be turned off. PRs that violate these rules are rejected in review.

## The non-negotiables

### 1. `robots.txt` is always honored

Every recipe runs `robots.txt` checking before fetching any URL. If the source's `robots.txt` disallows our user agent from the path we want to fetch, we do not fetch it.

The `fetch.respect_robots_txt` field exists for auditability — it is always `true`. Setting it to `false` is technically valid in the schema but will cause your PR to be rejected:

```yaml
# Correct (the default; you may omit this block entirely):
fetch:
  respect_robots_txt: true

# Will be rejected in PR review — do not submit:
fetch:
  respect_robots_txt: false   # rejected
```

If a source's `robots.txt` blocks our access, the options are: (a) skip the source, (b) seek API access or a formal partnership, or (c) FOIA the data. We do not bypass `robots.txt`.

### 2. No CAPTCHA bypass, ever

If a source presents a CAPTCHA or anti-bot challenge (Cloudflare, Akamai, hCaptcha, etc.), we stop and flag the recipe for human review. We do not attempt to solve CAPTCHAs or route through residential proxies to evade detection.

This is non-negotiable. The legal risk (DMCA 1201 post-2025) and the ethical risk of automated bypasses are both unacceptable.

If your source is protected by anti-bot systems, open an issue describing the source. Do not submit a recipe that attempts to work around the protection.

### 3. Real, identified user agent

All requests identify our bot with:

```
CivicSignalsBot/1.0; +https://civicsignals.io/bot
```

The platform applies this user agent by default. You can override it to be more specific (for example, to identify a particular connector or use case), but you may not use a browser impersonation string:

```yaml
# Acceptable custom user agent:
fetch:
  user_agent: "CivicSignalsBot/1.0 (wa-state-webs recipe); +https://civicsignals.io/bot"

# Not acceptable — impersonates a browser:
fetch:
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
```

### 4. No login-gated content

Recipes ingest public-side data only. If the content you want is behind a login wall (even a free vendor registration), we skip it and use the public-side equivalent instead.

The only exception is API-key-authenticated REST APIs on sources where the key is freely available (Grants.gov, some state APIs). These are not "login-gated" in the same sense — they are authenticated public APIs. Use `env:` references for secrets so keys are never committed in YAML.

## Configurable politeness settings

These can be tuned in the `fetch` block — upward from the defaults, never below them.

### `politeness_seconds` (default: 10)

Minimum delay between requests to the same host. The default is 10 seconds, which is the platform minimum.

You may increase this for sources that appear rate-sensitive or whose `robots.txt` suggests a crawl delay:

```yaml
fetch:
  politeness_seconds: 30   # more conservative; fine for infrequent sources
```

Do not set below 10.

### `jitter_seconds` (default: 0)

Random extra delay (0 to `jitter_seconds`) added before each request, in addition to `politeness_seconds`. Prevents lockstep request patterns when many recipes run simultaneously:

```yaml
fetch:
  politeness_seconds: 10
  jitter_seconds: 5   # each request waits 10–15 seconds
```

### `max_redirects` (default: 5)

Cap on the number of redirects to follow. If the source redirects more than this, the fetch fails visibly (not silently into a loop):

```yaml
fetch:
  max_redirects: 3   # more conservative; fine for most sources
```

### Request scheduling via `schedule.cron`

Space out your recipe runs reasonably. State portals, board meeting pages, and similar sources typically update at most once or twice a day. An hourly schedule is usually fine; per-minute schedules are not appropriate unless the source genuinely updates in near-real-time.

```yaml
schedule:
  cron: "0 */2 * * *"   # every 2 hours — appropriate for a state portal
  cron: "0 8 * * *"     # once a day at 8 AM — appropriate for document sources
  cron: "*/5 * * * *"   # every 5 minutes — only if the source is real-time (e.g., live auction)
```

## Source-specific legal notes

Some sources have specific legal considerations. Document these in the recipe's comments or in a `legal_notes` field on the connector descriptor (for platform connectors).

Common patterns:

**Public records (state/local meeting minutes, procurement portals):**
These are public records. Scraping public-facing pages is generally permissible. No additional notes needed in most cases.

**RSS feeds:**
RSS is explicitly designed for programmatic consumption. No additional notes needed.

**Federal APIs (Grants.gov, USAspending, SAM.gov):**
Public domain data. Free API access. API key may be required; use `env:` references.

**Commercial platforms hosting public data (BoardDocs, Granicus, etc.):**
The data is public records hosted on a private platform. We scrape public-facing pages only — not login-gated content, not vendor-registration-required sections.

**Sources with explicit ToS restrictions:**
If the source's ToS explicitly prohibits automated access to their public pages, document this in the recipe's comments and open an issue for discussion before submitting.

## What to do if a source blocks you

During development: if a source returns a CAPTCHA page, an anti-bot challenge, or a 403 Forbidden:

1. Check `robots.txt` — if it disallows our path, stop.
2. Check whether the source has a public API — prefer the API.
3. Check whether the data is available via a bulk download — prefer that.
4. Open an issue describing the source and its blocking behavior.
5. Do **not** attempt to bypass the block.

In production: blocked recipes are auto-paused when their fetch success rate drops below threshold. The platform posts a GitHub issue with sample failing input and notifies the on-call channel. The recipe stays paused until a human reviews and either fixes the underlying issue or marks the source as inaccessible.

## Summary

| Rule | Configurable? |
|---|---|
| Honor `robots.txt` | No — always on |
| No CAPTCHA bypass | No — never |
| Identified user agent | Partially — can specify a more descriptive bot UA, not a browser UA |
| No login-gated content | No |
| `politeness_seconds` ≥ 10 | Upward only |
| `jitter_seconds` | Optional, any non-negative value |
| `max_redirects` | Adjustable (default 5) |
| Schedule cadence | Proportional to source update frequency |
